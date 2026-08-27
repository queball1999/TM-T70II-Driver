# Development and validation on Windows (WSL2)

How to build, test and validate this driver from a Windows workstation using
WSL2 — including printing to a real TM-T70II over USB by forwarding the device
into WSL with `usbipd-win`, and capturing the Windows APD byte stream to compare
against.

For installing on a real Linux host, see [INSTALLATION.md](INSTALLATION.md).

**Time:** about 20 minutes for the first setup; a few minutes per validation run
after that.

---

## 0. What runs where

```
Windows                              WSL2 (Ubuntu)
--------                             -------------
usbipd-win  ── attach 04b8:0202 ──▶  /dev/bus/usb/...  ─▶ cupsd ─▶ rastertoescpos ─▶ tmt70 ─▶ printer
win-capture.ps1 (APD reference)      tools/escpos_dis.py  (compare the two streams)
repo on P:\ ── /mnt/p/... ──────────▶ build via tools/build.sh into ~/build (ext4)
```

Two rules that save time later:

* **Never build on `/mnt/*`.** DrvFs reports every file as `0777`, which makes
  debhelper try to execute `debian/*.docs`, and it is slow. `tools/build.sh`
  already rsyncs to `~/build` and fixes the modes — use it rather than calling
  `dpkg-buildpackage` in place.
* **The USB device belongs to one OS at a time.** While it is attached to WSL,
  Windows (and the Epson APD queue) cannot see it, and vice versa.

---

## 1. WSL2 with systemd

```powershell
wsl --install -d Ubuntu       # skip if WSL is already set up
wsl --update                  # need a 5.10.60.1+ kernel for USB/IP
wsl --version                 # confirm WSL 2 and the kernel version
```

Enable systemd so `systemctl` manages CUPS the way it will in production:

```bash
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
```

Then from PowerShell: `wsl --shutdown`, and reopen the shell. Verify with
`systemctl is-system-running` (`running` or `degraded` are both fine).

Without systemd, use `sudo service cups start` everywhere this guide says
`systemctl start cups`.

---

## 2. Toolchain inside WSL

```bash
sudo apt update
sudo apt install -y build-essential debhelper dpkg-dev fakeroot rsync lintian \
                    python3 cups cups-client cups-filters ghostscript \
                    poppler-utils libusb-1.0-0 shellcheck xxd usbutils
sudo systemctl enable --now cups
sudo usermod -aG lp,lpadmin "$USER"    # lpadmin lets you run lpadmin without sudo
```

Log out and back in (`exit`, then reopen WSL) for the groups to apply.

---

## 3. Get the source into WSL

The repo lives on a Windows drive, so it is visible at `/mnt/p/...`. That path
has a space in it — quote it:

```bash
cd "/mnt/p/Coding/Epson Driver"
```

Editing from Windows and building from WSL works fine because `build.sh` copies
the tree out to ext4 first. If you would rather work entirely in WSL (faster
git, correct file modes), clone into the Linux filesystem instead:

```bash
git clone "/mnt/p/Coding/Epson Driver" ~/epson-tmt70ii-escpos
cd ~/epson-tmt70ii-escpos
```

---

## 4. Validate without a printer

This is the loop to run on every change. Nothing here needs hardware.

```bash
make selftest     # raster -> filter -> ESC/POS, with byte-level assertions
make ppdtest      # cupstestppd on the PPD
make lint         # compile + ppdtest + shellcheck
```

`make selftest` must end with `ALL SELFTESTS PASSED`. It builds a 512x700 test
bitmap, pushes it through `mkraster.py` and the filter under five different
option sets, and asserts on the emitted commands (`GS ( L` graphics, `GS V`
cut style, `GS ( K` density, `GS L` margin, and that blank reduction actually
shrinks the stream).

Inspect one option combination by hand:

```bash
python3 tools/mkraster.py page.pbm \
  | PPD= ./src/filter/rastertoescpos 1 me title 1 "Density=dark BlankReduction=all" \
  | python3 tools/escpos_dis.py -
```

Build and lint the package:

```bash
make deb          # or: ./tools/build.sh
make deb-lint     # build + lintian
```

---

## 5. Install into WSL and create a dry-run queue

You can exercise the whole CUPS pipeline in WSL with no printer at all, by
pointing the queue at a file sink:

```bash
sudo apt install ./epson-tmt70ii-escpos_1.0.0_all.deb    # or: sudo make install

sudo lpadmin -p TMT70FILE -E -v file:///tmp/tmt70-out.bin \
  -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd
sudo cupsctl FileDevice=Yes        # CUPS refuses file:// URIs otherwise
sudo systemctl restart cups
```

Print into it and disassemble the result:

```bash
lp -d TMT70FILE -o Density=dark -o EndOfJobCut=full /etc/hostname
python3 tools/escpos_dis.py /tmp/tmt70-out.bin
```

`TMT70_DRYRUN=<path>` does the same for a real queue — it copies the exact byte
stream to a file *and* sends it to the printer. `make dryrun
LP_OPTS='-o PageSize=X58mm -o Rotate180=True'` wraps that.

---

## 6. Attach the real printer to WSL (usbipd)

The TM-T70II is a vendor-class device with no `usblp` node, so WSL needs the raw
USB device forwarded to it.

Install `usbipd-win` on Windows (once), in an **Administrator** PowerShell:

```powershell
winget install --interactive --exact dorssel.usbipd-win
```

Then each session — the first two commands need Administrator, `attach` does not:

```powershell
usbipd list
#  BUSID  VID:PID    DEVICE                          STATE
#  2-4    04b8:0202  EPSON TM-T70II                  Not shared

usbipd bind   --busid 2-4          # Administrator; once per device, persists
usbipd attach --wsl --busid 2-4    # each time WSL restarts or the device replugs
```

Confirm inside WSL:

```bash
lsusb | grep -i 04b8               # ID 04b8:0202 Seiko Epson Corp.
lpinfo -v | grep tmt70             # direct tmt70://04b8:0202
```

If `lsusb` sees the device but `lpinfo` does not list `tmt70://`, the udev rule
has not fired (udev under WSL can be slow to start):

```bash
sudo systemctl start systemd-udevd
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=04b8
ls -l /dev/bus/usb/*/*             # want group 'lp', mode 0660
```

Blunt fallback while debugging in WSL only (never on a production host):

```bash
sudo chmod 666 /dev/bus/usb/<bus>/<dev>
```

Release the device back to Windows when you are done:

```powershell
usbipd detach --busid 2-4
```

### usbipd problems

| Symptom | Fix |
|---|---|
| `usbipd: error: WSL kernel ... does not support USB/IP` | `wsl --update`, then `wsl --shutdown` |
| `attach` succeeds but `lsusb` in WSL is empty | run `lsusb` in the *default* distro, or pass `--distribution <name>`; make sure WSL was running before attaching |
| Device disappears after `wsl --shutdown` or a replug | re-run `usbipd attach` — `bind` persists, `attach` does not |
| Windows spooler grabs it back | the Epson APD queue is holding the port; keep it paused while the device is attached to WSL |

---

## 7. End-to-end validation on hardware

With the device attached, this is the same path production takes:

```bash
sudo lpadmin -p TM-T70II -E -v tmt70://04b8:0202 \
  -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd
# or, from a checkout:  make queue

make print-test LP_OPTS='-o Density=dark -o EndOfJobCut=full'
```

Work through the options and check both the paper and the bytes:

```bash
make dryrun LP_OPTS='-o PageSize=X58mm'                 # 58 mm width
make dryrun LP_OPTS='-o Rotate180=True -o LeftMargin=5mm'
make dryrun LP_OPTS='-o BlankReduction=all'
make dryrun LP_OPTS='-o EndOfJobCut=none'
lp -d TM-T70II -n 2 -o EndOfJobCut=full ticket.pdf      # copies
```

Each `dryrun` prints the disassembly of `/tmp/tmt70-dryrun.bin`, so you can check
the emitted command matches the option you set. Cross-check against the option
table in [README.md](README.md) and the encodings in [PROTOCOL.md](PROTOCOL.md).

Watch what CUPS is doing while a job runs:

```bash
sudo cupsctl --debug-logging
sudo tail -f /var/log/cups/error_log      # filter/backend stderr lands here
```

---

## 8. Capturing the Windows APD stream (reference)

To compare this driver against Epson's Advanced Printer Driver v4, capture the
APD output on the Windows side. Nothing prints on paper — the queue is
temporarily repointed at a FILE port.

Detach the device from WSL first (`usbipd detach --busid 2-4`) so Windows owns
it again, then in PowerShell from the repo root:

```powershell
.\tools\win-capture.ps1 -Tag baseline

# change exactly ONE setting in
#   Settings > Printers > EPSON TM-T70II Receipt > Printing preferences
# then capture again with a name for it:
.\tools\win-capture.ps1 -Tag density-dark
```

Captures land in `.\capture\<tag>__<doc>.prn`. Disassemble and diff:

```bash
python3 tools/escpos_dis.py capture/density-dark__blocks.prn
make capture-decode                       # every capture/*.prn
diff <(python3 tools/escpos_dis.py capture/baseline__blocks.prn) \
     <(python3 tools/escpos_dis.py capture/density-dark__blocks.prn)
```

Notes:

* `-Method SwapPort` (the default) captures the *real* queue, so whatever is set
  in Printing preferences is what gets captured. `-Method CloneQueue` is faster
  but only ever captures driver defaults.
* If a run is interrupted and leaves the queue on the file port:
  `.\tools\win-capture.ps1 -RestorePort`.
* `capture/` and `*.prn` are gitignored; `make distclean` deletes them.

The RE finding this already produced: APD sends **no** per-job density or speed
command — those are printer-resident NV settings. This driver emits the
documented `GS ( K` fn49/fn50 per job instead. Details in
[PROTOCOL.md](PROTOCOL.md).

---

## 9. Before you push

```bash
make lint          # compile + cupstestppd + shellcheck
make selftest      # must end in ALL SELFTESTS PASSED
make deb-lint      # package builds clean under lintian
make clean         # drop build products
```

If the change touches the emitted byte stream, also record a `make dryrun`
disassembly before and after, and say in the commit message which ESC/POS
command changed.

---

## WSL gotchas, collected

| Thing | Why | What to do |
|---|---|---|
| `debian/*.docs: Permission denied` / executed as a script | DrvFs shows `0777` on `/mnt/*` | build with `./tools/build.sh` (it rsyncs to `~/build` and fixes modes) |
| `systemctl` says "system has not been booted with systemd" | systemd not enabled | add the `[boot] systemd=true` block from step 1, then `wsl --shutdown` |
| CUPS not running after a WSL restart | no session manager by default | `sudo systemctl enable --now cups` (with systemd) or `sudo service cups start` |
| `lp` hangs, `lpstat -p` says the backend cannot open the device | the device is attached to Windows, not WSL | `usbipd attach --wsl --busid <id>` |
| `file://` queue refuses jobs | CUPS disables file devices by default | `sudo cupsctl FileDevice=Yes && sudo systemctl restart cups` |
| Line-ending noise in git diffs | CRLF from Windows editors | `git config core.autocrlf input` on the Windows side |
| Slow builds, odd `git status` churn | working on `/mnt/p` | clone into `~` and push back, or just accept it for editing and build with `build.sh` |

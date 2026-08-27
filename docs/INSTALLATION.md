# Installation guide (production)

Installing `epson-tmt70ii-escpos` on a real Linux host with a TM-T70II attached.
For development / validation on a Windows workstation see
[DEVELOPMENT.md](DEVELOPMENT.md). For the option and Makefile reference see
[README.md](README.md).

**Time:** about 10 minutes on a prepared host.

---

## 0. Requirements

| | |
|---|---|
| OS | Debian 12+ / Ubuntu 22.04+ (validated on Ubuntu 26.04, CUPS 2.4.16) |
| Packages | `cups`, `cups-daemon`, `libusb-1.0-0`, `udev`, `python3` >= 3.8 — pulled in by the `.deb` |
| Recommended | `cups-filters`, `ghostscript`, `poppler-utils` — needed to print PDFs; without them only raster/text input works |
| Access | `sudo` on the host; the printing account in the `lp` group |
| Hardware | TM-T70II on USB (`04b8:0202`), or on Ethernet via UB-E04 (port 9100) |

Not required: a kernel `usblp` device. The USB interface is vendor-class, so
`/dev/usb/lp0` never appears — the bundled `tmt70` backend uses libusb instead.

---

## 1. Get the package onto the host

Either copy the prebuilt artefact:

```bash
scp epson-tmt70ii-escpos_1.0.0_all.deb prod-host:/tmp/
```

...or build it on the host from a checkout:

```bash
sudo apt install -y build-essential debhelper dpkg-dev fakeroot rsync python3
git clone <repo-url> epson-tmt70ii-escpos && cd epson-tmt70ii-escpos
make selftest        # no printer needed; must end in ALL SELFTESTS PASSED
make deb             # -> ./epson-tmt70ii-escpos_1.0.0_all.deb
```

`make deb` builds in `~/build` and copies the `.deb` back next to the source.

---

## 2. Install

```bash
sudo apt install ./epson-tmt70ii-escpos_1.0.0_all.deb
```

Use `apt install ./file.deb` rather than `dpkg -i`, so the CUPS and libusb
dependencies resolve in one step.

What lands on disk:

| Path | Role |
|---|---|
| `/usr/lib/cups/filter/rastertoescpos` | CUPS raster to ESC/POS; owns all print options |
| `/usr/lib/cups/backend/tmt70` | libusb backend (also `file://`, `socket://`) |
| `/usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd` | model + preferences UI |
| `/usr/lib/udev/rules.d/60-epson-tmt70ii.rules` | `lp`-group access to the device |
| `/usr/share/doc/epson-tmt70ii-escpos/` | this documentation |

The `postinst` reloads the udev rules and reloads CUPS, so no reboot is needed.

---

## 3. Grant device access

```bash
sudo usermod -aG lp "$USER"      # and every account that will print directly
```

Log out and back in (or run `newgrp lp`) for the group to take effect. Confirm:

```bash
id -nG | tr ' ' '\n' | grep -x lp
```

For a service account (a POS daemon, say) add that account instead:

```bash
sudo usermod -aG lp posd
sudo systemctl restart posd
```

Jobs submitted through CUPS run as the `lp` user inside `cupsd`, so a queue works
even when the submitting user is not in `lp`; group membership only matters for
running the backend or the filter by hand.

---

## 4. Verify the printer is seen

Plug in and power on the printer, then:

```bash
lsusb | grep -i 04b8                 # expect: ID 04b8:0202 Seiko Epson Corp.
lpinfo -v | grep tmt70               # expect: direct tmt70://04b8:0202
```

Check the device node is owned by the `lp` group (`crw-rw---- root lp`):

```bash
ls -l /dev/bus/usb/*/*
```

If `lpinfo` shows nothing, re-apply udev:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=04b8
```

---

## 5. Create the queue

USB, auto-detected URI:

```bash
sudo lpadmin -p TM-T70II -E \
  -v "$(lpinfo -v | awk '/tmt70:/{print $2; exit}')" \
  -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd
```

USB, explicit URI — use this if the unit is not plugged in yet:

```bash
sudo lpadmin -p TM-T70II -E -v tmt70://04b8:0202 \
  -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd
```

Pin one specific unit when several TM printers share a host by appending the USB
serial: `tmt70://04b8:0202/<serial>`.

Ethernet (UB-E04, or a serial-to-Ethernet adaptor):

```bash
sudo lpadmin -p TM-T70II -E -v socket://192.168.1.50:9100 \
  -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd
```

From a checkout, `make queue`, `make queue PRINTER=... DEVICE_URI=...` and
`make net-queue HOST=<ip>` wrap the same commands.

### Site defaults

Set the options every job should get, so applications need no `-o` flags:

```bash
sudo lpadmin -p TM-T70II \
  -o Density=dark -o EndOfJobCut=full -o PageSize=X80mm \
  -o BlankReduction=topbottom -o FeedBeforeCut=4mm
sudo lpadmin -d TM-T70II          # make it the system default queue
```

`lpoptions -p TM-T70II -l` lists every option and its current default. The full
option table is in [README.md](README.md).

---

## 6. Smoke test

```bash
printf '==== TM-T70II ====\nthe quick brown fox 0123456789\n\n' \
  | lp -d TM-T70II -o EndOfJobCut=full -
lpstat -W completed -o TM-T70II | head
```

Paper should feed, print, and cut. From a checkout the same thing is
`make print-test LP_OPTS='-o Density=dark -o EndOfJobCut=full'`.

To check the byte stream without spending paper, capture what the backend would
send and disassemble it:

```bash
TMT70_DRYRUN=/tmp/tmt70.bin lp -d TM-T70II /etc/hostname
python3 tools/escpos_dis.py /tmp/tmt70.bin
```

---

## 7. Optional: share the queue

Other machines print through this host — details in [NETWORK.md](NETWORK.md).

```bash
sudo cupsctl --share-printers --remote-any
sudo lpadmin -p TM-T70II -o printer-is-shared=true
sudo ufw allow 631/tcp && sudo ufw allow 5353/udp    # only if ufw is active
```

---

## Upgrades and removal

```bash
sudo apt install ./epson-tmt70ii-escpos_1.1.0_all.deb   # in-place upgrade
```

Queues, option defaults and everything under `/etc/cups` survive an upgrade —
the package owns no files in `/etc`. Pin a version with
`sudo apt-mark hold epson-tmt70ii-escpos`.

Roll back:

```bash
sudo apt install --allow-downgrades ./epson-tmt70ii-escpos_1.0.0_all.deb
```

Remove:

```bash
sudo lpadmin -x TM-T70II                    # drop the queue first
sudo apt remove epson-tmt70ii-escpos        # or 'purge'
```

---

## Troubleshooting

Turn on CUPS debug logging first — it is the fastest path to the real error:

```bash
sudo cupsctl --debug-logging
lp -d TM-T70II /etc/hostname
sudo tail -f /var/log/cups/error_log
sudo cupsctl --no-debug-logging             # turn it back off afterwards
```

| Symptom | Cause | Fix |
|---|---|---|
| `lpinfo -v` shows no `tmt70://` | udev rule not applied, or printer off | re-run the udev reload/trigger from step 4; check `lsusb` |
| Job stuck at "Processing", log shows `LIBUSB_ERROR_ACCESS` | device node permissions | confirm the node is `root:lp` mode `0660`; re-run the udev trigger |
| `LIBUSB_ERROR_BUSY` | another process holds the interface (an old ESC/POS daemon, a second job) | stop that process; `lpstat -o` then `cancel -a TM-T70II` |
| Queue goes to "Stopped" after one job | the backend returned a failure | `lpstat -p TM-T70II -l` for the reason, fix it, then `cupsenable TM-T70II` |
| PDF prints blank, or the job dies in the filter chain | `ghostscript` / `poppler-utils` missing | `sudo apt install ghostscript poppler-utils cups-filters` |
| Output shifted, or wider than the paper | wrong media width | `-o PageSize=X58mm` for 58 mm rolls; adjust `-o LeftMargin=` |
| Prints but never cuts | cut disabled, or too little feed before the blade | `-o EndOfJobCut=full -o FeedBeforeCut=4mm` |
| Too faint or too dark | density | `-o Density=dark` (or `light`); density and speed are also printer-resident NV settings, see [PROTOCOL.md](PROTOCOL.md) |
| Nothing prints and CUPS reports no error | job went to a stale queue | `lpstat -t`, and check the device URI matches step 5 |

Collect this before filing an issue:

```bash
lpstat -t; lpoptions -p TM-T70II -l; lsusb | grep 04b8
dpkg -l epson-tmt70ii-escpos; cupsd -v
sudo tail -200 /var/log/cups/error_log
```

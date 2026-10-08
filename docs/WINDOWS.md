# Windows support (`tmt70print`)

Printing to the TM-T70II from Windows with **no .NET, no Epson APD, and no
OPOS ADK** — none of `EPSON_OPOS_ADK_FOR_NET_*.exe`, no .NET Framework, no COM
registration, no Pillow.

For Linux see [INSTALLATION.md](INSTALLATION.md); for WSL development see
[DEVELOPMENT.md](DEVELOPMENT.md).

---

## What this is (and is not)

`tmt70print` is at heart a **command-line printing tool**, not a Windows print
*driver*.

```
receipt.txt / logo.png  ->  tmt70print  ->  ESC/POS  ->  WinUSB | TCP 9100 | spooler RAW
```

It **is** a full port of the driver's output stage: identical ESC/POS, identical
options, sharing the actual Linux source files rather than a reimplementation.

It **is not**, and will never need to be, a v4 print driver package — the
kind that needs attestation- or WHQL-signing to install since the
PrintNightmare mitigations. That signing requirement is the one part of a
real driver that cannot be done dependency-free, so this project never builds
one.

It **can**, however, appear as a real queue in Settings → Printers, usable by
any app's File → Print, without needing that signing at all — see
**D. Native print queue** below. The trick: a *driver* renders a document into
bytes; a much smaller, unsigned-and-locally-registerable **Port Monitor**
just transports already-rendered bytes to a port. Pairing a custom monitor
with an inbox, Microsoft-signed driver ("Generic / Text Only") sidesteps the
signing wall entirely — at the cost of that inbox driver's own limits (text
only, no images; see below).

### Shared with Linux, not reimplemented

`win\tmt70win\core.py` loads these files with `importlib` and uses them as-is:

| Shared file | What Windows uses it for |
|---|---|
| `src/filter/rastertoescpos` | `EscposOut`, `build()`, rotate / blank-reduce / clamp, option parsing |
| `src/backend/tmt70` | `sink_usb()` (libusb), `sink_socket()`, `sink_file()`, `LibUsb` |
| `tools/mkraster.py` | CUPS raster v3 header offsets |

Nothing under `src/`, `debian/`, `ppd/`, `udev/` or the `Makefile` is modified,
and `win\selftest.py` asserts that with `git status`. The `.deb` build is
unaffected.

**Image output is byte-for-byte identical to Linux** for the same bitmap and
options — the parity test in `win\selftest.py` compares the two streams directly.

**Text output is deliberately different.** Linux rasterises text through
Ghostscript; Windows sends it as resident-font ESC/POS characters, which is
faster and sharper on a receipt printer but uses the printer's font rather than
the document's. Pass `--kind image` with a pre-rendered PNG if you need exact
visual parity.

---

## Requirements

| | |
|---|---|
| OS | Windows 10 / 11, x64 |
| Runtime | none, if built with `-Fetch` (CPython embeddable is bundled). Otherwise Python 3.8+ on `PATH` |
| USB output | `libusb-1.0.dll` (bundled) **and** the WinUSB driver bound to the printer — see below |
| TCP output | nothing |
| Spooler output | a pass-through queue such as *Generic / Text Only* |

---

## Install

### From the installer

```powershell
.\tmt70print-setup-1.0.0.exe
```

Installs to `C:\Program Files\tmt70print`, optionally adds it to `PATH`.

### Portable

```powershell
Expand-Archive .\epson-tmt70ii-escpos-win_1.0.0.zip C:\tmt70
C:\tmt70\tmt70print.cmd --list
```

### Build it yourself

```powershell
.\tools\build-win.ps1            # zip + installer, uses the system Python
.\tools\build-win.ps1 -Fetch     # also bundles CPython embeddable + libusb
```

The counterpart of [tools/build.sh](../tools/build.sh). It runs
`win\selftest.py` first and refuses to package if the tests fail. The installer
step is skipped with a warning when Inno Setup's `ISCC.exe` is not present —
the `.zip` is always produced.

---

## Pick a transport

### A. USB via WinUSB — the direct equivalent of the Linux setup

The TM-T70II USB interface is vendor-class, so Windows has no in-box driver for
it. Bind WinUSB to the device once:

1. Install [Zadig](https://zadig.akeo.ie/).
2. *Options ▸ List All Devices*, select the TM-T70II (`04B8:0202`).
3. Choose **WinUSB** in the target box, click *Replace Driver*.

```powershell
tmt70print --list
tmt70print receipt.txt --device tmt70://04b8:0202 --density dark --cut full
```

Two things to know before doing this:

* **It is exclusive.** While WinUSB is bound, the Epson APD queue and any OPOS
  application cannot use the printer. Zadig can restore the previous driver, and
  so can *Device Manager ▸ Update driver ▸ Browse ▸ Let me pick*.
* **It is per-machine, not per-printer.** It changes nothing in the printer's
  own NV memory, so a Linux host that later gets the same unit still sees the
  vendor-class interface and still works with `tmt70://04b8:0202` unchanged.

### B. TCP — nothing to install

For a unit on Ethernet (UB-E04) or behind a serial-to-Ethernet adaptor:

```powershell
tmt70print receipt.txt --device socket://192.168.1.50:9100
```

### C. Windows spooler — for existing queues

If the printer already has a Windows queue on a pass-through driver
(*Generic / Text Only*), write RAW through the spooler and skip USB entirely:

```powershell
tmt70print --list                       # shows every installed queue
tmt70print receipt.txt --device "printer://EPSON TM-T70II Receipt"
```

Copy the URI from `--list` verbatim, quotes included. A network share needs the
`printer://` prefix and the full UNC name, otherwise a leading `\\` is read as
a file path:

```powershell
tmt70print receipt.txt --device "printer://\\192.168.4.144\EPSON TM-T70II Receipt"
```

Whether RAW pass-through reaches the printer depends on the queue's driver. A
*Generic / Text Only* queue always works. An APD queue usually does, because the
RAW datatype bypasses the driver's rendering — worth testing before relying on
it.

### D. Native print queue — File → Print from any app, no CLI needed

A/B/C all still mean running `tmt70print` yourself. This option instead
installs a real queue in **Settings → Printers** — "EPSON TM-T70II
(tmt70print)" — that Notepad, a browser, or anything else can print to
through the normal print dialog, entirely through this project's own code.

```powershell
.\win\monitor\register-monitor.ps1 -Device "tmt70://04b8:0202" -ExtraArgs "--reflow --tabstop 2"
```

or select the **"Register a native Windows print queue"** task in the
installer. `--reflow` is strongly recommended in `-ExtraArgs` for any
text-heavy queue — see the limits below for why.

**How, without a signed driver:** a print *driver* renders a document into
bytes; a **Port Monitor** is the much smaller piece that just moves
already-rendered bytes to a port, and registering one locally
(`AddMonitor`) does not require WHQL/attestation signing the way a driver
package does. `win\monitor\tmt70mon\tmt70mon.dll` is that monitor. It's
paired with Windows' own inbox, already-signed **"Generic / Text Only"**
driver, so nothing new needs installing or trusting beyond the monitor DLL
itself. When a job arrives, the monitor accumulates its bytes to a temp file
and shells out to the normal `tmt70print` CLI (`--kind text --device
<configured>`) — same code path as A/B/C, just triggered automatically
instead of by hand.

**Current limits (v1):**

* **Text only.** "Generic / Text Only" doesn't rasterize images or graphics,
  so a document with a logo or mixed layout prints as text-only, not the full
  page. For images, use `tmt70print logo.png` directly (A/B/C).
* **One queue, one fixed target.** Configuration (which `--device` and which
  extra options) lives in the registry, not a per-print dialog:
  ```
  HKLM\SYSTEM\CurrentControlSet\Control\Print\Monitors\TMT70 Monitor\Config
  ```
  Change `Device`/`ExtraArgs` there (or re-run `register-monitor.ps1` with
  new `-Device`/`-ExtraArgs`) and restart the Spooler service to apply it.
* **Multi-page documents print as one continuous job.** The page-eject byte
  between pages is discarded rather than triggering a cut/feed.
* **Source apps pre-wrap at their own width, and the driver adds its own
  page margin as literal characters.** Notepad (and most apps) wrap text
  through "Generic / Text Only" at a width meant for a full page, not an
  80mm receipt — left alone, each of those wrapped source lines becomes its
  own paragraph here, fragmenting prose. "Generic / Text Only" also always
  emits a fixed top/left margin as literal blank lines and spaces before
  the real content (confirmed: several blank lines, then a run of plain
  spaces, then the first real line) — with nothing else, that reads as an
  unwanted first-line indent. `--reflow` in `ExtraArgs` (see above) fixes
  both: it rejoins wrapped source lines into real paragraphs before
  re-wrapping to the receipt's width, and strips that leading margin.

See [win/monitor/README.md](../win/monitor/README.md) for the full design
and diagnostics (`C:\Windows\Temp\tmt70mon.log` and `...\tmt70mon-lastjob.log`).
Full image/graphics parity with the Linux side is tracked as a separate,
unproven research question — not solved by this option.

---

## Usage

```powershell
tmt70print receipt.txt --density dark --cut full
tmt70print logo.png --media X58mm --threshold 140
tmt70print report.txt --align center --copies 2
type receipt.txt | tmt70print - --device socket://10.0.0.50:9100
tmt70print receipt.txt --dry-run -o out.bin      # build bytes, send nothing
tmt70print --list
tmt70print --show-config
```

Every option maps to the PPD keyword of the same name, so a Windows command line
and an `lp -o` describe the same job:

| Windows | Linux | Values |
|---|---|---|
| `--media` | `-o PageSize=` | `X80mm`, `X80mmLong`, `X58mm` |
| `--density` | `-o Density=` | `light`, `normal`, `dark` |
| `--speed` | `-o PrintSpeed=` | `auto`, `1`–`4` |
| `--cut` | `-o EndOfJobCut=` | `partial`, `full`, `none` |
| `--feed` | `-o FeedBeforeCut=` | millimetres |
| `--margin` | `-o LeftMargin=` | millimetres |
| `--reduce` | `-o BlankReduction=` | `off`, `topbottom`, `all` |
| `--rotate180` | `-o Rotate180=True` | flag |
| `--codepage` | `-o CodePage=` | `0`, `2`, `16`, `19` |
| `--copies` | `lp -n` | integer |

Windows-only options: `--align`, `--cols`, `--encoding`, `--no-wrap`,
`--tabstop`, `--reflow` for text; `--threshold`, `--no-dither`, `--invert`,
`--no-scale` for images. `tmt70print --help` lists everything.

`--reflow` rejoins consecutive non-blank lines into paragraphs (blank lines
still break) before hard-wrapping to the receipt's width. It matters
specifically for text arriving already-wrapped at a *different* column width
— which is exactly what the native print queue (option D) gets from apps
printing through "Generic / Text Only": each of the source app's own wrapped
lines otherwise becomes its own truncated paragraph here, fragmenting prose
at whatever point the source happened to wrap. Off by default because it
would wrongly join genuinely-meaningful line breaks in preformatted text
(an itemized list, ASCII layout); turn it on via `ExtraArgs` for a
text-heavy native queue, or by hand for a file you know was pre-wrapped
elsewhere.

### Defaults file

Site defaults live in `tmt70print.ini` — the equivalent of
`lpadmin -p TM-T70II -o ...`. Searched in order:

1. `%APPDATA%\tmt70print\tmt70print.ini`
2. next to `tmt70print.cmd`

```ini
[defaults]
device = tmt70://04b8:0202
media = X80mm
density = dark
cut = full
reduce = topbottom
```

`tmt70print --show-config` prints what is actually in effect and which file it
came from. A commented template ships as `tmt70print.ini.sample`.

### Input formats

Sniffed from the content, or forced with `--kind`:

* **Text** — any encoding `--encoding` names; wrapped to the media width and
  transcoded to the printer code page (unmappable characters become `?`, with
  smart quotes and dashes substituted first).
* **Images** — PNG (all colour types, 1/2/4/8/16-bit, non-interlaced), BMP
  (1/4/8/24/32-bit uncompressed), PNM (P1–P6). Greyscaled, Floyd–Steinberg
  dithered, scaled down to the paper width. Pure stdlib: `zlib` does the PNG
  inflate.
* **`--kind escpos`** — a raw stream, passed through untouched.

No PDF. Rasterising PDF would mean bundling Ghostscript or pdfium; convert
first, or print PDFs through the Linux queue.

---

## Testing without a printer

```powershell
python win\selftest.py
```

61 checks, no hardware, no CUPS: parity against the Linux filter, the same
byte-level ESC/POS assertions `tools/selftest.sh` makes, the image decoders,
dithering, text wrapping and code pages, URI parsing, the sinks, the CLI, and a
`git status` check that the Linux tree is untouched.

Inspect a stream:

```powershell
tmt70print receipt.txt --dry-run -o out.bin
python tools\escpos_dis.py out.bin
```

Compare against Linux — build the same job on both and diff the disassembly.
The image path must match exactly.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `--list` shows no USB device | WinUSB not bound | run Zadig against `04B8:0202`; `--list` also shows spooler queues that need no driver |
| `libusb unavailable` | no `libusb-1.0.dll` | rebuild with `-Fetch`, or drop the `MS64\dll` build into `vendor\` |
| `LIBUSB_ERROR_ACCESS` / `_BUSY` | the APD queue or an OPOS app holds the device | close it, or pause that queue |
| `OpenPrinter(...) failed: error 1801` | queue name wrong | copy the exact name from `tmt70print --list` |
| `no Python found` | no bundled runtime and none on `PATH` | install Python 3.8+, or rebuild with `-Fetch` |
| `cannot find src/filter/rastertoescpos` | shared sources not staged next to the tool | set `TMT70_HOME` to the directory holding `src\`, or reinstall |
| Text prints in the wrong font | expected — resident font, by design | pre-render to PNG and use `--kind image` |
| Accented characters print as `?` | code page lacks them | `--codepage 16` (Windows-1252) |
| Image too dark or too light | dither threshold | `--threshold 100`…`160`, or `--no-dither` |
| Native queue: nothing prints, no error | job never reached the monitor's port | check `C:\Windows\Temp\tmt70mon.log` for `WritePort`/`EndDocPort` lines; if absent, check the queue's `PortName` is `TMT70:` and the monitor is registered (`HKLM\SYSTEM\CurrentControlSet\Control\Print\Monitors\TMT70 Monitor`) |
| Native queue: `tmt70print exit=1` in `tmt70mon.log` | `tmt70print` itself failed | check `C:\Windows\Temp\tmt70mon-lastjob.log` for its own error output (same errors as running the CLI by hand) |
| Native queue: install runs but nothing registers | not run elevated, or spooler wasn't running when `AddMonitor` was called | re-run `register-monitor.ps1` from an elevated PowerShell; if it was mid-install of an already-registered monitor, `Get-Service Spooler` should be running throughout except during the DLL-replace step |

Add `-v` for the effective settings, the stream size, and the config file in use.

---

## Layout

```
win/tmt70print.py         entry point
win/tmt70print.cmd        launcher (bundled python, else py/python from PATH)
win/tmt70print.ini        sample defaults
win/tmt70print.iss        Inno Setup script (native Pascal, no .NET)
win/selftest.py           61 checks, no hardware needed
win/tmt70win/core.py      loads the Linux sources; libusb-1.0.dll shim
win/tmt70win/images.py    PNG/BMP/PNM decode + Floyd-Steinberg (stdlib only)
win/tmt70win/text.py      resident-font ESC/POS text, code pages, wrapping
win/tmt70win/job.py       image -> CUPS raster -> the Linux filter; text framing
win/tmt70win/sinks.py     WinUSB / TCP / file / spooler RAW
win/tmt70win/cli.py       argument and INI handling
win/vendor/               libusb-1.0.dll goes here
win/monitor/tmt70mon/     native Port Monitor DLL (option D, needs a C compiler to build)
win/monitor/register-monitor.ps1  install/uninstall the native queue
win/monitor/README.md     design + diagnostics for option D
tools/build-win.ps1       zip + installer (counterpart of tools/build.sh)
```

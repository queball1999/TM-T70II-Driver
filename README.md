# epson-tmt70ii-escpos

A from-scratch Linux / CUPS driver for the **EPSON TM-T70II** thermal receipt
printer, delivered as an installable `.deb`. Built by reverse-engineering the
byte stream the Windows *Advanced Printer Driver v4* emits and cross-checking it
against Epson's public ESC/POS reference.

* **Filter** `rastertoescpos` — CUPS raster → ESC/POS; owns all the print options.
* **Backend** `tmt70` — libusb access to the vendor-class USB interface `04b8:0202`
  (the kernel `usblp` driver never binds it, so there is no `/dev/usb/lp0`).
  Also does `file://`, `socket://` and a `TMT70_DRYRUN` dump.
* **PPD** `EPSON_TM-T70II.ppd` — exposes density, speed, 80/58 mm media,
  180° rotation, blank-space reduction, left margin, copies and cut style as
  ordinary printer preferences (GNOME dialog, `lp -o`, `http://localhost:631`).
* **udev rule** — grants the `lp` group access to the device.

## Quick start

```bash
make selftest                                   # no printer required
make deb                                         # -> epson-tmt70ii-escpos_1.0.0_all.deb
sudo apt install ./epson-tmt70ii-escpos_1.0.0_all.deb
sudo usermod -aG lp "$USER"                       # then log out / in
make queue                                        # PRINTER=TM-T70II DEVICE_URI=tmt70://04b8:0202
make print-test LP_OPTS='-o Density=dark -o EndOfJobCut=full'
```

`make help` lists every target.

## Documentation

* [docs/INSTALLATION.md](docs/INSTALLATION.md) — installing on a production Linux host,
  step by step, with troubleshooting
* [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — building and validating from Windows via
  WSL2, including forwarding the USB printer with `usbipd`
* [docs/WINDOWS.md](docs/WINDOWS.md) — Windows support: `tmt70print`, zero .NET,
  no Epson APD or OPOS ADK
* [docs/README.md](docs/README.md) — option reference / Makefile targets / testing guide
* [docs/NETWORK.md](docs/NETWORK.md) — sharing the queue to other machines; Ethernet units
* [docs/PROTOCOL.md](docs/PROTOCOL.md) — the reverse-engineering write-up (what the
  Windows driver actually sends, and how each option maps to ESC/POS)

## Windows

A command-line front end, `tmt70print`, prints from Windows with **zero .NET** —
no Epson APD, no `EPSON_OPOS_ADK_FOR_NET_*.exe`, no COM registration, no Pillow.

```powershell
.\tools\build-win.ps1 -Fetch          # -> epson-tmt70ii-escpos-win_1.0.0.zip + setup .exe
tmt70print receipt.txt --density dark --cut full
tmt70print logo.png --media X58mm
tmt70print receipt.txt --device socket://10.0.0.50:9100
```

It imports `src/filter/rastertoescpos` and `src/backend/tmt70` unmodified, so
image output is byte-for-byte identical to Linux — `python win/selftest.py`
asserts that (61 checks, no printer needed). Transports: WinUSB via libusb, raw
TCP 9100, or RAW through an existing Windows queue.

A real, native queue is also available — "EPSON TM-T70II (tmt70print)" in
Settings → Printers, usable by any app's File → Print, no CLI needed — via a
custom Port Monitor (`win/monitor/`) paired with Windows' own inbox "Generic
/ Text Only" driver, which needs no signing. Text only for now; see
[docs/WINDOWS.md](docs/WINDOWS.md) for the setup, limits, and the
signing/preferences-dialog trade-off behind the CLI-first design.

## Layout

```
src/filter/rastertoescpos     CUPS raster -> ESC/POS
src/backend/tmt70             libusb backend
ppd/EPSON_TM-T70II.ppd        preferences UI
udev/60-epson-tmt70ii.rules   device access
debian/                       .deb packaging (debhelper)
tools/                        win-capture.ps1, escpos_dis.py, mkraster.py,
                              selftest.sh, band_geom.py, build.sh
Makefile                      build / test / install / queue / decode targets
win/                          Windows front end (tmt70print) + Inno Setup packaging
win/monitor/                  native print queue (Port Monitor + inbox driver, no signing)
tools/build-win.ps1           Windows zip / installer build
```

## Status

Validated on real hardware: built as a `.deb`, installed on Ubuntu 26.04
(CUPS 2.4.16), queue created on `tmt70://04b8:0202`, and receipts printed over
USB end-to-end (`lp` → Ghostscript raster at 510×N dots → `rastertoescpos` →
`tmt70` libusb backend → printer). Filter self-tests pass; every PPD option was
checked against the emitted ESC/POS.

The RE capture (baseline vs density-dark, via `win-capture.ps1 -Method SwapPort`)
showed the Windows driver sends **no per-job density/speed command** — those are
printer-resident NV settings. This driver emits the documented per-job
`GS ( K` fn49/fn50 instead, which the TM-T70II accepts. Details in
docs/PROTOCOL.md.

USB note: the TM-T70II is a vendor-class device, so on WSL2 it must be attached
with `usbipd`; on a normal Linux box the shipped udev rule is enough.

Windows front end validated on real hardware: `python win/selftest.py` passes
61/61 (parity against the Linux filter's byte stream, ESC/POS assertions, image
decoders, dithering, text wrapping/code pages, URI parsing, the file sink, and
the CLI), and physical receipts — text, a logo image, and a combined
multi-part receipt — printed successfully from Windows via the spooler-RAW
transport (`tmt70print file --device "printer://..."`).

The native print queue (`win/monitor/`, see docs/WINDOWS.md option D) is also
validated end-to-end: an unsigned, locally-registered Port Monitor DLL loads
into `spoolsv.exe` under real Windows security policy (no signing prompt, no
policy block), and a real Notepad File → Print job flowed through it to a
physical printed receipt. Text-only in this version; full image/graphics
parity with the Linux side remains an open research question.

License: GPL-2.0-or-later.

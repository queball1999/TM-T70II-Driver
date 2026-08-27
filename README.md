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
* [docs/README.md](docs/README.md) — option reference / Makefile targets / testing guide
* [docs/NETWORK.md](docs/NETWORK.md) — sharing the queue to other machines; Ethernet units
* [docs/PROTOCOL.md](docs/PROTOCOL.md) — the reverse-engineering write-up (what the
  Windows driver actually sends, and how each option maps to ESC/POS)

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

License: GPL-2.0-or-later.

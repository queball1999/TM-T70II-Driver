# epson-tmt70ii-escpos

A from-scratch Linux/CUPS driver for the **EPSON TM-T70II** thermal receipt
printer, packaged as a `.deb`. Built by reverse-engineering the byte stream the
Windows *Advanced Printer Driver v4* emits (see [PROTOCOL.md](PROTOCOL.md)) and
cross-checking against Epson's public ESC/POS command reference.

> **Guides:** [INSTALLATION.md](INSTALLATION.md) walks a production install on a
> Linux host end to end; [DEVELOPMENT.md](DEVELOPMENT.md) covers building and
> validating from Windows through WSL2 (including `usbipd` USB forwarding).
> This file is the option / Makefile / testing reference.

## What's in the box

| Path (installed) | Role |
|---|---|
| `/usr/lib/cups/filter/rastertoescpos` | CUPS raster → ESC/POS. All the print options live here. |
| `/usr/lib/cups/backend/tmt70` | libusb backend for the vendor-class USB interface `04b8:0202`. Also `file://`, `socket://`, and a `TMT70_DRYRUN` dump mode. |
| `/usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd` | Model + preferences UI. |
| `/lib/udev/rules.d/60-epson-tmt70ii.rules` | Gives the `lp` group access to the device (kernel `usblp` never binds it). |

## Why a custom backend

The TM-T70II USB interface is a **vendor-specific class**, not the USB printer
class, so Linux never creates `/dev/usb/lp0`. `tmt70` opens the device with
libusb (via `ctypes` — no extra Python packages) and writes to its bulk OUT
endpoint. A udev rule grants access; add yourself to `lp`:

```bash
sudo usermod -aG lp "$USER"   # then log out and back in
```

## Install

```bash
sudo apt install ./epson-tmt70ii-escpos_1.0.0_all.deb
```

Full production procedure — requirements, queue creation, site defaults, upgrades
and troubleshooting — in [INSTALLATION.md](INSTALLATION.md).

Dependencies: `cups`, `libusb-1.0-0`, `udev`; plus `cups-filters`,
`ghostscript` and `poppler-utils` for the raster pipeline (Recommended).

## Create the queue

```bash
# auto-detected URI:
lpadmin -p TM-T70II -E \
  -v "$(lpinfo -v | awk '/tmt70:/{print $2; exit}')" \
  -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd

# or explicitly:
lpadmin -p TM-T70II -E -v tmt70://04b8:0202 \
  -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd
```

## Print

```bash
lp -d TM-T70II report.pdf
lp -d TM-T70II -o Density=dark -o PrintSpeed=2 -o EndOfJobCut=full receipt.txt
lp -d TM-T70II -o PageSize=X58mm -o Rotate180=True -o BlankReduction=all note.pdf
lp -d TM-T70II -n 2 -o EndOfJobCut=full ticket.pdf
```

### Options (also in the GNOME print dialog / `http://localhost:631` / `lpoptions -l`)

| Option | Choices | Effect |
|---|---|---|
| `PageSize` | `X80mm` (default), `X80mmLong`, `X58mm`, `Custom.WIDTHxHEIGHT` | media width / roll length |
| `ColorModel` | `KGray` (1-bit, default), `Gray` (8-bit) | raster depth fed to the filter |
| `Density` | `light`, `normal`, `dark` | `GS ( K` fn 49 print density |
| `PrintSpeed` | `auto`, `1`–`4` | `GS ( K` fn 50 print speed |
| `Rotate180` | `False`/`True` | rotate the whole page 180° |
| `BlankReduction` | `off`, `topbottom`, `all` | trim blank paper; `all` also collapses internal gaps |
| `LeftMargin` | `0mm`…`10mm` | `GS L` left offset |
| `EndOfJobCut` | `partial` (default), `full`, `none` | `GS V` cut style |
| `FeedBeforeCut` | `0mm`…`12mm` | blank feed before the cut |
| `CodePage` | `0` (PC437), `16` (WPC1252), `2` (PC850), `19` (PC858) | `ESC t` code page |

## Makefile

`make help` lists everything. Common targets:

| Target | What it does |
|---|---|
| `make selftest` | byte-compile + raster→filter→ESC/POS assertions (no printer) |
| `make ppdtest` | validate the PPD with `cupstestppd` |
| `make lint` | compile + `ppdtest` + `shellcheck` |
| `make deb` / `make deb-lint` | build the `.deb` (in `~/build`, copied back), optionally run lintian |
| `sudo make install` | install filter/backend/PPD/udev/metainfo straight into the running system (no `.deb`); honours `DESTDIR` / `PREFIX` |
| `sudo make uninstall` | remove what `make install` added |
| `make queue` | `lpadmin` the CUPS queue — override `PRINTER=` / `DEVICE_URI=` |
| `make remove-queue` | delete the queue |
| `make net-queue HOST=<ip>` | queue for a TM-T70II reached over TCP (`socket://ip:9100`) |
| `make share` | publish this host's queue to the LAN over IPP |
| `make unshare` | stop publishing it |
| `make client-queue SERVER=<host>` | (on a remote machine) add the shared queue |
| `make print-test` | send a generated test receipt — add `LP_OPTS='-o Density=dark -o EndOfJobCut=full'` |
| `make dryrun` | same, but capture the ESC/POS to `/tmp/tmt70-dryrun.bin` and disassemble it |
| `make decode FILE=capture/x.prn` | disassemble any ESC/POS / `.prn` stream |
| `make capture-decode` | disassemble every `capture/*.prn` |
| `make clean` / `make distclean` | drop build products / also drop captures |

Typical bring-up on a fresh machine:

```bash
make selftest
make deb
sudo apt install ./epson-tmt70ii-escpos_1.0.0_all.deb   # or: sudo make install
sudo usermod -aG lp "$USER"        # then re-login
make queue                          # PRINTER=TM-T70II DEVICE_URI=tmt70://04b8:0202
make print-test LP_OPTS='-o Density=dark -o EndOfJobCut=full'
```

## Network printing

The printer is USB, but other machines can print to it through this host — see
[NETWORK.md](NETWORK.md). Short version:

```bash
# on the Linux host with the printer:
make queue && make share
# on each remote machine:
make client-queue SERVER=<host-or-ip>       # add DRIVERLESS=1 to skip installing this pkg
```

If the printer is later put on Ethernet (Epson UB-E04): `make net-queue HOST=<ip>`.

## Testing without a printer

(On a Windows workstation, do this inside WSL — see [DEVELOPMENT.md](DEVELOPMENT.md).)

```bash
make selftest
python3 tools/mkraster.py page.pbm | \
  PPD= ./src/filter/rastertoescpos 1 me t 1 "Density=dark" | \
  python3 tools/escpos_dis.py -           # disassemble what the filter produced
make dryrun LP_OPTS='-o PageSize=X58mm -o Rotate180=True'
```

`TMT70_DRYRUN=/tmp/out.bin lp -d TM-T70II file.pdf` captures the exact bytes sent
to the printer; compare with a Windows APD capture using `tools/escpos_dis.py`.

## Build the .deb directly

```bash
./tools/build.sh            # builds in ~/build (not on /mnt/c), copies *.deb back
./tools/build.sh lintian
```

## Reverse-engineering the Windows driver (optional, Windows side)

Full capture/compare loop in [DEVELOPMENT.md](DEVELOPMENT.md).

```powershell
# baseline (captures to a FILE port - nothing is printed on paper)
.\tools\win-capture.ps1 -Tag baseline
# then change ONE setting in the TM-T70II Printing Preferences dialog and:
.\tools\win-capture.ps1 -Tag density-dark
python .\tools\escpos_dis.py .\capture\density-dark__blocks.prn
```

## Status / limitations

- Density and speed command *encodings* are from Epson's public reference; the
  RE capture validated the raster/cut/margin framing. To pin density/speed
  byte-exact to APD, capture with that preference toggled (above) and adjust
  `EscposOut.density()` / `.speed()` in the filter.
- CUPS raster input must be **uncompressed v2/v3** (the PPD requests this).
- Barcodes/QR print as rasterised graphics (the pipeline is image-based), which
  is fine for receipts but not byte-level ESC/POS barcodes.

# epson-tmt70ii-escpos

A from-scratch Linux/CUPS driver for the **EPSON TM-T70II** thermal receipt
printer, packaged as a `.deb`. Built by reverse-engineering the byte stream the
Windows *Advanced Printer Driver v4* emits (see [PROTOCOL.md](PROTOCOL.md)) and
cross-checking against Epson's public ESC/POS command reference.

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

## Testing without a printer

```bash
./tools/selftest.sh                      # raster → filter → ESC/POS + assertions
python3 tools/mkraster.py page.pbm | \
  PPD= ./src/filter/rastertoescpos 1 me t 1 "Density=dark" | \
  python3 tools/escpos_dis.py -           # disassemble what the filter produced
```

`TMT70_DRYRUN=/tmp/out.bin lp -d TM-T70II file.pdf` captures the exact bytes sent
to the printer; compare with a Windows APD capture using `tools/escpos_dis.py`.

## Build the .deb

```bash
./tools/build.sh            # builds in ~/build (not on /mnt/c), copies *.deb back
./tools/build.sh lintian
```

## Reverse-engineering the Windows driver (optional, Windows side)

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

# TM-T70II — protocol notes (reverse-engineered + documented)

## How these notes were produced

- `tools/win-capture.ps1` builds a throwaway queue on the **same Epson APD4 driver**
  (`EPSON TM-T70 ReceiptE4`) pointed at a Windows FILE port, mirrors the real queue's
  driver preferences onto it, and prints a set of text documents. One `.prn` per document
  lands in `capture/`.
- `tools/escpos_dis.py` / `tools/band_geom.py` decode those streams.
- Command semantics cross-checked against Epson's public **ESC/POS Command Reference**
  (TM printer, "Commands in Code Order").

The printer is a **thermal** receipt printer: 180 × 180 dpi, ~512-dot (72 mm) print width
on 80 mm roll, monochrome. There is no colour, greyscale, resolution choice, or page
layout in the desktop-printer sense.

## USB

| | |
|---|---|
| VID:PID | `04b8:0202` |
| Windows device | "EPSON USB Controller for TM/BA/EU Printers" — **vendor-specific class**, not USB-printer class |
| Consequence on Linux | kernel `usblp` does **not** bind; there is no `/dev/usb/lp0`. Talk to it with **libusb** (bulk OUT to the interface's OUT endpoint). A udev rule grants group access. |
| Interface / endpoints | single vendor interface; one bulk OUT + one bulk IN (confirm with `lsusb -v` on the target; the backend auto-detects). |

## What the Windows APD4 driver actually emits (baseline capture)

Per job, in order:

| Bytes | Command | Notes / our driver |
|---|---|---|
| `1B 3D 01` | `ESC = 1` — select peripheral = printer | harmless; we emit it |
| `1D 28 4A 02 00 01 00` ×… | `GS ( J` (01,02,03) | **not in the public reference — APD-private framing** (the ESD-PRT port monitor normally consumes it). Unknown `GS (` groups are length-skipped by the printer. **We do not emit it.** |
| `1B 63 30 02` / `1B 63 31 02` / `1B 63 33 00` | `ESC c 0/1/3 …` — paper-sensor / stop config | defensive config. `ESC c 3 0` = no sensor drives the paper-end signal. We emit a safe fixed set. |
| `1B 32` | `ESC 2` — default line spacing | we emit it |
| `1B 52 00` | `ESC R 0` — international char set = USA | raster pipeline → cosmetic; we emit it |
| `1B 74 10` | `ESC t 16` — character code table = page 16 | raster pipeline → cosmetic; we set the PPD-selected page |
| `1D 62 01` | `GS b 1` — smoothing ON | we emit it |
| `1B 4A 00` then `1B 4A n` | `ESC J n` — print & feed n dots | **top margin / inter-band feed** |
| `1B 24 nL nH` | `ESC $` — absolute horizontal position | **left offset** of each raster band (baseline used 176–184 dots) |
| `1D 28 4C pL pH 30 70 30 01 01 31 xL xH yL yH <data>` | `GS ( L` **fn=112** — store graphics (raster) to print buffer | `m=48 fn=112 a=48 bx=1 by=1 c=49`; `xL xH` = width in **dots**, `yL yH` = height in **dots**; data = row-major, MSB-first, `ceil(x/8)` bytes per row, 1 = black dot. APD splits tall images into bands of **≤256 dots**. |
| `1D 28 4C 02 00 30 32` | `GS ( L` **fn=50** — print the graphics buffer | one after each stored band |
| `1B 4A 00` | `ESC J 0` | final feed |
| `1D 56 41 00` | `GS V 65 0` — feed to cut position, **full cut** | APD default cut = **full**, not partial |

No `ESC @` (initialize) is sent — APD relies on the printer's stored/NV state. Our driver
sends a light init so CUPS jobs are self-contained (`ESC @`, then line spacing / smoothing).

### Band geometry seen in `capture/baseline__*.prn`

```
plain    : 2 bands, width 184 dots,  offset 184, full cut
blocks   : 4 bands, width 184 dots,  offset 184, full cut   (~256-dot bands)
columns  : 2 bands, width 184 dots,  offset 184, full cut
ruler    : 2 bands, width 192 dots,  offset 176, full cut
```

The narrow widths/offsets are an artefact of feeding **plain text through GDI + `Out-Printer`**
(tiny default font) plus APD's crop-to-ink paper-saving — they are **not** how our CUPS
raster pipeline behaves (Ghostscript rasterises to the full 512-dot width and we own the
geometry). The capture's value is the **command vocabulary and framing**, which is confirmed
above.

## Option → ESC/POS mapping used by this driver

| PPD option | Command | Source | Status |
|---|---|---|---|
| raster image | `GS ( L`/`GS 8 L` fn 112 + fn 50 | capture ✔ + reference | implemented |
| `PageSize` `X80mm` / `X58mm` / custom roll length | raster width clamp; `GS L nL nH` (left margin) + `GS W nL nH` (print-area width) | reference | implemented |
| `LeftMargin` | `GS L nL nH` (and/or `ESC $` per band, as APD does) | capture ✔ | implemented |
| `Rotate180` | rotate the raster buffer 180° in the filter (device-agnostic) | — | implemented |
| `EndOfJobCut` `PartialCut` / `FullCut` / `None` | `GS V 66 n` / `GS V 65 n` / (nothing) | capture ✔ (APD uses 65) | implemented |
| `Copies` | re-emit body + cut between copies | — | implemented |
| `BlankReduction` `Off` / `TopBottom` / `All` | trim leading/trailing blank scanlines; `All` also collapses interior blank runs | APD "paper reduction" analogue | implemented |
| `Density` `Light` / `Normal` / `Dark` | `GS ( K` **fn=49** (pL pH = 02 00, m=49, n∈{250,0,6}=light/normal/dark by convention) | Epson reference | **doc-based — capture pending** |
| `PrintSpeed` `1`‑`4` / `Auto` | `GS ( K` **fn=50** (pL pH = 02 00, m=50, n = level) | Epson reference | **doc-based — capture pending** |

To confirm the two "capture pending" rows byte-for-byte: set that one control in the
Windows *Printing preferences* dialog for **EPSON TM-T70II Receipt**, then run
`./tools/win-capture.ps1 -Tag density-dark` (etc.) and diff with `tools/escpos_dis.py`.
If APD turns out to encode them via memory switch (`GS ( E` fn=3) or `GS ( M` instead,
switch the filter's `_density()` / `_speed()` helpers — they are isolated for exactly this.

## References

- Epson ESC/POS Command Reference — Commands in Code Order:
  <https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/commands.html>
- `GS ( L` / `GS 8 L` (graphics): …/escpos/gs_lparen_cl.html
- `GS ( K` fn 49/50 (print density / speed): …/escpos/gs_lparen_lk_fn49.html , `_fn50`
- `GS V` (paper cut): …/escpos/gs_cv.html
- TM-T70II Technical Reference Guide (paper widths, print area, memory switches).

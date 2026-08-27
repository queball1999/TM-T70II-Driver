#!/usr/bin/env python3
"""Quick look at GS ( L fn=112 raster band geometry inside a captured .prn."""
import sys

for path in sys.argv[1:]:
    d = open(path, "rb").read()
    i = 0
    bands = []
    while True:
        j = d.find(b"\x1d\x28\x4c", i)
        if j < 0:
            break
        pL, pH = d[j + 3], d[j + 4]
        plen = pL | (pH << 8)
        m, fn = d[j + 5], d[j + 6]
        if fn == 112 and j + 15 <= len(d):
            x = d[j + 11] | (d[j + 12] << 8)
            y = d[j + 13] | (d[j + 14] << 8)
            bands.append((x, y, plen))
        i = j + 3
    tot = sum(b[1] for b in bands)
    widths = sorted({b[0] for b in bands})
    print(f"{path}")
    print(f"   raster bands : {len(bands)}")
    print(f"   band widths  : {widths} dots  (~{[round(w/180*25.4,1) for w in widths]} mm @180dpi)")
    print(f"   total height : {tot} dots  (~{tot/180*25.4:.0f} mm)")
    # also: ESC $ left offset, ESC J feeds, cut style
    escpos_left = []
    k = 0
    while True:
        j = d.find(b"\x1b\x24", k)
        if j < 0 or j + 4 > len(d):
            break
        escpos_left.append(d[j + 2] | (d[j + 3] << 8))
        k = j + 2
    cut = "full (GS V 65)" if b"\x1d\x56\x41" in d else ("partial (GS V 66)" if b"\x1d\x56\x42" in d else "?")
    print(f"   ESC $ offsets: {sorted(set(escpos_left))} dots")
    print(f"   cut          : {cut}")
    print()

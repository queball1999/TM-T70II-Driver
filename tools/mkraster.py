#!/usr/bin/env python3
"""
mkraster.py -- wrap a binary PBM (P4) as a single-page CUPS raster v3 stream
so the rastertoescpos filter can be exercised without CUPS or a printer.

    python3 tools/mkraster.py test/sample.pbm > /tmp/page.ras
    PPD= ./src/filter/rastertoescpos 1 me t 1 "Density=dark EndOfJobCut=full" \
         < /tmp/page.ras > /tmp/out.escpos
    python3 tools/escpos_dis.py /tmp/out.escpos
"""
import struct
import sys

HEADER_LEN = 1796
BE = ">"

# offsets used by CupsRaster._U in src/filter/rastertoescpos
OFF = {
    "HWResolution0": 276, "HWResolution1": 280,
    "NumCopies": 340,
    "cupsWidth": 372, "cupsHeight": 376,
    "cupsBitsPerColor": 384, "cupsBitsPerPixel": 388,
    "cupsBytesPerLine": 392, "cupsColorOrder": 396,
    "cupsColorSpace": 400, "cupsCompression": 404,
    "cupsNumColors": 420,
}


def read_pbm(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(b"P4"):
        raise SystemExit("expected a binary PBM (P4)")
    # parse header: P4 <ws> W <ws> H <ws(one)> then raster
    idx = 2
    fields = []
    while len(fields) < 2:
        while idx < len(data) and data[idx:idx + 1].isspace():
            idx += 1
        if data[idx:idx + 1] == b"#":
            while idx < len(data) and data[idx:idx + 1] != b"\n":
                idx += 1
            continue
        start = idx
        while idx < len(data) and not data[idx:idx + 1].isspace():
            idx += 1
        fields.append(int(data[start:idx]))
    idx += 1  # single whitespace after H
    w, h = fields
    row = (w + 7) // 8
    body = data[idx:idx + row * h]
    if len(body) != row * h:
        raise SystemExit("truncated PBM body")
    return w, h, row, body


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    w, h, row, body = read_pbm(argv[1])

    hdr = bytearray(HEADER_LEN)

    def u32(name, val):
        struct.pack_into(BE + "I", hdr, OFF[name], val)

    u32("HWResolution0", 180)
    u32("HWResolution1", 180)
    u32("NumCopies", 1)
    u32("cupsWidth", w)
    u32("cupsHeight", h)
    u32("cupsBitsPerColor", 1)
    u32("cupsBitsPerPixel", 1)
    u32("cupsBytesPerLine", row)
    u32("cupsColorOrder", 0)
    u32("cupsColorSpace", 3)     # CUPS_CSPACE_K  (1 bit set => black)
    u32("cupsCompression", 0)
    u32("cupsNumColors", 1)
    hdr[1732:1732 + 5] = b"X80mm"

    out = sys.stdout.buffer
    out.write(b"RaS3")
    out.write(hdr)
    out.write(body)


if __name__ == "__main__":
    main(sys.argv)

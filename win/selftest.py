#!/usr/bin/env python3
"""
selftest -- validate the Windows front end without a printer.

SPDX-License-Identifier: GPL-2.0-or-later

The important check is PARITY: for the same bitmap and the same options, the
bytes this package emits must be identical to the bytes the Linux .deb emits,
because both run src/filter/rastertoescpos.  Anything else means the Windows
path has diverged.

    python win/selftest.py            # from the repo root
"""

import contextlib
import io
import os
import subprocess
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tmt70win import core, images, job, sinks, text   # noqa: E402

PASS, FAIL = [], []


@contextlib.contextmanager
def quiet():
    """Swallow the filter/backend stderr chatter (they log per page)."""
    saved = sys.stderr
    sys.stderr = io.StringIO()
    try:
        yield
    finally:
        sys.stderr = saved


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "ok  " if cond else "FAIL"
    print(f"  {mark} {name}" + (f"  -- {detail}" if detail and not cond else ""))
    return cond


def hexof(b):
    return b.hex()


# --------------------------------------------------------------------------- #

def make_pbm(w=512, h=700):
    """The same test bitmap tools/selftest.sh builds, as packed 1bpp.

    Feature coordinates are given for the canonical 512x700 page and scaled
    proportionally, so small sizes stay in bounds for the decoder tests.
    """
    row = (w + 7) // 8
    buf = bytearray(row * h)
    sy = lambda v: min(h, max(0, v * h // 700))
    sx = lambda v: min(w, max(0, v * w // 512))

    def hbar(y0, y1):
        for y in range(sy(y0), sy(y1)):
            for b in range(row):
                buf[y * row + b] = 0xFF

    def box(y0, y1, x0, x1):
        for y in range(sy(y0), sy(y1)):
            for x in range(sx(x0), sx(x1)):
                buf[y * row + (x >> 3)] |= 0x80 >> (x & 7)

    hbar(20, 60)
    box(120, 200, 40, 300)
    box(260, 320, 200, 470)
    hbar(360, 380)
    hbar(690, 700)
    # mask off padding bits past w so packed comparisons are exact
    if w % 8:
        mask = (0xFF << (8 - w % 8)) & 0xFF
        for y in range(h):
            buf[y * row + row - 1] &= mask
    return bytes(buf), w, h


def png_from_bitmap(bitmap, w, h):
    """Encode packed 1bpp (1=black) as an 8-bit greyscale PNG."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)                                   # filter type 0
        base = y * ((w + 7) // 8)
        for x in range(w):
            bit = (bitmap[base + (x >> 3)] >> (7 - (x & 7))) & 1
            raw.append(0 if bit else 255)

    def chunk(tag, body):
        return (len(body).to_bytes(4, "big") + tag + body
                + zlib.crc32(tag + body).to_bytes(4, "big"))

    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- #

def test_shared_sources():
    print("\n== shared sources load ==")
    f = core.filter_mod()
    check("filter imports", hasattr(f, "EscposOut") and hasattr(f, "build"))
    check("filter constants", f.MAX_DOTS == 512 and f.DPI == 180,
          f"MAX_DOTS={f.MAX_DOTS} DPI={f.DPI}")
    mk = core.mkraster_mod()
    check("mkraster imports", mk.HEADER_LEN == 1796)
    try:
        b = core.backend_mod()
        check("backend imports", hasattr(b, "sink_usb") and hasattr(b, "sink_socket"))
    except Exception as e:                              # noqa: BLE001
        check("backend imports", False, str(e))


def test_parity():
    """Windows image path vs the Linux filter run as a subprocess."""
    print("\n== parity with the Linux filter ==")
    bitmap, w, h = make_pbm()
    png = png_from_bitmap(bitmap, w, h)

    cases = [
        ("baseline", "Density=normal BlankReduction=off EndOfJobCut=partial"),
        ("dark-full", "Density=dark BlankReduction=off EndOfJobCut=full"),
        ("reduce-all", "Density=normal BlankReduction=all EndOfJobCut=partial"),
        ("rot-margin", "Rotate180=True LeftMargin=5mm EndOfJobCut=none"),
    ]

    f = core.filter_mod()
    for label, optstr in cases:
        # --- Windows path -------------------------------------------------- #
        opts = job.Options(page_size="X80mm")
        opts["PageSizeDots"] = str(f.MAX_DOTS)      # match the filter's own default
        opts.update_optstring(optstr)
        opts["_copies"] = "1"
        win_bytes = job.build_image_job(opts, png, dither=False, threshold=128,
                                        scale=False)

        # --- reference: the same filter, fed the same raster --------------- #
        stream = job.raster_stream(bitmap, w, h, "X80mm")
        ref_opts = dict(f.DEFAULTS)
        ref_opts["PageSizeDots"] = str(f.MAX_DOTS)
        ref_opts.update(f.parse_opt_string(optstr))
        ref_opts["_copies"] = "1"
        ref_out = io.BytesIO()
        f.build(ref_opts, io.BytesIO(stream), ref_out)
        ref_bytes = ref_out.getvalue()

        check(f"parity {label}", win_bytes == ref_bytes,
              f"{len(win_bytes)} vs {len(ref_bytes)} bytes")


def test_escpos_assertions():
    """The same byte-level assertions tools/selftest.sh makes."""
    print("\n== ESC/POS assertions ==")
    bitmap, w, h = make_pbm()
    png = png_from_bitmap(bitmap, w, h)
    f = core.filter_mod()

    def run(optstr):
        opts = job.Options(page_size="X80mm")
        opts["PageSizeDots"] = str(f.MAX_DOTS)
        opts.update_optstring(optstr)
        opts["_copies"] = "1"
        return job.build_image_job(opts, png, dither=False, scale=False)

    base = run("Density=normal BlankReduction=off EndOfJobCut=partial")
    dark = run("Density=dark BlankReduction=off EndOfJobCut=full")
    rot = run("Rotate180=True LeftMargin=5mm EndOfJobCut=none")
    red = run("Density=normal BlankReduction=all EndOfJobCut=partial")

    check("GS ( L graphics block", "1d284c" in hexof(base))
    check("GS ( L fn=50 print buffer", "1d284c02003032" in hexof(base))
    check("GS V 66 partial cut", "1d5642" in hexof(base))
    check("GS V 65 full cut", "1d5641" in hexof(dark))
    check("GS ( K density=dark", "1d284b02003106" in hexof(dark))
    check("GS L left margin 35 dots", "1d4c2300" in hexof(rot))
    check("no cut when EndOfJobCut=none", "1d56" not in hexof(rot))
    check("BlankReduction=all shrinks output", len(red) < len(base),
          f"{len(red)} vs {len(base)}")


def test_image_decoders():
    print("\n== image decoders ==")
    bitmap, w, h = make_pbm(64, 32)
    png = png_from_bitmap(bitmap, w, h)
    g = images.decode(png)
    check("PNG greyscale decode", (g.w, g.h) == (w, h), f"{g.w}x{g.h}")
    back, bw, bh = g.to_bitmap(dither=False)
    check("PNG round-trips to the same bitmap", back == bitmap)

    pbm = b"P4\n%d %d\n" % (w, h) + bitmap
    g2 = images.decode(pbm)
    check("PBM decode", (g2.w, g2.h) == (w, h))
    check("PBM matches PNG", g2.to_bitmap(dither=False)[0] == bitmap)

    # 24-bit BMP, bottom-up, of a 4x2 pattern
    px = [(0, 0, 0), (255, 255, 255), (0, 0, 0), (255, 255, 255)]
    stride = ((4 * 24 + 31) // 32) * 4
    rows = b""
    for _ in range(2):
        row = b"".join(bytes([b, gg, r]) for (r, gg, b) in px)
        rows += row + b"\x00" * (stride - len(row))
    hdr = (b"BM" + (14 + 40 + len(rows)).to_bytes(4, "little") + b"\x00" * 4
           + (14 + 40).to_bytes(4, "little"))
    info = (b"(\x00\x00\x00" + (4).to_bytes(4, "little") + (2).to_bytes(4, "little")
            + b"\x01\x00\x18\x00" + b"\x00" * 24)
    g3 = images.decode(hdr + info + rows)
    check("BMP 24-bit decode", (g3.w, g3.h) == (4, 2), f"{g3.w}x{g3.h}")
    check("BMP pixel values", g3.pix[0] == 0 and g3.pix[1] == 255)

    big = images.Gray(1000, 500, bytes(1000 * 500))
    small = big.fit_width(512)
    check("scale to paper width", (small.w, small.h) == (512, 256),
          f"{small.w}x{small.h}")
    check("no upscaling", images.Gray(100, 50, bytes(5000)).fit_width(512).w == 100)


def test_dither():
    print("\n== dithering ==")
    mid = images.Gray(64, 8, bytes([128] * (64 * 8)))
    bm, w, h = mid.to_bitmap(dither=True)
    ones = sum(bin(b).count("1") for b in bm)
    check("50% grey dithers to a mix", 0 < ones < w * h,
          f"{ones} of {w*h} dots set")
    black, _, _ = images.Gray(64, 8, bytes([0] * (64 * 8))).to_bitmap(dither=True)
    check("solid black is all dots", black == b"\xff" * len(black))
    white, _, _ = images.Gray(64, 8, bytes([255] * (64 * 8))).to_bitmap(dither=True)
    check("solid white is no dots", white == b"\x00" * len(white))


def test_text():
    print("\n== text path ==")
    f = core.filter_mod()
    opts = job.Options(page_size="X80mm", Density="dark", EndOfJobCut="full")
    opts["_copies"] = "1"
    out = job.build_text_job(opts, "HELLO\nWORLD")
    hx = hexof(out)
    check("ESC @ init", hx.startswith("1b40"))
    check("density=dark present", "1d284b02003106" in hx)
    check("full cut present", "1d5641" in hx)
    check("text is in the stream", b"HELLO" in out and b"WORLD" in out)

    check("80mm is 42 columns", text.columns_for("X80mm") == 42)
    check("58mm is 30 columns", text.columns_for("X58mm") == 30)
    wrapped = text.wrap("x" * 100, 42)
    check("hard wrap at width", all(len(x) <= 42 for x in wrapped) and len(wrapped) == 3,
          f"{[len(x) for x in wrapped]}")
    check("wrap breaks on spaces", text.wrap("aaa bbb ccc", 7) == ["aaa bbb", "ccc"],
          str(text.wrap("aaa bbb ccc", 7)))

    reflowed = text.reflow("\tfirst line\nsecond line\n\n\tnew paragraph")
    check("reflow joins soft-wrapped lines",
          reflowed == "\tfirst line second line\n\n\tnew paragraph", repr(reflowed))
    check("reflow preserves blank-line breaks", text.reflow("a\n\nb") == "a\n\nb",
          repr(text.reflow("a\n\nb")))
    margin = text.reflow("\r\n\n\n\n\n\n       Alyssa,\r\n\r\nnext line")
    check("reflow strips Generic/Text-Only's page-margin preamble",
          margin == "Alyssa,\n\nnext line", repr(margin))
    check("reflow keeps a real tab indent on the first line",
          text.reflow("\n\n\tIndented start") == "\tIndented start",
          repr(text.reflow("\n\n\tIndented start")))

    check("cp437 encode", text.encode("caf\u00e9", 0) == b"caf\x82")
    check("cp1252 encode", text.encode("caf\u00e9", 16) == b"caf\xe9")
    check("unmappable becomes '?'", text.encode("\u4e2d", 0) == b"?")
    check("smart quotes substitute", text.encode("\u201chi\u201d", 0) == b'"hi"')

    two = job.Options(page_size="X80mm", EndOfJobCut="full")
    two["_copies"] = "2"
    dbl = job.build_text_job(two, "X")
    check("copies emit two cuts", hexof(dbl).count("1d5641") == 2,
          str(hexof(dbl).count("1d5641")))


def test_uris():
    print("\n== URI parsing ==")
    cases = [
        ("tmt70://04b8:0202", ("usb", None)),
        ("tmt70://04b8:0202/X5K123", ("usb", "X5K123")),
        ("usb://04b8:0202", ("usb", None)),
        ("", ("usb", None)),
        ("socket://10.0.0.5:9100", ("socket", ("10.0.0.5", 9100))),
        ("socket://10.0.0.5", ("socket", ("10.0.0.5", 9100))),
        ("tcp://printer:9101", ("socket", ("printer", 9101))),
        ("file://C:\\tmp\\out.bin", ("file", "C:\\tmp\\out.bin")),
        ("printer://EPSON TM-T70II", ("printer", "EPSON TM-T70II")),
        ("TM-T70II", ("printer", "TM-T70II")),
        ("C:\\tmp\\out.bin", ("file", "C:\\tmp\\out.bin")),
    ]
    for uri, want in cases:
        got = sinks.parse_uri(uri)
        check(f"uri {uri!r}", got == want, f"got {got}, want {want}")


def test_file_sink(tmpdir):
    print("\n== file sink ==")
    path = os.path.join(tmpdir, "out.bin")
    rc = sinks.send(f"file://{path}", b"\x1b@hello")
    check("file sink returns OK", rc == 0, str(rc))
    check("file sink wrote the bytes",
          os.path.isfile(path) and open(path, "rb").read() == b"\x1b@hello")


def test_cli(tmpdir):
    print("\n== cli ==")
    from tmt70win import cli
    src = os.path.join(tmpdir, "receipt.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("TOTAL 10.25\n")
    out = os.path.join(tmpdir, "cli.bin")
    rc = cli.main([src, "--dry-run", "-o", out, "--density", "dark",
                   "--cut", "full", "--media", "X58mm"])
    check("cli --dry-run exits 0", rc == 0, str(rc))
    data = open(out, "rb").read()
    check("cli produced ESC/POS", data.startswith(b"\x1b@"))
    check("cli applied --density dark", "1d284b02003106" in hexof(data))
    check("cli applied --cut full", "1d5641" in hexof(data))
    check("cli text present", b"TOTAL 10.25" in data)

    rc = cli.main(["--show-config"])
    check("cli --show-config exits 0", rc == 0, str(rc))

    missing = cli.main([os.path.join(tmpdir, "nope.txt"), "--dry-run",
                        "-o", os.path.join(tmpdir, "x.bin")])
    check("cli reports a missing file", missing == 1, str(missing))


def test_linux_untouched():
    print("\n== linux tree untouched ==")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(["git", "status", "--porcelain",
                              "src", "debian", "ppd", "udev", "Makefile"],
                             cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  skip git check ({e})")
        return
    dirty = [ln for ln in out.stdout.splitlines() if ln.strip()]
    check("no changes under src/ debian/ ppd/ udev/ Makefile", not dirty,
          "; ".join(dirty))


def main():
    import tempfile
    print(f"tmt70print selftest -- python {sys.version.split()[0]} on {sys.platform}")
    with tempfile.TemporaryDirectory() as tmp, quiet():
        test_shared_sources()
        test_parity()
        test_escpos_assertions()
        test_image_decoders()
        test_dither()
        test_text()
        test_uris()
        test_file_sink(tmp)
        test_cli(tmp)
        test_linux_untouched()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  FAILED: {name}")
        return 1
    print("ALL WINDOWS SELFTESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

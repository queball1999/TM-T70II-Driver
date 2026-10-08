"""
job -- turn an input file plus options into an ESC/POS byte stream.

SPDX-License-Identifier: GPL-2.0-or-later

Two paths:

  image  ->  Gray -> 1bpp -> synthetic CUPS raster v3 -> filter.build()
             This is the *same* code the .deb runs, so the emitted bytes are
             identical to what Linux produces for the same bitmap and options.

  text   ->  filter.EscposOut framing + resident-font characters (see text.py).
             Same init / density / speed / margin / cut commands as build();
             only the page body differs.
"""

import io
import struct

from . import core, images, text

__all__ = ["Options", "build_job", "raster_stream", "PAGE_SIZES"]

# PPD PageSize keyword -> printable dots (mirrors ImageableArea in the PPD)
PAGE_SIZES = {
    "x80mm": 510,
    "x80mmlong": 510,
    "x58mm": 360,
}


def page_dots(name):
    return PAGE_SIZES.get(str(name).lower(), PAGE_SIZES["x80mm"])


# --------------------------------------------------------------------------- #
# options
# --------------------------------------------------------------------------- #

class Options(dict):
    """Filter options, defaulted from the shared DEFAULTS table."""

    def __init__(self, page_size="X80mm", **over):
        f = core.filter_mod()
        super().__init__(f.DEFAULTS)
        self["PageSizeDots"] = str(page_dots(page_size))
        self.page_size = page_size
        for k, v in over.items():
            if v is not None:
                self[f._canon(k)] = str(v)

    def update_optstring(self, s):
        """Merge a CUPS-style 'Key=Value Key=Value' string."""
        if s:
            self.update(core.filter_mod().parse_opt_string(s))
        return self

    @property
    def copies(self):
        try:
            return max(1, int(self.get("_copies", 1)))
        except (TypeError, ValueError):
            return 1


# --------------------------------------------------------------------------- #
# image -> CUPS raster v3
# --------------------------------------------------------------------------- #

def raster_stream(bitmap, w, h, page_size="X80mm"):
    """Wrap a packed 1bpp bitmap as a one-page CUPS raster v3 stream.

    Header offsets come from tools/mkraster.py so there is one definition of
    the layout shared with the Linux self-test.
    """
    mk = core.mkraster_mod()
    hdr = bytearray(mk.HEADER_LEN)

    def u32(name, val):
        struct.pack_into(mk.BE + "I", hdr, mk.OFF[name], val)

    u32("HWResolution0", 180)
    u32("HWResolution1", 180)
    u32("NumCopies", 1)
    u32("cupsWidth", w)
    u32("cupsHeight", h)
    u32("cupsBitsPerColor", 1)
    u32("cupsBitsPerPixel", 1)
    u32("cupsBytesPerLine", (w + 7) // 8)
    u32("cupsColorOrder", 0)
    u32("cupsColorSpace", 3)                    # CUPS_CSPACE_K, bit set = black
    u32("cupsCompression", 0)
    u32("cupsNumColors", 1)

    name = str(page_size).encode("ascii", "replace")[:63]
    hdr[1732:1732 + len(name)] = name

    return b"RaS3" + bytes(hdr) + bitmap


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #

def build_image_job(opts, data, dither=True, threshold=128, invert=False,
                    scale=True):
    """Decode an image and run it through the unmodified Linux filter."""
    f = core.filter_mod()
    img = images.decode(data)
    if invert:
        img = img.invert()
    max_dots = min(f.MAX_DOTS, page_dots(opts.page_size))
    if scale:
        img = img.fit_width(max_dots)
    bitmap, w, h = img.to_bitmap(dither=dither, threshold=threshold)

    stream = raster_stream(bitmap, w, h, opts.page_size)
    out = io.BytesIO()
    f.build(opts, io.BytesIO(stream), out)
    return out.getvalue()


def build_text_job(opts, body, align="left", cols=None, tabstop=4,
                   wrap_long=True, reflow_lines=False):
    """Emit resident-font text with the same framing build() uses."""
    f = core.filter_mod()
    out = io.BytesIO()

    left_dots = int(round(f.as_num(opts["LeftMargin"]) * f.MM))
    area_dots = int(f.as_num(opts["PageSizeDots"], f.MAX_DOTS))
    cut_mode = opts["EndOfJobCut"].lower()
    feed_cut = int(round(f.as_num(opts["FeedBeforeCut"], 3) * f.MM))
    codepage = int(f.as_num(opts["CodePage"], 0))

    eo = f.EscposOut(out)
    eo.init(codepage)
    eo.density(opts["Density"].lower())
    eo.speed(opts["PrintSpeed"].lower())
    eo.print_area(min(f.MAX_DOTS, left_dots + area_dots))
    eo.left_margin(left_dots)

    remaining = opts.copies
    while remaining > 0:
        remaining -= 1
        text.render(eo, body, codepage=codepage, page_size=opts.page_size,
                    cols=cols, align=align, tabstop=tabstop,
                    wrap_long=wrap_long, reflow_lines=reflow_lines)
        if remaining > 0:
            eo.feed_dots(24)
            eo.cut(cut_mode, feed_cut)

    eo.cut(cut_mode, feed_cut)
    out.flush()
    return out.getvalue()


def looks_like_image(data):
    return (data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"BM")
            or data[:2] in (b"P1", b"P2", b"P3", b"P4", b"P5", b"P6"))


def build_job(opts, data, kind="auto", encoding="utf-8", **kw):
    """Dispatch on content: 'image', 'text', or 'auto' to sniff.

    Returns the complete ESC/POS byte stream.
    """
    if kind == "auto":
        kind = "image" if looks_like_image(data) else "text"

    if kind == "image":
        return build_image_job(
            opts, data,
            dither=kw.get("dither", True),
            threshold=kw.get("threshold", 128),
            invert=kw.get("invert", False),
            scale=kw.get("scale", True),
        )

    if kind == "escpos":                        # already ESC/POS: pass through
        return data

    body = data.decode(encoding, errors="replace") if isinstance(data, (bytes, bytearray)) else data
    return build_text_job(
        opts, body,
        align=kw.get("align", "left"),
        cols=kw.get("cols"),
        tabstop=kw.get("tabstop", 4),
        wrap_long=kw.get("wrap", True),
        reflow_lines=kw.get("reflow", False),
    )

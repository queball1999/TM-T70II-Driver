"""
text -- render plain text as native ESC/POS characters.

SPDX-License-Identifier: GPL-2.0-or-later

The Linux path rasterises everything through Ghostscript, so a text document
arrives at the filter as a bitmap.  There is no Ghostscript here, and for a
receipt printer there does not need to be: the TM-T70II has resident fonts and
prints them faster and more crisply than a dithered bitmap of the same text.

Consequence, documented in docs/WINDOWS.md: text output is set in the printer's
own font rather than the document's.  Image input keeps byte-for-byte parity
with Linux (see job.py); text does not, by design.
"""

__all__ = ["CODEPAGES", "MEDIA", "columns_for", "encode", "reflow", "render"]

ESC = b"\x1b"
GS = b"\x1d"

# ESC t n  ->  Python codec.  Matches the CodePage choices in the PPD.
CODEPAGES = {
    0: "cp437",         # PC437 USA / Standard Europe
    2: "cp850",         # Multilingual
    16: "cp1252",       # Windows-1252
    19: "cp858",        # Euro
}

# printable dots and resident font A columns (font A cell is 12 dots wide)
MEDIA = {
    "x80mm": (512, 42),
    "x80mmlong": (512, 42),
    "x58mm": (360, 30),
}

ALIGN = {"left": 0, "centre": 1, "center": 1, "right": 2}


def columns_for(page_size, override=None):
    """Character columns for a PPD PageSize keyword."""
    if override:
        return max(8, int(override))
    return MEDIA.get(str(page_size).lower(), MEDIA["x80mm"])[1]


def dots_for(page_size):
    return MEDIA.get(str(page_size).lower(), MEDIA["x80mm"])[0]


def encode(s, codepage=0):
    """Encode to the printer's code page, substituting '?' for what it lacks."""
    codec = CODEPAGES.get(int(codepage), "cp437")
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode(codec)
        except UnicodeEncodeError:
            out += _fallback(ch, codec)
    return bytes(out)


_SUBSTITUTES = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "--", "…": "...", " ": " ",
    "•": "*", "−": "-", "×": "x",
}


def _fallback(ch, codec):
    alt = _SUBSTITUTES.get(ch)
    if alt:
        try:
            return alt.encode(codec)
        except UnicodeEncodeError:
            pass
    return b"?"


def wrap(line, cols, tabstop=4):
    """Hard-wrap one logical line to cols characters, breaking on spaces.

    Indentation (if any) marks a first-line paragraph indent, not a hanging
    indent: it's kept on the first physical line only, matching standard
    printed-prose convention. This assumes `line` is already one real
    paragraph -- if the source pre-wrapped at a different width (e.g. text
    printed via a GDI driver), reflow() it first so a paragraph isn't split
    across several "logical" lines before it ever reaches wrap().
    """
    line = line.replace("\t", " " * tabstop).rstrip()
    if not line:
        return [""]
    out = []
    while len(line) > cols:
        cut = line.rfind(" ", 0, cols + 1)
        if cut <= 0:
            cut = cols                      # no space to break on: hard cut
        out.append(line[:cut].rstrip())
        line = line[cut:].lstrip()
    out.append(line)
    return out


def _join_paragraph(lines):
    """Join a run of source lines into one, keeping only the first line's
    leading indentation (its tab/spaces mark the start of the paragraph)."""
    first = lines[0]
    indent_len = len(first) - len(first.lstrip(" \t"))
    indent = first[:indent_len]
    words = " ".join(l.strip() for l in lines)
    return indent + words


def reflow(body):
    """Rejoin consecutive non-blank source lines into paragraphs, keeping
    blank lines as real paragraph breaks.

    Apps that print plain text through a GDI-rendering driver (e.g. Notepad
    via "Generic / Text Only") pre-wrap the content at *their own*, usually
    much wider, column width -- each visual line arrives here as its own
    "logical" line. Left alone, wrap() then treats every one of those as a
    separate paragraph, fragmenting continuous prose at whatever point the
    source happened to wrap, not ours. Reflowing first (opt-in: it would
    wrongly join genuinely-meaningful line breaks in preformatted text, e.g.
    an itemized list) undoes that, matching how Markdown/`fmt` treat a
    single newline as a soft break and a blank line as a hard one.

    That same GDI path also always emits a fixed top/left page margin as
    literal characters -- confirmed on "Generic / Text Only": several blank
    lines followed by a run of plain spaces before the real first line
    (e.g. b"\\r\\n\\n\\n\\n\\n\\n       Alyssa,"). Nothing the document
    actually contains, so strip it before it can be mistaken for an
    intentional leading blank line or a first-line paragraph indent.
    """
    body = body.replace("\r\n", "\n").replace("\r", "\n")

    lines = body.split("\n")
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i < len(lines):
        lines[i] = lines[i].lstrip(" ")     # leading spaces only -- a real tab is a real indent
    body = "\n".join(lines[i:])

    paragraphs = []
    current = []
    for raw in body.split("\n"):
        if raw.strip() == "":
            if current:
                paragraphs.append(_join_paragraph(current))
                current = []
            paragraphs.append("")
        else:
            current.append(raw)
    if current:
        paragraphs.append(_join_paragraph(current))
    return "\n".join(paragraphs)


def render(eo, body, codepage=0, page_size="x80mm", cols=None,
           align="left", tabstop=4, wrap_long=True, reflow_lines=False):
    """Emit `body` through an EscposOut, honouring the code page and width.

    eo   -- filter.EscposOut, already init()ed by the caller
    body -- str
    """
    ncols = columns_for(page_size, cols)
    a = ALIGN.get(str(align).lower(), 0)
    if a:
        eo.raw(ESC + b"a" + bytes([a]))

    if reflow_lines:
        body = reflow(body)

    for logical in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        lines = wrap(logical, ncols, tabstop) if wrap_long else [logical]
        for physical in lines:
            eo.raw(encode(physical, codepage))
            eo.raw(b"\n")

    if a:
        eo.raw(ESC + b"a" + bytes([0]))

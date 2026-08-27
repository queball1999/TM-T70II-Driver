#!/usr/bin/env python3
"""
escpos_dis.py -- a disassembler for Epson ESC/POS command streams.

Reads a raw print stream (e.g. a .prn captured from the Windows Advanced Printer
Driver, or a dry-run dump from this project's backend) and prints a human
readable listing of every command, with raster/graphics payloads summarised
rather than dumped byte-for-byte.

Usage:
    python3 escpos_dis.py CAPTURE.prn
    python3 escpos_dis.py --raster-ascii CAPTURE.prn      # also draw bitmaps
    python3 escpos_dis.py --max-raster 40 CAPTURE.prn
    cat dump.bin | python3 escpos_dis.py -

Scope: the commands the TM-T70II actually understands (see
docs links in docs/PROTOCOL.md). Unknown bytes are reported as raw data runs so
nothing is silently swallowed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# byte helpers
# --------------------------------------------------------------------------- #

def h(b: int) -> str:
    return f"{b:02X}"


def hexrun(data: bytes, limit: int = 16) -> str:
    out = " ".join(h(x) for x in data[:limit])
    if len(data) > limit:
        out += f" ... (+{len(data) - limit} bytes)"
    return out


def ascii_run(data: bytes, limit: int = 64) -> str:
    s = "".join(chr(x) if 32 <= x < 127 else "." for x in data[:limit])
    if len(data) > limit:
        s += f"...(+{len(data) - limit})"
    return s


# --------------------------------------------------------------------------- #
# parser state
# --------------------------------------------------------------------------- #

@dataclass
class Stats:
    commands: int = 0
    text_bytes: int = 0
    raster_blocks: int = 0
    raster_rows: int = 0
    raster_bytes: int = 0
    cuts: int = 0
    density_cmds: list = field(default_factory=list)
    speed_cmds: list = field(default_factory=list)
    codepages: list = field(default_factory=list)
    notes: list = field(default_factory=list)


class Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def eof(self) -> bool:
        return self.i >= len(self.d)

    def peek(self, n: int = 1) -> bytes:
        return self.d[self.i:self.i + n]

    def take(self, n: int = 1) -> bytes:
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def u8(self) -> int:
        v = self.d[self.i]
        self.i += 1
        return v

    def u16le(self) -> int:
        v = self.d[self.i] | (self.d[self.i + 1] << 8)
        self.i += 2
        return v


# --------------------------------------------------------------------------- #
# rendering of raster payloads
# --------------------------------------------------------------------------- #

def render_bitmap(rows: list[bytes], width_bits: int, max_rows: int) -> list[str]:
    lines = []
    step = max(1, len(rows) // max_rows) if max_rows else 1
    for r in range(0, len(rows), step):
        row = rows[r]
        chars = []
        for x in range(width_bits):
            byte = row[x >> 3] if (x >> 3) < len(row) else 0
            bit = (byte >> (7 - (x & 7))) & 1
            chars.append("#" if bit else " ")
        lines.append("".join(chars).rstrip())
    return lines


# --------------------------------------------------------------------------- #
# main disassembly
# --------------------------------------------------------------------------- #

CUT_FN = {
    0: "full cut", 1: "partial cut",
    48: "full cut", 49: "partial cut",
    65: "feed n, full cut", 66: "feed n, partial cut",
    103: "feed to cut pos + cut", 104: "feed to cut pos + cut",
}


def disassemble(data: bytes, args) -> Stats:
    st = Stats()
    r = Reader(data)
    pending_text = bytearray()

    def flush_text():
        if pending_text:
            st.text_bytes += len(pending_text)
            print(f"        TEXT  {len(pending_text):5d}  |{ascii_run(bytes(pending_text))}|")
            pending_text.clear()

    def emit(off: int, name: str, detail: str = ""):
        st.commands += 1
        print(f"@{off:08X} {name:<22} {detail}")

    while not r.eof():
        off = r.i
        b = r.u8()

        # ---- ESC ---------------------------------------------------------- #
        if b == 0x1B:
            if r.eof():
                flush_text(); emit(off, "ESC <trailing>"); break
            c = r.u8()
            if c == 0x40:
                flush_text(); emit(off, "ESC @", "initialize printer")
            elif c == 0x74:                       # ESC t n  -- code page
                n = r.u8(); flush_text()
                emit(off, "ESC t", f"select character code table = {n}")
                st.codepages.append(n)
            elif c == 0x7B:                       # ESC { n  -- upside-down
                n = r.u8(); flush_text()
                emit(off, "ESC { (upside-down)", f"n={n} -> {'ON' if n & 1 else 'off'}")
                st.notes.append(f"upside-down mode n={n}")
            elif c == 0x32:                       # ESC 2 -- default line spacing
                flush_text(); emit(off, "ESC 2", "line spacing = default (~30 dots)")
            elif c == 0x33:                       # ESC 3 n -- set line spacing
                n = r.u8(); flush_text()
                emit(off, "ESC 3", f"line spacing = {n}/180 in")
            elif c == 0x4A:                       # ESC J n -- feed n dots
                n = r.u8(); flush_text()
                emit(off, "ESC J", f"print & feed {n} dots")
            elif c == 0x64:                       # ESC d n -- feed n lines
                n = r.u8(); flush_text()
                emit(off, "ESC d", f"print & feed {n} lines")
            elif c == 0x61:                       # ESC a n -- justification
                n = r.u8(); flush_text()
                emit(off, "ESC a", f"justification = {['left','center','right'][n] if n < 3 else n}")
            elif c == 0x21:                       # ESC ! n -- print mode
                n = r.u8(); flush_text()
                emit(off, "ESC !", f"print mode bitmask = 0x{n:02X}")
            elif c == 0x2D:                       # ESC - n -- underline
                n = r.u8(); flush_text(); emit(off, "ESC -", f"underline = {n}")
            elif c == 0x45:                       # ESC E n -- bold
                n = r.u8(); flush_text(); emit(off, "ESC E", f"bold = {n}")
            elif c == 0x47:                       # ESC G n -- double-strike
                n = r.u8(); flush_text(); emit(off, "ESC G", f"double-strike = {n}")
            elif c == 0x4D:                       # ESC M n -- font
                n = r.u8(); flush_text(); emit(off, "ESC M", f"font = {'A' if n in (0,48) else 'B' if n in (1,49) else n}")
            elif c == 0x52:                       # ESC R n -- intl charset
                n = r.u8(); flush_text(); emit(off, "ESC R", f"international charset = {n}")
            elif c == 0x2A:                       # ESC * m nL nH  -- bit image
                m = r.u8(); nL = r.u8(); nH = r.u8()
                width = nL | (nH << 8)
                bytes_per_col = 1 if m in (0, 1) else 3
                payload = r.take(width * bytes_per_col)
                flush_text()
                emit(off, "ESC * (bit image)", f"mode={m} width={width} cols -> {len(payload)} bytes")
                st.raster_blocks += 1
                st.raster_bytes += len(payload)
            elif c == 0x33:
                pass
            else:
                flush_text()
                emit(off, "ESC ?", f"unhandled ESC {h(c)}")
            continue

        # ---- GS ---------------------------------------------------------- #
        if b == 0x1D:
            if r.eof():
                flush_text(); emit(off, "GS <trailing>"); break
            c = r.u8()

            if c == 0x56:                        # GS V -- cut
                m = r.u8()
                extra = ""
                if m in (65, 66, 103, 104):
                    n = r.u8(); extra = f" n={n}"
                flush_text()
                emit(off, "GS V (cut)", f"m={m} ({CUT_FN.get(m, '?')}){extra}")
                st.cuts += 1

            elif c == 0x76 and r.peek(1) == b"\x30":   # GS v 0 -- raster bit image
                r.u8()                                  # the '0'
                mode = r.u8()
                xL = r.u8(); xH = r.u8()
                yL = r.u8(); yH = r.u8()
                xbytes = xL | (xH << 8)
                ylines = yL | (yH << 8)
                payload = r.take(xbytes * ylines)
                flush_text()
                emit(off, "GS v 0 (raster)",
                     f"mode={mode} width={xbytes*8}px x {ylines}px -> {len(payload)} bytes")
                st.raster_blocks += 1
                st.raster_rows += ylines
                st.raster_bytes += len(payload)
                if args.raster_ascii:
                    rows = [payload[k*xbytes:(k+1)*xbytes] for k in range(ylines)]
                    for ln in render_bitmap(rows, xbytes * 8, args.max_raster):
                        print(f"    | {ln}")

            elif c == 0x28:                      # GS ( ...  -- grouped
                fn_letter = r.u8()
                pL = r.u8(); pH = r.u8()
                plen = pL | (pH << 8)
                params = r.take(plen)
                flush_text()
                label = {
                    0x41: "GS ( A  (test print)",
                    0x43: "GS ( C  (counter)",
                    0x45: "GS ( E  (user setup / mem switch)",
                    0x4B: "GS ( K  (print control: density/speed)",
                    0x4C: "GS ( L  (graphics data)",
                    0x38: "GS 8 L  (large graphics data)",
                    0x48: "GS ( H  (request response)",
                    0x50: "GS ( P  (paper layout)",
                }.get(fn_letter, f"GS ( {h(fn_letter)}")
                sub = params[0] if params else None
                emit(off, label, f"fn={sub} len={plen} params=[{hexrun(params)}]")
                if fn_letter == 0x45:
                    st.density_cmds.append(("GS ( E", list(params)))
                if fn_letter == 0x4B:
                    # fn 48/49 = print density, 50 = print speed (per Epson refs)
                    if sub in (48, 49):
                        st.density_cmds.append(("GS ( K", list(params)))
                    elif sub == 50:
                        st.speed_cmds.append(list(params))
                if fn_letter in (0x4C, 0x38):
                    st.raster_blocks += 1
                    st.raster_bytes += plen

            elif c == 0x38 and r.peek(1) == b"\x4C":   # GS 8 L nP1..nP4 ...
                r.u8()                                   # 'L'
                p1 = r.u8(); p2 = r.u8(); p3 = r.u8(); p4 = r.u8()
                plen = p1 | (p2 << 8) | (p3 << 16) | (p4 << 24)
                params = r.take(plen)
                flush_text()
                emit(off, "GS 8 L (large graphics)",
                     f"len={plen} head=[{hexrun(params, 12)}]")
                st.raster_blocks += 1
                st.raster_bytes += plen

            elif c == 0x4C:                      # GS L nL nH -- left margin
                v = r.u16le(); flush_text()
                emit(off, "GS L (left margin)", f"{v} dots ({v/8:.1f} mm @203) ({v/7.09:.1f} mm @180)")
                st.notes.append(f"left margin = {v} dots")

            elif c == 0x57:                      # GS W nL nH -- print area width
                v = r.u16le(); flush_text()
                emit(off, "GS W (print area width)", f"{v} dots")
                st.notes.append(f"print area width = {v} dots")

            elif c == 0x21:                      # GS ! n -- char size
                n = r.u8(); flush_text()
                emit(off, "GS !", f"char size = 0x{n:02X}")

            elif c == 0x42:                      # GS B n -- reverse
                n = r.u8(); flush_text(); emit(off, "GS B", f"reverse video = {n}")

            elif c == 0x72:                      # GS r n -- transmit status
                n = r.u8(); flush_text(); emit(off, "GS r", f"transmit status n={n}")

            elif c == 0x61:                      # GS a n -- ASB
                n = r.u8(); flush_text(); emit(off, "GS a", f"auto status back = 0x{n:02X}")

            elif c == 0x66:                      # GS f n -- HRI font
                n = r.u8(); flush_text(); emit(off, "GS f", f"HRI font = {n}")

            elif c == 0x48:                      # GS H n -- HRI position
                n = r.u8(); flush_text(); emit(off, "GS H", f"HRI position = {n}")

            elif c == 0x77:                      # GS w n -- barcode width
                n = r.u8(); flush_text(); emit(off, "GS w", f"barcode module width = {n}")

            elif c == 0x68:                      # GS h n -- barcode height
                n = r.u8(); flush_text(); emit(off, "GS h", f"barcode height = {n}")

            elif c == 0x6B:                      # GS k -- print barcode
                m = r.u8(); flush_text()
                if m <= 6:
                    payload = bytearray()
                    while not r.eof() and r.peek(1) != b"\x00":
                        payload += r.take(1)
                    if not r.eof():
                        r.u8()
                else:
                    n = r.u8(); payload = r.take(n)
                emit(off, "GS k (barcode)", f"m={m} data={ascii_run(bytes(payload), 32)}")

            else:
                flush_text()
                emit(off, "GS ?", f"unhandled GS {h(c)}")
            continue

        # ---- FS (2-byte charset etc.) ---------------------------------- #
        if b == 0x1C:
            if r.eof():
                flush_text(); emit(off, "FS <trailing>"); break
            c = r.u8()
            if c == 0x2E:
                flush_text(); emit(off, "FS .", "cancel Kanji mode")
            elif c == 0x26:
                flush_text(); emit(off, "FS &", "select Kanji mode")
            elif c == 0x43:
                n = r.u8(); flush_text(); emit(off, "FS C", f"Kanji code system = {n}")
            elif c == 0x21:
                n = r.u8(); flush_text(); emit(off, "FS !", f"Kanji print mode = 0x{n:02X}")
            elif c == 0x70:                      # FS p -- print NV bit image
                n = r.u8(); m = r.u8(); flush_text()
                emit(off, "FS p (NV logo, obsolete)", f"n={n} m={m}")
            elif c == 0x71:                      # FS q -- define NV bit image
                n = r.u8(); flush_text()
                emit(off, "FS q (define NV logo)", f"n={n} images (payload skipped by parser)")
                st.notes.append("stream defines NV logo(s) with FS q")
                break
            else:
                flush_text(); emit(off, "FS ?", f"unhandled FS {h(c)}")
            continue

        # ---- DLE (real-time) ----------------------------------------- #
        if b == 0x10:
            if r.eof():
                flush_text(); emit(off, "DLE <trailing>"); break
            c = r.u8()
            if c == 0x04:
                n = r.u8(); flush_text(); emit(off, "DLE EOT", f"real-time status n={n}")
            elif c == 0x05:
                n = r.u8(); flush_text(); emit(off, "DLE ENQ", f"real-time request n={n}")
            elif c == 0x14:
                a = r.u8(); b2 = r.u8(); c2 = r.u8(); flush_text()
                emit(off, "DLE DC4", f"real-time generate pulse/power {a} {b2} {c2}")
            else:
                flush_text(); emit(off, "DLE ?", f"unhandled DLE {h(c)}")
            continue

        # ---- control / whitespace ----------------------------------- #
        if b == 0x0A:
            flush_text(); emit(off, "LF", "print & line feed")
            continue
        if b == 0x0D:
            flush_text(); emit(off, "CR")
            continue
        if b == 0x0C:
            flush_text(); emit(off, "FF", "form feed / print page mode")
            continue
        if b == 0x09:
            flush_text(); emit(off, "HT", "horizontal tab")
            continue
        if b == 0x00:
            flush_text(); emit(off, "NUL")
            continue

        # ---- plain data ------------------------------------------------ #
        pending_text.append(b)

    flush_text()
    return st


def print_summary(st: Stats):
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  commands parsed     : {st.commands}")
    print(f"  text bytes          : {st.text_bytes}")
    print(f"  raster/gfx blocks   : {st.raster_blocks}")
    print(f"  raster rows (GS v 0): {st.raster_rows}")
    print(f"  raster/gfx bytes    : {st.raster_bytes}")
    print(f"  cut commands        : {st.cuts}")
    if st.density_cmds:
        print(f"  density commands    :")
        for name, p in st.density_cmds:
            print(f"      {name}  {' '.join(h(x) for x in p)}")
    if st.speed_cmds:
        print(f"  speed commands      :")
        for p in st.speed_cmds:
            print(f"      GS ( K  {' '.join(h(x) for x in p)}")
    if st.codepages:
        print(f"  code pages selected : {st.codepages}")
    if st.notes:
        print("  notes:")
        for n in st.notes:
            print(f"      - {n}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Disassemble an ESC/POS print stream")
    ap.add_argument("file", help="input .prn / dump, or - for stdin")
    ap.add_argument("--raster-ascii", action="store_true",
                    help="draw GS v 0 raster payloads as ASCII art")
    ap.add_argument("--max-raster", type=int, default=60,
                    help="max rows to draw per raster block (default 60)")
    args = ap.parse_args(argv)

    if args.file == "-":
        data = sys.stdin.buffer.read()
    else:
        with open(args.file, "rb") as fh:
            data = fh.read()

    print(f"# {args.file}: {len(data)} bytes\n")
    st = disassemble(data, args)
    print_summary(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

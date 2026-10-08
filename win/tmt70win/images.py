"""
images -- decode PNG / BMP / PNM to greyscale, then dither to 1bpp.

SPDX-License-Identifier: GPL-2.0-or-later

Standard library only (zlib does the PNG inflate).  No Pillow, no .NET, no
native dependency of any kind.

The output is the same packed format the CUPS raster path produces and that
EscposOut.raster_band() consumes: rows of ceil(w/8) bytes, MSB first, bit set
means black.
"""

import struct
import zlib

__all__ = ["Gray", "decode", "load", "SUPPORTED"]

SUPPORTED = (".png", ".bmp", ".pbm", ".pgm", ".ppm", ".pnm")


class ImageError(RuntimeError):
    pass


class Gray:
    """8-bit greyscale image. pix[y*w + x], 0 = black, 255 = white."""

    __slots__ = ("w", "h", "pix")

    def __init__(self, w, h, pix):
        self.w = w
        self.h = h
        self.pix = pix

    # -- geometry ---------------------------------------------------------- #

    def scale_to_width(self, target_w):
        """Nearest-neighbour scale so the image is exactly target_w wide.

        Nearest-neighbour rather than an averaging filter because the result is
        about to be dithered to one bit; smoothing first only muddies the
        threshold decisions.
        """
        if target_w == self.w or target_w <= 0:
            return self
        target_h = max(1, int(round(self.h * target_w / float(self.w))))
        src, w = self.pix, self.w
        out = bytearray(target_w * target_h)
        for y in range(target_h):
            sy = min(self.h - 1, y * self.h // target_h)
            base, obase = sy * w, y * target_w
            for x in range(target_w):
                out[obase + x] = src[base + (x * w // target_w)]
        return Gray(target_w, target_h, out)

    def fit_width(self, max_w):
        """Scale down to max_w if wider; never scale up."""
        return self.scale_to_width(max_w) if self.w > max_w else self

    def invert(self):
        return Gray(self.w, self.h, bytes(255 - v for v in self.pix))

    # -- 1bpp -------------------------------------------------------------- #

    def to_bitmap(self, dither=True, threshold=128):
        """Pack to ceil(w/8) bytes per row, MSB first, 1 = black."""
        w, h = self.w, self.h
        rb = (w + 7) // 8
        out = bytearray(rb * h)
        if not dither:
            pix = self.pix
            for y in range(h):
                base, obase = y * w, y * rb
                for x in range(w):
                    if pix[base + x] < threshold:
                        out[obase + (x >> 3)] |= 0x80 >> (x & 7)
            return bytes(out), w, h

        # Floyd-Steinberg over a mutable int buffer (errors go out of 0..255)
        buf = [self.pix[i] for i in range(w * h)]
        for y in range(h):
            base, obase = y * w, y * rb
            nxt = base + w
            for x in range(w):
                old = buf[base + x]
                new = 255 if old >= threshold else 0
                if new == 0:
                    out[obase + (x >> 3)] |= 0x80 >> (x & 7)
                err = old - new
                if err:
                    if x + 1 < w:
                        buf[base + x + 1] += (err * 7) >> 4
                    if y + 1 < h:
                        if x:
                            buf[nxt + x - 1] += (err * 3) >> 4
                        buf[nxt + x] += (err * 5) >> 4
                        if x + 1 < w:
                            buf[nxt + x + 1] += err >> 4
        return bytes(out), w, h


# --------------------------------------------------------------------------- #
# PNG
# --------------------------------------------------------------------------- #

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_chunks(data):
    pos = len(_PNG_MAGIC)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        yield ctype, body
        pos += 12 + length              # length + type + body + crc


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _png_unfilter(raw, w, h, bpp, stride):
    """Undo the per-scanline PNG filters. Returns h*stride bytes."""
    out = bytearray(h * stride)
    pos = 0
    prev = bytearray(stride)
    for y in range(h):
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ft == 0:
            pass
        elif ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        else:
            raise ImageError(f"PNG: unknown filter type {ft}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def _unpack_bits(line, depth, count):
    """Expand sub-byte samples to one value per sample."""
    vals = []
    per = 8 // depth
    mask = (1 << depth) - 1
    for i in range(count):
        b = line[i // per]
        shift = 8 - depth * (i % per + 1)
        vals.append((b >> shift) & mask)
    return vals


def decode_png(data):
    ihdr = None
    palette = b""
    trns = b""
    idat = bytearray()
    for ctype, body in _png_chunks(data):
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body[:13])
        elif ctype == b"PLTE":
            palette = body
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
    if ihdr is None:
        raise ImageError("PNG: no IHDR")
    w, h, depth, ctype_n, comp, filt, interlace = ihdr
    if interlace:
        raise ImageError("PNG: interlaced images are not supported "
                         "(re-save without Adam7 interlacing)")
    if comp != 0 or filt != 0:
        raise ImageError("PNG: unsupported compression/filter method")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype_n)
    if channels is None:
        raise ImageError(f"PNG: unsupported colour type {ctype_n}")
    if depth not in (1, 2, 4, 8, 16):
        raise ImageError(f"PNG: unsupported bit depth {depth}")
    if depth == 16 and ctype_n == 3:
        raise ImageError("PNG: 16-bit palette is not valid")

    bits_per_pixel = channels * depth
    stride = (w * bits_per_pixel + 7) // 8
    bpp = max(1, bits_per_pixel // 8)
    raw = zlib.decompress(bytes(idat))
    if len(raw) < h * (stride + 1):
        raise ImageError("PNG: truncated image data")
    planes = _png_unfilter(raw, w, h, bpp, stride)

    maxval = (1 << depth) - 1
    out = bytearray(w * h)
    for y in range(h):
        line = planes[y * stride:(y + 1) * stride]
        obase = y * w
        if depth == 16:
            # take the high byte of each 16-bit sample
            samples = [line[i] for i in range(0, len(line), 2)]
        elif depth == 8:
            samples = line
        else:
            samples = _unpack_bits(line, depth, w * channels)

        if ctype_n == 3:                                   # palette
            for x in range(w):
                idx = samples[x] * 3
                r, g, b = palette[idx], palette[idx + 1], palette[idx + 2]
                v = (r * 299 + g * 587 + b * 114) // 1000
                if trns and samples[x] < len(trns):        # composite on white
                    a = trns[samples[x]]
                    v = (v * a + 255 * (255 - a)) // 255
                out[obase + x] = v
        elif ctype_n in (0, 4):                            # grey [+ alpha]
            step = channels
            for x in range(w):
                v = samples[x * step]
                if depth < 8:
                    v = v * 255 // maxval
                if channels == 2:
                    a = samples[x * step + 1]
                    v = (v * a + 255 * (255 - a)) // 255
                out[obase + x] = v
        else:                                              # RGB [+ alpha]
            step = channels
            for x in range(w):
                r = samples[x * step]
                g = samples[x * step + 1]
                b = samples[x * step + 2]
                v = (r * 299 + g * 587 + b * 114) // 1000
                if channels == 4:
                    a = samples[x * step + 3]
                    v = (v * a + 255 * (255 - a)) // 255
                out[obase + x] = v
    return Gray(w, h, bytes(out))


# --------------------------------------------------------------------------- #
# BMP
# --------------------------------------------------------------------------- #

def decode_bmp(data):
    if data[:2] != b"BM":
        raise ImageError("BMP: bad signature")
    (offset,) = struct.unpack("<I", data[10:14])
    (hdr_size,) = struct.unpack("<I", data[14:18])
    if hdr_size < 40:
        raise ImageError("BMP: only BITMAPINFOHEADER and later are supported")
    w, h = struct.unpack("<ii", data[18:26])
    planes, depth = struct.unpack("<HH", data[26:30])
    (compression,) = struct.unpack("<I", data[30:34])
    (clr_used,) = struct.unpack("<I", data[46:50])
    if planes != 1 or compression != 0:
        raise ImageError("BMP: only uncompressed BI_RGB is supported")
    if depth not in (1, 4, 8, 24, 32):
        raise ImageError(f"BMP: unsupported bit depth {depth}")

    bottom_up = h > 0
    h = abs(h)
    stride = ((w * depth + 31) // 32) * 4

    palette = []
    if depth <= 8:
        n = clr_used or (1 << depth)
        pstart = 14 + hdr_size
        for i in range(n):
            b, g, r = data[pstart + i * 4:pstart + i * 4 + 3]
            palette.append((r * 299 + g * 587 + b * 114) // 1000)

    out = bytearray(w * h)
    for row in range(h):
        y = (h - 1 - row) if bottom_up else row
        line = data[offset + row * stride:offset + (row + 1) * stride]
        obase = y * w
        if depth == 24:
            for x in range(w):
                b, g, r = line[x * 3:x * 3 + 3]
                out[obase + x] = (r * 299 + g * 587 + b * 114) // 1000
        elif depth == 32:
            for x in range(w):
                b, g, r = line[x * 4:x * 4 + 3]
                out[obase + x] = (r * 299 + g * 587 + b * 114) // 1000
        else:
            for x, idx in enumerate(_unpack_bits(line, depth, w)):
                out[obase + x] = palette[idx] if idx < len(palette) else 0
    return Gray(w, h, bytes(out))


# --------------------------------------------------------------------------- #
# PNM (P1-P6)
# --------------------------------------------------------------------------- #

def _pnm_fields(data, start, count):
    vals, idx = [], start
    while len(vals) < count:
        while idx < len(data) and data[idx:idx + 1].isspace():
            idx += 1
        if data[idx:idx + 1] == b"#":
            while idx < len(data) and data[idx:idx + 1] != b"\n":
                idx += 1
            continue
        s = idx
        while idx < len(data) and not data[idx:idx + 1].isspace():
            idx += 1
        vals.append(int(data[s:idx]))
    return vals, idx + 1                        # skip the single trailing space


def decode_pnm(data):
    magic = data[:2]
    if magic not in (b"P1", b"P2", b"P3", b"P4", b"P5", b"P6"):
        raise ImageError("PNM: bad magic")
    need = 2 if magic in (b"P1", b"P4") else 3
    vals, idx = _pnm_fields(data, 2, need)
    w, h = vals[0], vals[1]
    maxval = vals[2] if need == 3 else 1
    out = bytearray(w * h)

    if magic == b"P4":                                  # packed bitmap, 1 = black
        rb = (w + 7) // 8
        for y in range(h):
            line = data[idx + y * rb:idx + (y + 1) * rb]
            obase = y * w
            for x in range(w):
                bit = (line[x >> 3] >> (7 - (x & 7))) & 1
                out[obase + x] = 0 if bit else 255
    elif magic == b"P5":                                # binary grey
        for i in range(w * h):
            out[i] = data[idx + i] * 255 // maxval
    elif magic == b"P6":                                # binary RGB
        for i in range(w * h):
            r, g, b = data[idx + i * 3:idx + i * 3 + 3]
            out[i] = ((r * 299 + g * 587 + b * 114) // 1000) * 255 // maxval
    else:                                               # ASCII variants
        nums = [int(t) for t in data[idx:].split()]
        if magic == b"P1":
            for i in range(w * h):
                out[i] = 0 if nums[i] else 255
        elif magic == b"P2":
            for i in range(w * h):
                out[i] = nums[i] * 255 // maxval
        else:
            for i in range(w * h):
                r, g, b = nums[i * 3:i * 3 + 3]
                out[i] = ((r * 299 + g * 587 + b * 114) // 1000) * 255 // maxval
    return Gray(w, h, bytes(out))


# --------------------------------------------------------------------------- #

def decode(data):
    """Sniff the format from the leading bytes and decode to Gray."""
    if data.startswith(_PNG_MAGIC):
        return decode_png(data)
    if data.startswith(b"BM"):
        return decode_bmp(data)
    if data[:2] in (b"P1", b"P2", b"P3", b"P4", b"P5", b"P6"):
        return decode_pnm(data)
    raise ImageError("unrecognised image format (expected PNG, BMP or PNM)")


def load(path):
    with open(path, "rb") as fh:
        return decode(fh.read())

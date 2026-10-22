"""Minimal PNG read/write in the standard library (8-bit RGB/RGBA, non-interlaced), so the lab
works without Pillow. Slow in pure Python (a 1280x720 decode takes a few seconds)."""

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write(path, width, height, pixels, channels=3):
    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride:(y + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, 8, 2 if channels == 3 else 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
                + _chunk(b"IEND", b""))


def read(path):
    """Returns (width, height, channels, pixels: bytearray)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != SIGNATURE:
        raise ValueError("%s is not a PNG" % path)
    pos, idat = 8, bytearray()
    width = height = depth = color = interlace = None
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError("unsupported PNG (depth %s, color %s, interlace %s)" % (depth, color, interlace))
    bpp = 3 if color == 2 else 4
    stride = width * bpp
    raw = zlib.decompress(bytes(idat))
    out = bytearray(height * stride)
    prev = bytearray(stride)
    i = 0
    for y in range(height):
        kind = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if kind == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif kind == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif kind == 3:
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif kind == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, bpp, out


def crop_scale(width, height, channels, pixels, crop=None, scale=None):
    """Crop (x, y, w, h) then nearest-neighbour scale. Returns (width, height, pixels)."""
    if crop:
        x, y, w, h = crop
        x, y = max(0, x), max(0, y)
        w, h = min(w, width - x), min(h, height - y)
        stride = width * channels
        rows = [pixels[(y + r) * stride + x * channels:(y + r) * stride + (x + w) * channels] for r in range(h)]
        pixels, width, height = bytearray().join(rows), w, h
    if scale:
        new_w, new_h = max(1, int(width * scale)), max(1, int(height * scale))
        stride = width * channels
        xs = [int(i / scale) * channels for i in range(new_w)]
        out = bytearray()
        for j in range(new_h):
            row = pixels[int(j / scale) * stride:int(j / scale) * stride + stride]
            for sx in xs:
                out += row[sx:sx + channels]
        pixels, width, height = out, new_w, new_h
    return width, height, pixels

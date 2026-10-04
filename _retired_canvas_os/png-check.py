#!/usr/bin/env python3
"""
正确解析 PNG（含 filter 还原），检查截图是不是真的 NovaDesk 画面。
之前那版忽略了 PNG 的行过滤器，所以颜色全是错的。
"""
import struct
import sys
import zlib


def read_png(path):
    d = open(path, "rb").read()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    w = struct.unpack(">I", d[16:20])[0]
    h = struct.unpack(">I", d[20:24])[0]
    bitdepth, colortype = d[24], d[25]
    i = 8
    idat = b""
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        typ = d[i + 4:i + 8]
        if typ == b"IDAT":
            idat += d[i + 8:i + 8 + ln]
        if typ == b"IEND":
            break
        i += 12 + ln
    raw = zlib.decompress(idat)
    return w, h, bitdepth, colortype, raw


def unfilter(w, h, bpp, raw):
    """还原 PNG 行过滤器，返回每行的字节。"""
    stride = w * bpp
    out = []
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ftype == 3:  # Average
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[x] = (line[x] + pr) & 0xFF
        out.append(bytes(line))
        prev = line
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\DSH工作\NovaDesk\web\_screenshot.png"
    w, h, bd, ct, raw = read_png(path)
    print("PNG %dx%d bitdepth=%d colortype=%d" % (w, h, bd, ct))

    bpp = 4 if ct == 6 else 3
    rows = unfilter(w, h, bpp, raw)

    colors = {}
    bright = 0
    for y in range(h):
        row = rows[y]
        for x in range(w):
            o = x * bpp
            r, g, b = row[o], row[o + 1], row[o + 2]
            colors[(r, g, b)] = colors.get((r, g, b), 0) + 1
            if r > 180 and g > 180 and b > 180:
                bright += 1

    total = w * h
    print("total pixels: %d, distinct colors: %d, bright(text): %d" %
          (total, len(colors), bright))
    print()
    print("top colors:")
    for c, n in sorted(colors.items(), key=lambda kv: -kv[1])[:10]:
        pct = 100.0 * n / total
        print("   rgb%-18s %6d  (%.1f%%)" % (str(c), n, pct))

    print()
    palette = {
        "DESK_TOP #1B2838": (27, 40, 56),
        "DESK_BOTTOM #0D1620": (13, 22, 32),
        "TASKBAR #1A1F26": (26, 31, 38),
        "WIN_BG #1E2228": (30, 34, 40),
        "WIN_TITLE_ACTIVE #2D3748": (45, 55, 72),
        "ACCENT #4A90D9": (74, 144, 217),
        "TEXT #E6EDF3": (230, 237, 243),
        "BTN_CLOSE #E05252": (224, 82, 82),
    }
    print("palette match:")
    for name, rgb in palette.items():
        n = colors.get(rgb, 0)
        mark = "FOUND" if n > 0 else "  -- "
        print("   [%s] %-28s %d px" % (mark, name, n))


if __name__ == "__main__":
    main()

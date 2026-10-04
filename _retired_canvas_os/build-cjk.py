#!/usr/bin/env python3
"""
生成 NovaDesk 的中文点阵字库。

流程：
  1. 从 web/*.js 里抽出所有用到的非 ASCII 字符（界面文字的唯一事实来源）
  2. 调 gen-cjk.ps1 用 GDI+ 把它们渲染成点阵
  3. 算出所有字形的并集包围盒，统一裁剪到同一个窗口
     —— 必须统一，否则每个字相对基线的位置会不一致
  4. 输出 kernel-cjk.js

为什么要"算包围盒再裁"而不是直接定死偏移：
中文字形的墨迹范围随字体和字号变化，硬编码偏移很容易切掉笔画。
先量后裁，并留 1px 余量，就不会缺笔画。
"""
import json
import os
import subprocess
import sys

WEB = os.path.dirname(os.path.abspath(__file__))
OUT_JS = os.path.join(WEB, "kernel-cjk.js")
CHARS_TXT = os.path.join(WEB, "_cjk_chars.txt")
RAW_JSON = os.path.join(WEB, "_cjk_raw.json")
GEN_PS1 = os.path.join(WEB, "gen-cjk.ps1")

# 界面文字的唯一来源。kernel-cjk.js 自己不算（它是产物）。
SOURCE_FILES = [
    "kernel-core.js",
    "kernel-apps.js",
    "kernel.js",
    "host.js",
    "boot.js",
]

FONT = "Microsoft YaHei"
FONT_SIZE = 20
CELL = 32          # 先在大格子里渲染，量完再裁
MARGIN = 1         # 裁剪时四周留的余量


def strip_comments(src):
    """
    去掉注释。

    这一步很重要：源码里的中文注释非常多，如果连同注释一起收进字库，
    字形数会翻好几倍（实测 685 -> 300 左右），字库白白大出一大截。
    界面文字都在字符串字面量里，注释对字库没有意义。

    这里是有意写得"粗糙"的：字符串里如果出现 // 会被误伤，
    但对"决定要生成哪些字"这个用途没有影响（最多多收几个字）。
    """
    import re
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def collect_chars():
    chars = set()
    for name in SOURCE_FILES:
        p = os.path.join(WEB, name)
        if not os.path.exists(p):
            print("  ! missing %s" % name)
            continue
        with open(p, encoding="utf-8") as f:
            text = f.read()
        for ch in strip_comments(text):
            if ord(ch) > 127:
                chars.add(ch)
    return sorted(chars, key=ord)


def main():
    chars = collect_chars()
    print("collected %d unique non-ascii chars" % len(chars))
    if not chars:
        print("nothing to do")
        return 1

    with open(CHARS_TXT, "w", encoding="utf-8") as f:
        f.write("".join(chars))

    print("rasterising with %s @ %dpx into %dx%d cells..." % (FONT, FONT_SIZE, CELL, CELL))
    r = subprocess.run([
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", GEN_PS1,
        "-CharsFile", CHARS_TXT,
        "-Out", RAW_JSON,
        "-FontSize", str(FONT_SIZE),
        "-FontName", FONT,
        "-Cell", str(CELL),
        "-OffsetX", "0", "-OffsetY", "0",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print("gen-cjk failed:\n%s\n%s" % (r.stdout, r.stderr))
        return 1
    print("  " + r.stdout.strip().splitlines()[-1])

    data = json.load(open(RAW_JSON, encoding="utf-8"))
    glyphs = data["glyphs"]

    # ---- 量并集包围盒 ----
    minx, miny, maxx, maxy = 10 ** 9, 10 ** 9, -1, -1
    for g in glyphs:
        rows = g["rows"]
        for y in range(CELL):
            bits = rows[y]
            if not bits:
                continue
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y
            for x in range(CELL):
                if (bits >> (CELL - 1 - x)) & 1:
                    if x < minx:
                        minx = x
                    if x > maxx:
                        maxx = x

    if maxx < 0:
        print("no ink found - font probably failed to render")
        return 1

    # ---- 裁剪窗口（带余量）----
    x0 = max(0, minx - MARGIN)
    y0 = max(0, miny - MARGIN)
    x1 = min(CELL - 1, maxx + MARGIN)
    y1 = min(CELL - 1, maxy + MARGIN)
    cw = x1 - x0 + 1
    ch_h = y1 - y0 + 1

    print("ink bbox x %d..%d y %d..%d -> crop %dx%d at (%d,%d)"
          % (minx, maxx, miny, maxy, cw, ch_h, x0, y0))

    if maxx + MARGIN > CELL - 1 or maxy + MARGIN > CELL - 1:
        print("  ! WARNING: ink reached the render cell edge, glyphs may be clipped")

    # ASCII 基线对齐：
    # 中文墨迹底边在裁剪格里的行号 = maxy - y0
    # 中文基线大约在墨迹底边往上 2px
    # ASCII 是 7 行、基线在第 7 行，所以向下偏移 = 中文基线行 - 7
    cjk_bottom_row = maxy - y0
    ascii_dy = cjk_bottom_row - 2 - 7
    if ascii_dy < 0:
        ascii_dy = 0

    # ---- 裁剪并输出 ----
    lines = []
    lines.append("/*")
    lines.append(" * NovaDesk 中文点阵字库 —— 自动生成，不要手改。")
    lines.append(" *")
    lines.append(" * 生成命令： python web/build-cjk.py")
    lines.append(" * 字体：%s  %dpx   单元格：%dx%d   字形数：%d" % (FONT, FONT_SIZE, cw, ch_h, len(glyphs)))
    lines.append(" *")
    lines.append(" * 每个字形是 %d 个整数，一行一个，最高位对应最左边的像素。" % ch_h)
    lines.append(" * 所有字形裁的是同一个窗口，所以它们之间的相对位置（也就是基线）是一致的。" )
    lines.append(" */")
    lines.append("import { Font } from './kernel-core.js';")
    lines.append("")
    lines.append("const CJK_GLYPHS = {")

    mask = (1 << cw) - 1
    empty = 0
    for g in glyphs:
        rows = g["rows"]
        out_rows = []
        for y in range(y0, y1 + 1):
            bits = rows[y]
            # 取 [x0, x1] 这一段，并左移到第 0 位
            seg = (bits >> (CELL - 1 - x1)) & mask
            out_rows.append(seg)
        if not any(out_rows):
            empty += 1
        cps = "0x%x" % g["cp"]
        lines.append("  %s: [%s]," % (cps, ",".join(str(v) for v in out_rows)))

    lines.append("};")
    lines.append("")
    lines.append("Font.CJK_W = %d;" % cw)
    lines.append("Font.CJK_H = %d;" % ch_h)
    lines.append("Font.cjk = CJK_GLYPHS;")
    lines.append("Font.ASCII_DY = %d;" % ascii_dy)
    lines.append("/* 行高按中文算：中文比 ASCII 高得多，否则中文会上下叠在一起 */")
    lines.append("Font.CELL_H = %d;" % ch_h)
    lines.append("")

    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    size = os.path.getsize(OUT_JS)
    print("wrote %s (%d bytes, %.1f KB)" % (OUT_JS, size, size / 1024))
    print("  glyph cell : %dx%d" % (cw, ch_h))
    print("  ASCII_DY   : %d" % ascii_dy)
    print("  CELL_H     : %d" % ch_h)
    if empty:
        print("  ! %d glyph(s) rendered empty" % empty)

    # ---- 自检：报告哪些字符没进字库 ----
    have = set(g["cp"] for g in glyphs)
    missing = [c for c in chars if ord(c) not in have]
    if missing:
        print("  ! missing: %s" % "".join(missing))
    else:
        print("  all %d chars covered" % len(chars))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
把 gen-cjk.ps1 生成的 JSON 打印成 ASCII art，用来肉眼判断字形清不清楚。
"""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"D:\DSH工作\NovaDesk\web\_cjk_test.json"
data = json.load(open(path, encoding="utf-8"))
meta = data.get("_meta", {})
cell = meta.get("cell", 16)

print("font=%s size=%s cell=%s offset=(%s,%s)" % (
    meta.get("font"), meta.get("size"), cell,
    meta.get("offsetX"), meta.get("offsetY")))
print()

keys = [k for k in data.keys() if not k.startswith("_")]
# 横向并排显示，每行 8 个字
PER_ROW = 8
for i in range(0, len(keys), PER_ROW):
    group = keys[i:i + PER_ROW]
    print("  ".join(("%-*s" % (cell, k)) for k in group))
    for y in range(cell):
        line = []
        for k in group:
            bits = data[k][y]
            row = "".join("#" if (bits >> (cell - 1 - x)) & 1 else "." for x in range(cell))
            line.append(row)
        print("  ".join(line))
    print()

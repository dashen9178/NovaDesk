#!/usr/bin/env python3
"""静态审计：重复声明、危险汇点、动态执行。"""
import os
import re

AI = os.path.dirname(os.path.abspath(__file__))


def main():
    src = ''
    for f in ('ui.js', 'core.js'):
        with open(os.path.join(AI, f), encoding='utf-8') as fh:
            src += fh.read() + '\n'

    print('=== 1. 顶层重复声明 ===')
    for kind in ('const', 'let'):
        names = re.findall(r'^(?:export\s+)?' + kind + r'\s+([A-Za-z_$][\w$]*)', src, re.M)
        seen = {}
        for n in names:
            seen[n] = seen.get(n, 0) + 1
        dup = {k: v for k, v in seen.items() if v > 1}
        print('  %-6s 共 %d 个，重复: %s' % (kind, len(names), dup or '无'))

    print('\n=== 2. 顶层函数重复定义 ===')
    fns = re.findall(r'^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)', src, re.M)
    seen = {}
    for n in fns:
        seen[n] = seen.get(n, 0) + 1
    dup = {k: v for k, v in seen.items() if v > 1}
    print('  共 %d 个，重复: %s' % (len(fns), dup or '无'))

    print('\n=== 3. innerHTML 汇点 ===')
    for m in re.finditer(r'([\w.]+)\.innerHTML\s*=\s*([^;]+);', src):
        rhs = m.group(2).strip()
        if 'renderMarkdown' in rhs:
            flag = '!! 审查markdown'
        elif rhs == "''":
            flag = 'ok 清空'
        elif rhs.startswith("'") or rhs.startswith('"'):
            flag = 'ok 静态'
        elif rhs.startswith('html') or rhs.startswith('p.html'):
            flag = '!! 面板HTML(设计如此)'
        else:
            flag = '?? 需看'
        print('  %-22s %s = %s' % (flag, m.group(1), rhs[:50]))

    print('\n=== 4. 动态执行 ===')
    found = False
    for pat in ['eval(', 'new Function', 'document.write', 'Function(']:
        n = src.count(pat)
        if n:
            print('  !! %-18s %d 处' % (pat, n))
            found = True
    if not found:
        print('  无')

    print('\n=== 5. 属性拼接里直接塞变量 ===')
    n = 0
    for line in src.split('\n'):
        if re.search(r'(href|src|title|alt|style)\s*=\s*"', line) and '$' in line and 'escapeHtml' not in line:
            print('  !! ' + line.strip()[:100])
            n += 1
    if not n:
        print('  无')

    print('\n=== 6. 转义函数是否覆盖引号 ===')
    m = re.search(r'function escapeHtml[\s\S]{0,200}?\n}', src)
    if m:
        body = m.group(0)
        print('  转义 & :', '&' in body)
        print('  转义 < :', '<' in body)
        print('  转义 > :', '>' in body)
        print("  !! 转义 \" :", 'quot' in body or '"' in body.replace('&', ''))
        print("  !! 转义 ' :", '#' in body and '39' in body)

    print('\n=== 7. 历史/存储是否有容量上限 ===')
    for pat in ['messages = []', 'slice(-', 'MAX_', 'length >']:
        print('  %-16s %d 处' % (pat, src.count(pat)))

    print('\n=== 8. 原生桥的 URL 协议检查 ===')


if __name__ == '__main__':
    main()

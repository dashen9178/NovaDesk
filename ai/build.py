#!/usr/bin/env python3
"""
把 ai/ 下的模块打包成单个 NovaDesk.html。

为什么要打包：ES module 的 import 在 file:// 协议下会被 CORS 拦住，
双击打开会失败。内联成一段 <script> 后双击就能用。
"""
import os
import re
import sys

AI = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(AI, "..", "NovaDesk.html"))
INDEX = os.path.join(AI, "index.html")
GRADLE = os.path.abspath(os.path.join(AI, "..", "android", "app", "build.gradle.kts"))

# 顺序有讲究：core 定义 state / api / tools，ui 依赖它们
ORDER = ["core.js", "ui.js"]


def read_version():
    """
    版本号只有一个来源：android/app/build.gradle.kts 的 versionName。

    为什么要注入而不是让 JS 里写死：写死的话 gradle 一改版本，
    页面上报的版本号就是旧的 —— 而**更新检查全靠它**，
    报错了就会一直提示"有新版本"，或者干脆永远不提示。
    """
    try:
        with open(GRADLE, encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return "0.0"
    m = re.search(r'versionName\s*=\s*"([^"]+)"', txt)
    return m.group(1) if m else "0.0"


def strip_module_syntax(src):
    src = re.sub(r"^\s*import\s+[^;]*?;\s*$", "", src, flags=re.M)
    src = re.sub(r"^\s*export\s+default\s+[^;]*;\s*$", "", src, flags=re.M)
    src = re.sub(r"^\s*export\s+(class|function)\s+", r"\1 ", src, flags=re.M)
    src = re.sub(r"^\s*export\s+const\s+", "const ", src, flags=re.M)
    src = re.sub(r"^\s*export\s+let\s+", "let ", src, flags=re.M)
    src = re.sub(r"^\s*export\s*\{[^}]*\}\s*;?\s*$", "", src, flags=re.M)
    return src


def main():
    if not os.path.exists(INDEX):
        print("missing %s" % INDEX)
        return 1

    parts = []
    for name in ORDER:
        p = os.path.join(AI, name)
        if not os.path.exists(p):
            print("MISSING module: %s" % p)
            return 1
        with open(p, encoding="utf-8") as f:
            src = f.read()
        parts.append("/* ===== %s ===== */\n%s" % (name, strip_module_syntax(src)))

    bundle = "\n\n".join(parts)

    # 把版本号注入进去（单一事实来源见 read_version）
    version = read_version()
    if "__APP_VERSION__" not in bundle:
        print("WARNING: 找不到 __APP_VERSION__ 占位符，版本号没注入")
    bundle = bundle.replace("__APP_VERSION__", version)

    with open(INDEX, encoding="utf-8") as f:
        html = f.read()

    if "/*__JS_BUNDLE__*/" not in html:
        print("index.html is missing the /*__JS_BUNDLE__*/ placeholder")
        return 1

    # 用函数式替换，避免 bundle 里的反斜杠被当成转义序列
    html = html.replace("/*__JS_BUNDLE__*/", bundle)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(OUT)
    print("wrote %s (%.1f KB)" % (OUT, size / 1024))
    print("modules: %s   version: %s" % (", ".join(ORDER), version))

    # 自检
    bad = re.findall(r"^\s*(?:import|export)\s", html, flags=re.M)
    if bad:
        print("WARNING: %d leftover module statement(s)" % len(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())

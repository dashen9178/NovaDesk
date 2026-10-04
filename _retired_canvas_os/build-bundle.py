#!/usr/bin/env python3
"""
把 web/ 下的 ES module 打包成一个自包含的 HTML 文件。

为什么要打包：ES module 的 import 在 file:// 协议下会被 CORS 拦住，
双击打开会失败。打成单文件后 <script> 内联执行，双击就能用。
"""
import os
import re
import sys

WEB = r"D:\DSH工作\NovaDesk\web"
OUT = r"D:\DSH工作\NovaDesk\NovaDesk.html"

ORDER = ["kernel-core.js", "kernel-cjk.js", "kernel-apps.js", "kernel.js", "host.js", "boot.js"]


def strip_module_syntax(src, filename):
    """去掉 import/export，保留代码本体。"""
    # 去掉 import 行（多种写法）
    src = re.sub(r'^\s*import\s+[^;]*?;\s*$', '', src, flags=re.M)
    # export default X; -> 删掉（会重复定义）
    src = re.sub(r'^\s*export\s+default\s+[^;]*;\s*$', '', src, flags=re.M)
    # export class X -> class X ; export function x -> function x
    src = re.sub(r'^\s*export\s+(class|function)\s+', r'\1 ', src, flags=re.M)
    # export const X = -> const X =
    src = re.sub(r'^\s*export\s+const\s+', 'const ', src, flags=re.M)
    src = re.sub(r'^\s*export\s+let\s+', 'let ', src, flags=re.M)
    # export { a, b }; -> 删掉
    src = re.sub(r'^\s*export\s*\{[^}]*\}\s*;?\s*$', '', src, flags=re.M)

    # kernel-apps.js 自带一份 NovaKey 副本（值完全相同），
    # 而 kernel-core.js 也导出 NovaKey。整个 bundle 共用一个作用域，
    # 会把 apps 那份改名，避免重复声明。
    if filename == "kernel-apps.js":
        src = re.sub(r'\bNovaKey\b', 'AppsNovaKey', src)
        # 改名后表内自引用仍成立；setNovaKey 保留原语义
    return src


def main():
    parts = []
    for name in ORDER:
        p = os.path.join(WEB, name)
        if not os.path.exists(p):
            print("MISSING: %s" % p, file=sys.stderr)
            return 1
        with open(p, encoding="utf-8") as f:
            src = f.read()
        body = strip_module_syntax(src, name)
        parts.append("/* ========== %s ========== */\n%s" % (name, body))

    bundle = "\n\n".join(parts)

    html = HTML_TEMPLATE.replace("/*__BUNDLE__*/", bundle)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(OUT)
    print("wrote %s (%d bytes, %.1f KB)" % (OUT, size, size / 1024))
    print("modules bundled: %s" % ", ".join(ORDER))
    return 0


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#0d1620">
<title>NovaDesk</title>
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    background: #05080c; color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    -webkit-tap-highlight-color: transparent;
    -webkit-user-select: none; user-select: none;
    overscroll-behavior: none;
  }
  #wrap {
    position: fixed; inset: 0;
    display: flex; align-items: center; justify-content: center;
  }
  canvas#nova {
    display: block;
    width: 100%; height: 100%;
    object-fit: contain;
    image-rendering: pixelated;
    outline: none;
    touch-action: none;
    cursor: default;
  }
  /* 隐藏的输入桥：只为在手机上唤起软键盘，用户永远看不见 */
  #nova-ime {
    position: fixed; bottom: 0; left: 0;
    width: 1px; height: 1px; opacity: 0;
    border: 0; padding: 0; margin: 0;
    background: transparent; color: transparent;
    pointer-events: none;
  }
  #nova-error {
    display: none;
    position: fixed; left: 12px; right: 12px; bottom: 12px;
    background: #4a1414; border: 1px solid #e05252; border-radius: 8px;
    padding: 12px 14px; font: 13px/1.5 ui-monospace, Menlo, Consolas, monospace;
    color: #ffb4b4; z-index: 99; white-space: pre-wrap;
  }
  /* 竖屏提示：这个系统是横屏电脑界面 */
  #rotate {
    position: fixed; inset: 0; display: none;
    align-items: center; justify-content: center; flex-direction: column;
    background: #0d1620; z-index: 50; text-align: center; padding: 24px;
  }
  #rotate .ph { font-size: 56px; margin-bottom: 18px; }
  #rotate p { color: #8b949e; font-size: 15px; max-width: 300px; line-height: 1.6; }
  @media (orientation: portrait) and (max-width: 820px) {
    #rotate { display: flex; }
  }
</style>
</head>
<body>
<div id="wrap">
  <canvas id="nova" tabindex="0"></canvas>
</div>
<input id="nova-ime" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
<div id="nova-error"></div>
<div id="rotate">
  <div class="ph">&#128421;</div>
  <p>NovaDesk 是一个横屏的电脑系统。<br>请把手机横过来使用。</p>
</div>

<script>
/*__BUNDLE__*/

// ---- 启动 ----
(function () {
  var canvas = document.getElementById('nova');
  // 让 canvas 按 16:9 适配窗口，留黑边而不是拉伸变形
  function fit() {
    var wrap = document.getElementById('wrap');
    var W = wrap.clientWidth, H = wrap.clientHeight;
    var target = 16 / 9;
    var w = W, h = W / target;
    if (h > H) { h = H; w = H * target; }
    canvas.style.width = Math.floor(w) + 'px';
    canvas.style.height = Math.floor(h) + 'px';
  }
  fit();
  window.addEventListener('resize', fit);
  window.addEventListener('orientationchange', function () { setTimeout(fit, 120); });

  try {
    boot(canvas);
  } catch (err) {
    var el = document.getElementById('nova-error');
    el.style.display = 'block';
    el.textContent = 'Boot failed: ' + (err && err.message ? err.message : String(err))
      + '\n' + (err && err.stack ? err.stack : '');
    console.error(err);
  }
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())

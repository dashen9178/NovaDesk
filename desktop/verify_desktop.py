# -*- coding: utf-8 -*-
"""
桌面版实测：真的起 NovaDesk.exe，用 CDP 连进去逐项验证。

为什么能这么测：桌面壳支持 `--remote-debugging-port=N`，把参数转给 WebView2。
于是桌面版和网页版可以用同一套 CDP 客户端验证，不用靠肉眼盯窗口。

验的东西：
  · 页面真的从自己尾部解出来并加载了
  · 文件桥 / 导出桥都挂上了，安卓那套桥没有（不该有）
  · 三档权限：off 拒绝、all 能读能写能列目录能建能删
  · 用真实壳导出一个 exe，并逐字节核对它的页脚和内容
  · 长对话 + 超长思考时输入框仍在视口内（网页版修过的那个布局 bug）
"""
import json
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "ai"))
from verify_web_lib import CDP, evaluate  # noqa: E402  复用项目已有的 CDP 客户端

EXE = os.environ.get("NOVA_EXE", r"D:\novadesk-share\NovaDesk.exe")
PORT = int(os.environ.get("NOVA_PORT", "9411"))

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("   " + str(detail)[:200]) if detail else ""))


def http_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def exe_key(path):
    """和 C++ 侧 NvExeKey() 同一套算法：小写路径做 FNV-1a，取 16 位十六进制"""
    h = 1469598103934665603
    for ch in path.lower():
        h ^= ord(ch)
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def settings_path(exe):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "NovaDesk", "settings")
    return os.path.join(base, exe_key(exe) + ".ini")


def debug_env():
    """
    壳只在 NOVADESK_DEBUG=1 时才把 --remote-debugging-port 转给 WebView2。
    这是刻意加的闸：能开 CDP 就等于能把 localStorage 里的 API key 读走，
    不该光靠一个命令行参数就能打开。
    """
    env = dict(os.environ)
    env["NOVADESK_DEBUG"] = "1"
    return env


# ---- 操作原生对话框（Windows）----
# 权限确认框是系统 MessageBox，CDP 看不见它，只能用 Win32 找窗口、发按键。
_u32 = ctypes.windll.user32

WM_COMMAND = 0x0111
IDYES = 6
IDNO = 7


def find_confirm_dialog():
    """找那个标题是「文件权限」的确认框，返回句柄（没有就 None）"""
    return _u32.FindWindowW(None, "文件权限") or None


def click_yes_no(hwnd, yes):
    """给对话框发 WM_COMMAND，等价于点「是」或「否」"""
    _u32.PostMessageW(hwnd, WM_COMMAND, IDYES if yes else IDNO, 0)


def main():
    if not os.path.isfile(EXE):
        print("找不到 exe：%s" % EXE)
        return 1

    subprocess.run(["taskkill", "/IM", "NovaDesk.exe", "/F"], capture_output=True)

    ini = settings_path(EXE)
    backup = None
    if os.path.isfile(ini):
        with open(ini, "rb") as f:
            backup = f.read()
    os.makedirs(os.path.dirname(ini), exist_ok=True)
    # 从"关闭"这一档开始，保证第一步的拒绝是真的被拒
    with open(ini, "w", encoding="utf-8") as f:
        f.write("mode=off\r\nfolder=\r\n")

    proc = subprocess.Popen([EXE, "--remote-debugging-port=%d" % PORT],
                            env=debug_env())
    exported_path = None
    try:
        tabs = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                tabs = http_json("http://127.0.0.1:%d/json" % PORT)
                if tabs:
                    break
            except Exception:
                pass
        if not tabs:
            print("调试端口没起来，exe 可能没启动成功")
            return 1

        page = [t for t in tabs if t.get("type") == "page"][0]
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Runtime.enable")

        def js(expr):
            return evaluate(cdp, expr)

        jsync = lambda e: json.loads(js(e))

        # 页面是"导航提交了但脚本还没跑完"的状态也能连上 CDP，
        # 所以必须显式等初始化完成，否则第一步会误报"脚本没运行"。
        ready = False
        for _ in range(60):
            r = js("(typeof state === 'object' && typeof send === 'function')")
            if r is True:
                ready = True
                break
            time.sleep(0.3)
        print("\n=== 0. 等页面初始化 ===")
        check("页面脚本初始化完成", ready is True, str(r))

        print("\n=== 1. 页面是从自己尾部解出来的 ===")
        d = jsync("JSON.stringify({title:document.title, url:location.href,"
                  "ready:document.readyState})")
        print("   ", d)
        check("页面标题是 NovaDesk", d["title"] == "NovaDesk", d["title"])
        check("URL 是本地解包出来的 app.html",
              d["url"].startswith("file://") and d["url"].endswith("app.html"), d["url"])

        print("\n=== 2. 桥挂对了没 ===")
        bridges = jsync("""
        (async function(){
          var nf = await fileNative('info');
          return JSON.stringify({
            hasFile: hasFileBridge(),
            isDesktop: isDesktopShell(),
            hasExe: hasExeBridge(),
            hasApk: hasApkBridge(),
            hasNativeNet: !!(window.NovaNative && window.NovaNative.get),
            info: nf,
            chips: document.querySelectorAll('#sysbar .chip').length
          });
        })()
        """)
        print("   ", json.dumps(bridges, ensure_ascii=False)[:300])
        check("文件桥在", bridges["hasFile"] is True)
        check("认得出这是桌面壳", bridges["isDesktop"] is True)
        check("导出 exe 的桥在", bridges["hasExe"] is True)
        check("**没有安卓的 APK 桥**（不该有）", bridges["hasApk"] is False)
        check("**没有原生联网桥**（该走 WebView 自己的网络）",
              bridges["hasNativeNet"] is False)
        check("info() 返回了合法对象且带 mode",
              isinstance(bridges["info"], dict) and "mode" in bridges["info"],
              str(bridges["info"])[:120])
        check("界面上有文件权限芯片", bridges["chips"] >= 1, "%d 个" % bridges["chips"])

        print("\n=== 3. 关闭档位：必须拒绝 ===")
        off = jsync("""
        (async function(){
          return JSON.stringify({
            r: await fileNative('read', 'C:/Windows/win.ini'),
            w: await fileNative('write', 'C:/nova-should-not-exist.txt', 'x')
          });
        })()
        """)
        check("**读被拒绝**", off["r"].get("ok") is False, off["r"].get("error"))
        check("**写被拒绝**", off["w"].get("ok") is False, off["w"].get("error"))

        print("\n=== 4. 完全权限：真读写真列目录 ===")
        tmp = tempfile.mkdtemp(prefix="nova-desktop-")
        tmp_fwd = tmp.replace("\\", "/")

        # 升级到「完全权限」现在必须过一道**原生确认框** ——
        # 因为 AI 写的面板脚本和界面共用同一个 JS 环境，
        # 光在网页那层拦不住"脚本自己把权限打开"。
        # 这里既验"它确实弹了框"，也顺手把框点掉。
        js("(async function(){ await fileNative('setMode','all'); return 1; })()")
        time.sleep(0.8)
        dlg = find_confirm_dialog()
        check("**升权限时弹出了原生确认框**", dlg is not None,
              "窗口句柄 %s" % dlg)
        if dlg:
            # 先点「不允许」，确认它真的不给
            click_yes_no(dlg, yes=False)
            time.sleep(0.6)
            denied = jsync("(async function(){ return JSON.stringify(await fileNative('info')); })()")
            check("**点「不允许」之后权限没开**", denied.get("mode") == "off",
                  str(denied.get("mode")))

            # 再来一次，这次点「允许」
            js("(async function(){ await fileNative('setMode','all'); return 1; })()")
            time.sleep(0.8)
            dlg2 = find_confirm_dialog()
            check("第二次仍然会问", dlg2 is not None, str(dlg2))
            if dlg2:
                click_yes_no(dlg2, yes=True)
                time.sleep(0.6)

        mode = jsync("(async function(){ return JSON.stringify(await fileNative('info')); })()")
        check("切到完全权限", mode.get("mode") == "all", str(mode.get("mode")))

        target = tmp_fwd + "/hello.txt"
        body = u"你好 NovaDesk 桌面版"

        w = jsync("(async function(){ return JSON.stringify(await fileNative('write', %s, %s)); })()"
                  % (json.dumps(target), json.dumps(body)))
        check("写文件成功", w.get("ok") is True, w.get("error"))

        r = jsync("(async function(){ return JSON.stringify(await fileNative('read', %s)); })()"
                  % json.dumps(target))
        check("**读回来的内容和写进去的一模一样**",
              r.get("ok") is True and r.get("text") == body, repr(r.get("text"))[:80])

        st = jsync("(async function(){ return JSON.stringify(await fileNative('stat', %s)); })()"
                   % json.dumps(target))
        check("stat 看到文件存在且大小对",
              st.get("ok") is True and st.get("exists") is True
              and st.get("dir") is False and st.get("size") == len(body.encode("utf-8")),
              str(st))

        sub = tmp_fwd + "/子目录"
        m = jsync("(async function(){ return JSON.stringify(await fileNative('mkdir', %s)); })()"
                  % json.dumps(sub))
        check("建目录成功", m.get("ok") is True, m.get("error"))

        ls = jsync("(async function(){ return JSON.stringify(await fileNative('list', %s)); })()"
                   % json.dumps(tmp_fwd))
        names = [i["name"] for i in (ls.get("items") or [])]
        check("列目录能看到刚建的东西", "hello.txt" in names and "子目录" in names, str(names))

        de = jsync("(async function(){ return JSON.stringify(await fileNative('delete', %s)); })()"
                   % json.dumps(target))
        check("删文件成功", de.get("ok") is True, de.get("error"))
        gone = jsync("(async function(){ return JSON.stringify(await fileNative('stat', %s)); })()"
                     % json.dumps(target))
        check("删完真的没了", gone.get("exists") is False)

        print("\n=== 4b. 破坏性操作的护栏 ===")
        # 空路径在原生那边会解析成"用户主目录"（all 档）或"授权文件夹根"（folder 档），
        # 而 delete 是递归删除 —— 没有护栏的话一句 delete('') 就能清空整个家目录。
        home = os.path.expanduser("~")
        nulfile = tmp_fwd + "/nul.txt"
        t0 = time.time()
        g = jsync("""
        (async function(){
          return JSON.stringify({
            emptyDelete: await fileNative('delete', ''),
            homeDelete:  await fileNative('delete', %s),
            nulWrite:    await fileNative('write', %s, "a\\u0000b"),
            nulRead:     await fileNative('read', %s)
          });
        })()
        """ % (json.dumps(home.replace("\\", "/")),
               json.dumps(nulfile), json.dumps(nulfile)))
        elapsed = time.time() - t0
        print("   ", json.dumps(g, ensure_ascii=False)[:220])
        print("    用时 %.1f 秒" % elapsed)
        check("**空路径删除被拒绝**", g["emptyDelete"].get("ok") is False,
              str(g["emptyDelete"].get("error"))[:70])
        check("**家目录整体删除被拒绝**", g["homeDelete"].get("ok") is False,
              str(g["homeDelete"].get("error"))[:70])
        check("家目录还在（没被真删）", os.path.isdir(home) is True)
        # 参数里带 NUL 时，原生那边曾经会当场截断 -> 解包失败 -> 静默丢弃 ->
        # 页面的 Promise 永远不 settle。现在必须能正常返回。
        check("**参数里带 NUL 也能正常往返（不卡死）**",
              g["nulWrite"].get("ok") is True, str(g["nulWrite"].get("error"))[:70])
        check("**NUL 文件读回来不会卡死**", g["nulRead"] is not None)
        check("往返没有超时（说明桥没被噎住）", elapsed < 20, "%.1f 秒" % elapsed)
        js("(async function(){ await fileNative('delete', %s); return 1; })()"
           % json.dumps(nulfile))

        print("\n=== 5. 用真实壳导出一个 exe ===")
        exe_info = jsync("(async function(){ return JSON.stringify(await exeNative('info')); })()")
        print("   ", json.dumps(exe_info, ensure_ascii=False)[:240])
        check("导出桥报出了自己的壳长度",
              exe_info.get("available") is True and exe_info.get("shellSize", 0) > 100000,
              str(exe_info.get("shellSize")))

        # 用**真实的导出页**来测：直接调 buildStandaloneHtml，这样测到的
        # 就是用户会拿到的那份 HTML（含自适应 CSS 和"窗口贴合内容"脚本），
        # 而不是我手搓一个测试页 —— 那个测不出任何真实问题。
        #
        # 面板里额外塞一小段桥调用代码：导出的小应用里没有 NovaDesk 的 JS，
        # 要验它的原生桥和权限是不是独立的，就得让它自己去调。
        helper = (
            "<script>"
            "window.__pending={};"
            "window.__novaNative=function(rid,payload){"
            "  var f=window.__pending[String(rid)];"
            "  if(f){delete window.__pending[String(rid)];f(payload);}};"
            "window.__bridge=function(target,method,args){"
            "  return new Promise(function(res){"
            "    var id=Math.floor(Math.random()*1e9);"
            "    window.__pending[String(id)]=res;"
            "    var head=[String(id),target,method,String(args.length)];"
            "    for(var i=0;i<args.length;i++)head.push(String(args[i].length));"
            "    window.chrome.webview.postMessage(head.join('\\x1f')+'\\x1f'+args.join(''));"
            "  });};"
            "</script>")
        panels = [{
            "id": "p1", "title": "测试", "kind": "html", "place": "right",
            "html": "<h1>hello</h1><p>一个很小的工具</p>" + helper,
        }]
        app_html = js("buildStandaloneHtml('NV导出测试', %s)"
                      % json.dumps(panels, ensure_ascii=False))
        if not isinstance(app_html, str) or not app_html:
            check("能组装出导出页", False, str(app_html)[:120])
            return 2
        check("能组装出导出页", True, "%d 字符" % len(app_html))
        res = jsync("""
        (async function(){
          return JSON.stringify(await exeNative('export', 'NV导出测试', %s));
        })()
        """ % json.dumps(app_html))
        print("   ", json.dumps(res, ensure_ascii=False)[:240])
        check("导出返回成功", res.get("ok") is True, res.get("error"))

        exported_path = res.get("path")
        if exported_path and os.path.isfile(exported_path):
            size = os.path.getsize(exported_path)
            check("导出的 exe 真的存在", True, "%s (%d 字节)" % (exported_path, size))
            with open(exported_path, "rb") as f:
                data = f.read()
            footer = data[-40:]
            check("**页脚 magic 正确**", footer[:8] == b"NVDSEXE1", repr(footer[:8]))
            html_off = int.from_bytes(footer[8:16], "little")
            html_len = int.from_bytes(footer[16:24], "little")
            name_off = int.from_bytes(footer[24:32], "little")
            name_len = int.from_bytes(footer[32:36], "little")
            check("**页脚偏移自洽**",
                  html_off + html_len == name_off and name_off + name_len == size - 40,
                  "html@%d len=%d name@%d" % (html_off, html_len, name_off))
            nm = data[name_off:name_off + name_len].decode("utf-8")
            check("应用名写进去了", nm == "NV导出测试", nm)
            embedded = data[html_off:html_off + html_len].decode("utf-8")
            check("**页面内容一个字节不差**", embedded == app_html)
            check("**导出的包里没有把 NovaDesk 自己的页面攒进去**",
                  size == html_off + html_len + name_len + 40,
                  "%d 字节（壳 %d）" % (size, html_off))
        else:
            check("导出的 exe 真的存在", False, str(exported_path))

        print("\n=== 6. 布局：长对话不能把输入框顶出去 ===")
        # 桌面上没有存过 API key，所以首次配置卡是盖着的 —— 那层遮罩会把
        # 发送键挡在下面，elementFromPoint 自然点不到。先把遮罩关掉再测，
        # 否则测的是"遮罩挡不挡"，不是"按钮点不点得到"。
        js("state.apiKey = 'sk-test-only'; closeOverlays(); 1")
        lay = jsync("""
        (function(){
          var think = '';
          for (var i=0;i<120;i++) think += '思考第'+i+'行，要仔细想清楚。\\n';
          state.messages = [];
          for (var k=0;k<8;k++){
            state.messages.push({role:'user', content:'第'+k+'个问题'});
            state.messages.push({role:'assistant', content:'回答'+k, reasoning_content: think});
          }
          state.panels = []; state.actions = [];
          renderAll();
          var cw = document.querySelector('.composer-wrap').getBoundingClientRect();
          var sr = document.getElementById('btnSend').getBoundingClientRect();
          var hit = document.elementFromPoint(sr.left+sr.width/2, sr.top+sr.height/2);
          var sEl = document.getElementById('btnSend');
          return JSON.stringify({
            innerH: window.innerHeight,
            composerBottom: Math.round(cw.bottom),
            docH: document.documentElement.scrollHeight,
            visible: cw.bottom <= window.innerHeight + 1,
            notGrown: document.documentElement.scrollHeight <= window.innerHeight + 2,
            sendHittable: !!(hit && (hit === sEl || sEl.contains(hit)))
          });
        })()
        """)
        print("   ", lay)
        check("输入框仍在视口内", lay["visible"] is True,
              "底边 %d / 视口 %d" % (lay["composerBottom"], lay["innerH"]))
        check("**页面没被撑高**", lay["notGrown"] is True, "文档高 %d" % lay["docH"])
        check("停止/发送按钮点得到", lay["sendHittable"] is True)

        print("\n=== 7. 导出的 exe 真的是个独立应用 ===")
        # 两层意思：一是它真能跑、标题和页面都对；二是它**不能继承** NovaDesk
        # 的权限 —— 否则用户在 NovaDesk 里开了完全权限，随手导出的应用
        # 一启动就是全盘可读写，而他从没给过那个应用授权。
        proc2 = None
        if exported_path and os.path.isfile(exported_path):
            subprocess.run(["taskkill", "/IM", "NV导出测试.exe", "/F"], capture_output=True)
            proc2 = subprocess.Popen([exported_path,
                                      "--remote-debugging-port=%d" % (PORT + 1)],
                                     env=debug_env())
            try:
                tabs2 = None
                for _ in range(60):
                    time.sleep(0.5)
                    try:
                        tabs2 = http_json("http://127.0.0.1:%d/json" % (PORT + 1))
                        if tabs2:
                            break
                    except Exception:
                        pass
                if not tabs2:
                    check("导出的 exe 能启动", False, "调试端口没起来")
                else:
                    pg2 = [t for t in tabs2 if t.get("type") == "page"][0]
                    cdp2 = CDP(pg2["webSocketDebuggerUrl"])
                    cdp2.call("Runtime.enable")
                    ok2 = False
                    for _ in range(60):
                        if evaluate(cdp2, "(document.readyState === 'complete')") is True:
                            ok2 = True
                            break
                        time.sleep(0.3)
                    check("导出的 exe 能启动并加载页面", ok2 is True)
                    # 等它把"贴合内容"那条消息发完（页面在 load+60ms 和 +600ms 各量一次），
                    # 否则量到的是还没缩的默认尺寸
                    time.sleep(1.6)

                    d2 = json.loads(evaluate(cdp2, """JSON.stringify({
                      title: document.title,
                      body: (document.body.innerText || '').trim().slice(0, 40),
                      href: location.href,
                      vw: document.documentElement.clientWidth,
                      vh: document.documentElement.clientHeight,
                      ow: window.outerWidth, oh: window.outerHeight
                    })"""))
                    print("   ", d2)
                    check("**窗口标题就是用户起的应用名**", d2["title"] == "NV导出测试",
                          d2["title"])
                    check("**加载的是用户做的页面**", "hello" in d2["body"], d2["body"])
                    check("页面是从它自己尾部解出来的（路径和 NovaDesk 不一样）",
                          "run" in d2["href"] and exe_key(exported_path) in d2["href"],
                          d2["href"][-60:])
                    # 内容是"一个 h1 + 一行字"，窗口该缩到很小才对。
                    # 壳的默认是 640 宽，所以 < 500 就说明 FIT 那条路真的通了。
                    check("**窗口按内容贴合了（不是死板的固定尺寸）**",
                          d2["vw"] < 500 and d2["vh"] < 560,
                          "视口 %dx%d（默认是 640x560）" % (d2["vw"], d2["vh"]))

                    m2 = json.loads(evaluate(cdp2, """
                    (async function(){
                      return JSON.stringify(await window.__bridge('novaFiles','info',[]));
                    })()
                    """))
                    print("   ", m2)
                    check("**导出的应用自带可用的文件桥**",
                          m2.get("mode") is not None, str(m2)[:120])
                    check("**导出的应用不继承 NovaDesk 的权限（默认关闭）**",
                          m2.get("mode") == "off", str(m2.get("mode")))
                    r2 = json.loads(evaluate(cdp2, """
                    (async function(){
                      return JSON.stringify(
                        await window.__bridge('novaFiles','read',['C:/Windows/win.ini']));
                    })()
                    """))
                    check("**它没授权时读系统文件被拒绝**",
                          r2.get("ok") is False, str(r2.get("error"))[:80])
                    r3 = json.loads(evaluate(cdp2, """
                    (async function(){
                      return JSON.stringify(
                        await window.__bridge('novaFiles','delete',['']));
                    })()
                    """))
                    check("**导出的应用里空路径删除也被拒绝**",
                          r3.get("ok") is False, str(r3.get("error"))[:80])
            finally:
                if proc2:
                    proc2.terminate()
                subprocess.run(["taskkill", "/IM", "NV导出测试.exe", "/F"], capture_output=True)
        else:
            check("导出的 exe 能启动", False, "上一步没产出文件")

        print("\n=== 8. 拖图片进窗口不能盖屏 ===")
        drop_case = jsync("""
        (async function(){
          state.pendingFiles = []; renderFiles();

          var canvas = document.createElement('canvas');
          canvas.width = 500; canvas.height = 400;
          var g = canvas.getContext('2d');
          g.fillStyle = '#e53935';
          g.fillRect(0, 0, 500, 400);
          g.fillStyle = '#fff';
          g.font = 'bold 80px sans-serif';
          g.textAlign = 'center';
          g.fillText('DROP', 250, 220);

          var url = canvas.toDataURL('image/png');
          var bin = atob(url.split(',')[1]);
          var arr = new Uint8Array(bin.length);
          for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);

          var beforeHref = location.href;
          var dt = new DataTransfer();
          dt.items.add(new File([arr], 'drop.png', {type: 'image/png'}));
          function send(type) {
            var ev = new DragEvent(type, {
              bubbles: true, cancelable: true, dataTransfer: dt
            });
            return document.dispatchEvent(ev);
          }
          var over = send('dragover');
          var droppedNow = send('drop');

          for (var t = 0; t < 100 && state.pendingFiles.length === 0; t++) {
            await new Promise(function(r){ setTimeout(r, 20); });
          }

          var im = document.querySelector('#files .chipimg');
          var rect = im ? im.getBoundingClientRect() : null;
          var docEl = document.documentElement;
          var out = {
            hooked: window.__novaDragHooked === true,
            overPrevented: over === false,
            dropPrevented: droppedNow === false,
            pending: state.pendingFiles.length,
            imgW: rect ? Math.round(rect.width) : -1,
            imgH: rect ? Math.round(rect.height) : -1,
            samePage: location.href === beforeHref,
            oversized: docEl.scrollWidth > docEl.clientWidth + 2
                       || docEl.scrollHeight > docEl.clientHeight + 2
          };

          // 直接尝试导航到本地图片：原生壳必须取消，不能只靠页面拖放事件。
          try { location.href = 'file:///D:/fake-drag.png'; } catch (e) {}
          await new Promise(function(r){ setTimeout(r, 300); });
          out.nativeNavBlocked = location.href === beforeHref;
          return JSON.stringify(out);
        })()
        """)
        print("   ", drop_case)
        check("全局拖放防护已安装", drop_case.get("hooked") is True)
        check("拖过窗口时默认导航已被拦住", drop_case.get("overPrevented") is True)
        check("松手放下时默认导航已被拦住", drop_case.get("dropPrevented") is True)
        check("拖进来的图片已加入附件", drop_case.get("pending") == 1,
              "%s 个" % drop_case.get("pending"))
        check("拖入图片仍显示为 22px 缩略图",
              drop_case.get("imgW") == 22 and drop_case.get("imgH") == 22,
              "%sx%s" % (drop_case.get("imgW"), drop_case.get("imgH")))
        check("原生壳会拦截本地图片导航", drop_case.get("nativeNavBlocked") is True)
        check("页面没有被图片替换或撑大",
              drop_case.get("samePage") is True
              and drop_case.get("oversized") is False)
        js("state.pendingFiles = []; renderFiles(); 1")

        print("\n" + "=" * 58)
        print("PASSED: %d   FAILED: %d" % (len(PASS), len(FAIL)))
        for f in FAIL:
            print("   - " + f)
        return 0 if not FAIL else 2

    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        subprocess.run(["taskkill", "/IM", "NovaDesk.exe", "/F"], capture_output=True)
        # 测试往桌面扔的导出物要清掉，别在用户桌面上留垃圾
        if exported_path and os.path.isfile(exported_path):
            try:
                os.remove(exported_path)
            except Exception:
                pass
        # 还原权限设置
        try:
            if backup is not None:
                with open(ini, "wb") as f:
                    f.write(backup)
            elif os.path.isfile(ini):
                os.remove(ini)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

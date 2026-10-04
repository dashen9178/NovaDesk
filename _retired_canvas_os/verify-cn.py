#!/usr/bin/env python3
"""
验证中文版 NovaDesk：
  1. 开机是欢迎页（一行大字 + 一个输入框），不是桌面
  2. 输入指令能开对应窗口
  3. 中文真的渲染出来了（不是豆腐块）
  4. 退出页面再进来，状态能恢复
  5. 顺手存一张截图
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_web_lib import CDP, start_edge, wait_debug, evaluate

HTML = r"D:\DSH工作\NovaDesk\NovaDesk.html"
SHOT = r"D:\DSH工作\NovaDesk\web\_shot_welcome.png"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))


def main():
    proc, port = start_edge(HTML)
    try:
        targets = wait_debug(port)
        if not targets:
            print("FAIL: devtools never came up")
            return 1
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            print("FAIL: no page target")
            return 1
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        time.sleep(3.5)

        print("\n=== 1. boot / welcome screen ===")
        st = evaluate(cdp, """
        (function(){
          var nv = window.__nova;
          if (!nv) return 'NO __nova';
          var k = nv.kernel;
          return JSON.stringify({
            welcome: k.welcome,
            windows: k.windows.length,
            hasArt: !!k.welcomeArt,
            artW: k.welcomeArt ? k.welcomeArt.w : 0,
            artH: k.welcomeArt ? k.welcomeArt.h : 0,
            screen: k.screen.w + 'x' + k.screen.h,
            cjkW: nv.Font.CJK_W, cjkH: nv.Font.CJK_H,
            cellH: nv.Font.CELL_H, asciiDy: nv.Font.ASCII_DY,
            glyphs: nv.Font.cjk ? Object.keys(nv.Font.cjk).length : 0
          });
        })()
        """)
        print("   ", st)
        try:
            d = json.loads(st)
            check("开机进入欢迎页", d["welcome"] is True)
            check("欢迎页没有窗口", d["windows"] == 0)
            check("大字已渲染", d["hasArt"] and d["artW"] > 100 and d["artH"] > 40,
                  "%dx%d" % (d["artW"], d["artH"]))
            check("分辨率 800x450", d["screen"] == "800x450")
            check("中文字库已加载", d["glyphs"] > 100, "%d 个字形" % d["glyphs"])
        except Exception as e:
            check("boot state parseable", False, str(e))

        # 截图：欢迎页
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        if shot and "result" in shot:
            open(SHOT, "wb").write(base64.b64decode(shot["result"]["data"]))
            print("    screenshot -> %s" % SHOT)

        print("\n=== 2. 中文字库覆盖（不是豆腐块）===")
        render = evaluate(cdp, """
        (function(){
          var nv = window.__nova, F = nv.Font, k = nv.kernel;
          // 数亮点区分不了"真字形"和"豆腐块"（空心方框也有墨迹），
          // 所以直接查字库里到底有没有这个码位。
          function has(ch){ return !!(F.cjk && F.cjk[ch.codePointAt(0)]); }

          // 运行时真正会显示的中文，全部检查一遍
          var texts = [];
          k.appNames().forEach(function(n){ texts.push(k.labelOf(n)); });
          k.windows.forEach(function(w){
            texts.push(w.title);
            if (w.app.lines) texts.push(w.app.lines.join(''));
            if (w.app.buf) texts.push(String(w.app.buf));
          });
          var all = texts.join('');
          var missing = [];
          var seen = {};
          for (var i=0;i<all.length;i++){
            var ch = all[i], c = ch.codePointAt(0);
            if (c > 127 && !seen[ch]) { seen[ch]=1; if (!has(ch)) missing.push(ch); }
          }
          // 抽查一批界面上确实用到的字
          var probes = ['终','端','文','件','应','用','时','钟','保','存',
                        '输','入','指','令','未','知','运','行'];
          var bad = [];
          probes.forEach(function(ch){ if (!has(ch)) bad.push(ch); });
          return JSON.stringify({
            checkedTextLen: all.length,
            missing: missing,
            probeMissing: bad,
            totalGlyphs: Object.keys(F.cjk).length
          });
        })()
        """)
        print("   ", render)
        try:
            r = json.loads(render)
            check("界面用到的中文全部有字形", len(r["missing"]) == 0,
                  ("缺: " + "".join(r["missing"])) if r["missing"] else "%d 个字形" % r["totalGlyphs"])
            check("抽查常用字都有字形", len(r["probeMissing"]) == 0,
                  ("缺: " + "".join(r["probeMissing"])) if r["probeMissing"] else "全部命中")
        except Exception as e:
            check("glyph coverage parseable", False, str(e))

        print("\n=== 2b. 缺字会显示成方框而不是消失 ===")
        tofu = evaluate(cdp, """
        (function(){
          var nv = window.__nova, F = nv.Font, C = nv.Canvas;
          function ink(ch){
            var c = new C(40,40);
            F.draw(c, ch, 2, 2, 0xFF000000, 1);
            var n=0; for (var i=0;i<c.px.length;i++) if ((c.px[i]>>>24)!==0) n++;
            return n;
          }
          // 一个几乎不可能用到的生僻字
          var rare = '\\u9fa5';
          return JSON.stringify({rareInFont: !!F.cjk[rare.codePointAt(0)], ink: ink(rare)});
        })()
        """)
        print("   ", tofu)
        try:
            t = json.loads(tofu)
            check("未收录字有可见占位", t["ink"] > 0, "%d 像素" % t["ink"])
        except Exception as e:
            check("tofu probe parseable", False, str(e))

        print("\n=== 3. 输入指令开窗口 ===")
        flow = evaluate(cdp, """
        (function(){
          var nv = window.__nova, E = nv.NovaEvent, k = nv.kernel;
          function type(s){ for (var i=0;i<s.length;i++) nv.host.push(new E.Text(s[i])); }
          type('terminal');
          nv.host.push(new E.Text('\\n'));
          k.tick(); k.tick();
          return JSON.stringify({
            welcome: k.welcome,
            titles: k.windows.map(function(w){return w.title;}),
            names: k.windows.map(function(w){return w.app.name;})
          });
        })()
        """)
        print("   ", flow)
        try:
            f = json.loads(flow)
            check("输入 terminal 后进入桌面", f["welcome"] is False)
            check("打开了一个窗口", len(f["names"]) == 1, str(f["names"]))
            check("窗口标题是中文", f["titles"] and f["titles"][0] == "终端",
                  str(f["titles"]))
        except Exception as e:
            check("flow parseable", False, str(e))

        print("\n=== 4. 未知指令给提示 ===")
        bad = evaluate(cdp, """
        (function(){
          var nv = window.__nova;
          var k = nv.kernel;
          k.welcome = true; k.welcomeInput = ''; k.welcomeMsg = '';
          for (var i=0;i<3;i++) nv.host.push(new nv.NovaEvent.Text('xyz'[i]));
          nv.host.push(new nv.NovaEvent.Text('\\n'));
          k.tick();
          var m = k.welcomeMsg;
          k.welcome = false; k.welcomeInput=''; k.welcomeMsg='';
          k.tick();
          return m;
        })()
        """)
        print("   ", repr(bad))
        check("未知指令有中文提示", isinstance(bad, str) and "未知指令" in bad)

        print("\n=== 5. 终端中文输出 ===")
        term = evaluate(cdp, """
        (function(){
          var nv = window.__nova, E = nv.NovaEvent, k = nv.kernel, F = nv.Font;
          function type(s){ for (var i=0;i<s.length;i++) nv.host.push(new E.Text(s[i])); }
          var before = nv.host.frame.slice(0, 300000);
          type('help'); nv.host.push(new E.Text('\\n'));
          k.tick(); k.tick();
          var after = nv.host.frame;
          var changed = 0;
          for (var i=0;i<before.length;i++) if (before[i]!==after[i]) changed++;
          var w = k.windows[0];
          var lines = w.app.lines || [];
          var all = lines.join('');
          var missing = [], seen = {};
          for (var i=0;i<all.length;i++){
            var ch = all[i], c = ch.codePointAt(0);
            if (c > 127 && !seen[ch]) { seen[ch]=1; if (!(F.cjk && F.cjk[c])) missing.push(ch); }
          }
          return JSON.stringify({
            changed: changed,
            lineCount: lines.length,
            cjkCharsInOutput: Object.keys(seen).length,
            missing: missing,
            tail: lines.slice(-14)
          });
        })()
        """)
        try:
            t = json.loads(term)
            print("    changed pixels:", t["changed"], " lines:", t["lineCount"])
            for ln in t["tail"][:6]:
                print("      | " + ln)
            check("终端输出改变了画面", t["changed"] > 500)
            check("终端输出含中文", t["cjkCharsInOutput"] > 10,
                  "%d 个不同汉字" % t["cjkCharsInOutput"])
            check("help 输出的中文全部有字形", len(t["missing"]) == 0,
                  ("缺: " + "".join(t["missing"])) if t["missing"] else "全覆盖")
        except Exception as e:
            check("terminal probe parseable", False, str(e))

        print("\n=== 6. 退出重进能否恢复 ===")
        before = evaluate(cdp, """
        (function(){
          var k = window.__nova.kernel;
          k.userName = '测试用户';
          // 在终端里写个文件，验证文件系统和应用内容都能还原
          var w = k.windows[0];
          if (w && w.app.lines) {
            w.app.lines.push('#恢复测试标记ABC');
          }
          k.vfs.write('/home/留个记号.txt', '这句话要能活过刷新');
          k.saveState();
          return JSON.stringify({
            titles: k.windows.map(function(x){return x.title;}),
            user: k.userName,
            termLines: w && w.app.lines ? w.app.lines.length : 0,
            hasMarker: !!(w && w.app.lines && w.app.lines.join('').indexOf('恢复测试标记ABC') >= 0),
            vfsHas: k.vfs.exists('/home/留个记号.txt')
          });
        })()
        """)
        print("    before reload:", before)

        cdp.call("Page.reload", {"ignoreCache": True})
        time.sleep(4.0)
        after = evaluate(cdp, """
        (function(){
          var nv = window.__nova;
          if (!nv) return 'NO __nova after reload';
          var k = nv.kernel;
          var w = k.windows[0];
          return JSON.stringify({
            welcome: k.welcome,
            titles: k.windows.map(function(x){return x.title;}),
            user: k.userName,
            termLines: w && w.app.lines ? w.app.lines.length : 0,
            hasMarker: !!(w && w.app.lines && w.app.lines.join('').indexOf('恢复测试标记ABC') >= 0),
            vfsHas: k.vfs.exists('/home/留个记号.txt'),
            vfsContent: k.vfs.read('/home/留个记号.txt')
          });
        })()
        """)
        print("    after  reload:", after)
        try:
            b = json.loads(before)
            a = json.loads(after)
            check("重进后记住了用户名", a["user"] == b["user"], repr(a["user"]))
            check("重进后没有回到欢迎页", a["welcome"] is False)
            check("重进后窗口恢复了", a["titles"] == b["titles"], str(a["titles"]))
            check("重进后终端内容还在", a["hasMarker"] is True,
                  "%d 行" % a["termLines"])
            check("重进后文件系统还在", a["vfsHas"] is True and
                  a["vfsContent"] == "这句话要能活过刷新",
                  repr(a.get("vfsContent")))
        except Exception as e:
            check("reload probe parseable", False, str(e))

        print("\n" + "=" * 58)
        print("PASSED: %d   FAILED: %d" % (len(PASS), len(FAIL)))
        if FAIL:
            print("failed:")
            for f in FAIL:
                print("   - " + f)
        return 0 if not FAIL else 2
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

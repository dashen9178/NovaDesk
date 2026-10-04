#!/usr/bin/env python3
"""
验证 NovaDesk（AI 版）。

先用**伪造的**接口响应测试流式解析、工具调用、Markdown 渲染和存档，
这些都不消耗 token。真实接口只在最后单独测一次。
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_web_lib import CDP, start_edge, wait_debug, evaluate

HTML = r"D:\DSH工作\NovaDesk\NovaDesk.html"
SHOT_DIR = r"D:\DSH工作\NovaDesk\ai\_shots"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail else ""))


# 装一个假的 fetch：第一轮返回工具调用，第二轮返回正文
MOCK_JS = r"""
(function(){
  if (window.__mockInstalled) return 'already';
  window.__mockInstalled = true;
  window.__realFetch = window.fetch;
  window.__calls = [];

  function sse(objs) {
    var body = objs.map(function(o){ return 'data: ' + JSON.stringify(o) + '\n\n'; }).join('') +
               'data: [DONE]\n\n';
    var enc = new TextEncoder();
    var stream = new ReadableStream({
      start: function(c){ c.enqueue(enc.encode(body)); c.close(); }
    });
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  }

  window.__mockScript = function (mode) { window.__mockMode = mode; };

  window.fetch = function (url, opts) {
    if (String(url).indexOf('api.deepseek.com') < 0) {
      return window.__realFetch.apply(this, arguments);
    }
    window.__calls.push(JSON.parse(opts.body));
    var n = window.__calls.length;
    var mode = window.__mockMode || 'tool_then_text';

    if (mode === 'plain_text') {
      return Promise.resolve(sse([
        { choices: [ { delta: { reasoning_content: '先想一下：用户要渲染测试。' } } ] },
        { choices: [ { delta: { reasoning_content: '我应该给一段 Markdown。' } } ] },
        { choices: [ { delta: { content: '# 标题\n\n这是**粗体**和 `代码`。\n\n- 第一项\n- 第二项\n\n```js\nlet a = 1;\n```' } } ] },
        { choices: [ { delta: {}, finish_reason: 'stop' } ] }
      ]));
    }

    if (mode === 'slow') {
      // 一直往外吐字，不主动结束 —— 用来测"停止生成"。
      // 必须响应 opts.signal：真实 fetch 被 abort 时 stream 会报 AbortError，
      // 假的不响应的话，"停止"这条根本测不出来。
      var enc2 = new TextEncoder();
      var ctrl = null;
      var stream = new ReadableStream({
        start: function (c) {
          ctrl = c;
          var n = 0;
          window.__slowTimer = setInterval(function () {
            n++;
            try {
              c.enqueue(enc2.encode('data: ' + JSON.stringify(
                { choices: [ { delta: { content: '字' } } ] }) + '\n\n'));
            } catch (e) { clearInterval(window.__slowTimer); return; }
            if (n > 300) { clearInterval(window.__slowTimer); try { c.close(); } catch (e) {} }
          }, 80);
        },
        cancel: function () { clearInterval(window.__slowTimer); }
      });
      if (opts && opts.signal) {
        if (opts.signal.aborted) {
          clearInterval(window.__slowTimer);
        } else {
          opts.signal.addEventListener('abort', function () {
            window.__slowAborted = true;
            clearInterval(window.__slowTimer);
            window.__slowTimer = null;
            try { ctrl.error(new DOMException('Aborted', 'AbortError')); } catch (e) {}
          });
        }
      }
      return Promise.resolve(new Response(stream, {
        status: 200, headers: { 'Content-Type': 'text/event-stream' }
      }));
    }

    if (mode === 'ask_then_text') {
      if (n === 1) {
        return Promise.resolve(sse([
          { choices: [ { delta: { tool_calls: [ { index: 0, id: 'call_ask', type: 'function',
              function: { name: 'ask_user', arguments: '{"question":"按钮放哪边？","options":["放左边","放上方"]}' } } ] } } ] },
          { choices: [ { delta: {}, finish_reason: 'tool_calls' } ] }
        ]));
      }
      return Promise.resolve(sse([
        { choices: [ { delta: { content: '好，按你说的办。' } } ] },
        { choices: [ { delta: {}, finish_reason: 'stop' } ] }
      ]));
    }

    // 第一轮：只调工具；第二轮：给正文
    if (n === 1) {
      return Promise.resolve(sse([
        { choices: [ { delta: { reasoning_content: '用户要按钮和面板，调两个工具。' } } ] },        { choices: [ { delta: { tool_calls: [ { index: 0, id: 'call_1', type: 'function',
            function: { name: 'add_action', arguments: '' } } ] } } ] },
        { choices: [ { delta: { tool_calls: [ { index: 0,
            function: { arguments: '{"label":"' } } ] } } ] },
        { choices: [ { delta: { tool_calls: [ { index: 0,
            function: { arguments: '测试按钮","prompt":"' } } ] } } ] },
        { choices: [ { delta: { tool_calls: [ { index: 0,
            function: { arguments: '给我看看' } } ] } } ] },
        { choices: [ { delta: { tool_calls: [ { index: 0,
            function: { arguments: '"} ' } } ] } } ] },
        { choices: [ { delta: { tool_calls: [ { index: 1, id: 'call_2', type: 'function',
            function: { name: 'add_action', arguments: '{"label":"左侧按钮","prompt":"左边那个","position":"left"}' } } ] } } ] },
        { choices: [ { delta: { tool_calls: [ { index: 2, id: 'call_3', type: 'function',
            function: { name: 'create_panel', arguments: '{"title":"测试面板","html":"<h3>你好</h3><p>这是 AI 创建的面板。</p>"}' } } ] } } ] },
        { choices: [ { delta: {}, finish_reason: 'tool_calls' } ] }
      ]));
    }
    return Promise.resolve(sse([
      { choices: [ { delta: { content: '已经帮你把按钮和面板都做好了。按钮在输入框上方，点一下就会把预设的内容发给我；面板在右侧，可以常驻显示内容。如果名字或者位置想改，直接跟我说。这段话故意写得很长，用来验证"只改造系统的轮次必须完全不出声"，而不是靠字数阈值蒙过去。' } } ] },
      { choices: [ { delta: {}, finish_reason: 'stop' } ] }
    ]));
  };
  return 'installed';
})()
"""


def main():
    os.makedirs(SHOT_DIR, exist_ok=True)
    proc, port = start_edge(HTML)
    try:
        targets = wait_debug(port)
        if not targets:
            print("FAIL: devtools never came up")
            return 1
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            print("FAIL: no page")
            return 1
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        time.sleep(2.5)

        # 无头浏览器的 profile 是复用的，上一次跑测试留下的 localStorage 会污染结果
        evaluate(cdp, "try{localStorage.clear()}catch(e){}; 'cleared'")
        cdp.call("Page.reload", {"ignoreCache": True})
        time.sleep(2.5)

        print("\n=== 0. 脚本是否成功解析 ===")
        s = evaluate(cdp, """
        (function(){
          var t = {
            state: typeof state, init: typeof init, send: typeof send,
            runTool: typeof runTool, renderMarkdown: typeof renderMarkdown,
            renderPanels: typeof renderPanels, chatStream: typeof chatStream
          };
          var bad = null;
          if (t.init !== 'function') {
            // 整个脚本没跑起来，多半是语法错误 —— 把真正的报错捞出来
            var src = '';
            var ss = document.querySelectorAll('script');
            for (var i = 0; i < ss.length; i++) if (!ss[i].src) src += ss[i].textContent;
            try { new Function(src); bad = 'parse OK but not executed'; }
            catch (e) { bad = e.name + ': ' + e.message; }
          }
          t.__error = bad;
          return JSON.stringify(t);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            ok = (d["init"] == "function" and d["send"] == "function"
                  and d["runTool"] == "function" and d["renderMarkdown"] == "function"
                  and d["renderPanels"] == "function" and d["chatStream"] == "function")
            check("脚本解析并执行成功", ok, d.get("__error") or "")
        except Exception as e:
            check("parse probe parseable", False, str(e))

        print("\n=== 1. 首次加载 ===")
        s = evaluate(cdp, """
        (function(){
          return JSON.stringify({
            title: document.title,
            setupVisible: !document.getElementById('setup').hidden,
            composer: !!document.getElementById('input'),
            chatExists: !!document.getElementById('stream'),
            appTitle: document.getElementById('appTitle').textContent,
            /* 没配 key 时设置图标标红（原来那个小圆点已删） */
            settingsWarn: document.getElementById('btnSettings').classList.contains('warn'),
            err: window.__lastError || null
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("页面标题正确", d["title"] == "NovaDesk")
            check("没 key 时显示配置卡", d["setupVisible"] is True)
            check("输入框存在", d["composer"] is True)
            check("标题显示 NovaDesk", d["appTitle"] == "NovaDesk")
            check("未配置时设置图标标红提醒", d["settingsWarn"] is True)
        except Exception as e:
            check("load probe parseable", False, str(e))

        print("\n=== 2. 填 key 后进入首屏（只要大字+输入框+设置+思考）===")
        s = evaluate(cdp, """
        (function(){
          document.getElementById('setupKey').value = 'sk-mock-for-test';
          document.getElementById('setupSave').click();
          function vis(id){ var e=document.getElementById(id); return !!(e && e.offsetParent); }
          function visSel(q){ var e=document.querySelector(q); return !!(e && e.offsetParent); }
          return JSON.stringify({
            setupVisible: !document.getElementById('setup').hidden,
            settingsWarn: document.getElementById('btnSettings').classList.contains('warn'),
            hero: document.getElementById('chat').classList.contains('hero'),
            heroLine: (document.querySelector('#hero .line') || {}).textContent || null,
            heroVisible: document.querySelector('#hero .line')
              .getBoundingClientRect().height > 0,
            scrollHidden: !document.getElementById('scroll').offsetParent,
            thinkVisible: vis('btnThink'),
            settingsVisible: vis('btnSettings'),
            titleVisible: vis('appTitle'),
            settingsWarn: document.getElementById('btnSettings').classList.contains('warn'),
            panelsBtnVisible: vis('btnPanels'),
            newBtnVisible: vis('btnNew'),
            chips: document.querySelectorAll('.chip').length
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("配置卡关闭", d["setupVisible"] is False)
            check("首屏只有一行大字", d["heroLine"] == "Welcome to NovaDesk", str(d["heroLine"]))
            check("大字可见", d["heroVisible"] is True)
            check("首屏不显示消息区", d["scrollHidden"] is True)
            check("首屏有思考按钮", d["thinkVisible"] is True)
            check("首屏有设置按钮", d["settingsVisible"] is True)
            check("首屏不显示标题", d["titleVisible"] is False)
            check("配好 key 后设置图标不再标红", d["settingsWarn"] is False)
            check("首屏不显示面板按钮", d["panelsBtnVisible"] is False)
            check("首屏不显示新对话按钮", d["newBtnVisible"] is False)
            check("首屏没有多余的芯片按钮", d["chips"] == 0)
        except Exception as e:
            check("setup probe parseable", False, str(e))

        print("\n=== 3. 流式 + 工具调用（伪造接口）===")
        m = evaluate(cdp, MOCK_JS)
        print("    mock:", m)
        s = evaluate(cdp, """
        (function(){
          window.__mockScript('tool_then_text');
          // 注意：直接改 textarea.value 不会触发 input 事件，
          // 而发送按钮的 disabled 状态是靠 input 事件更新的。
          // 不补这一下，按钮还是禁用的，click 根本不会派发。
          var i = document.getElementById('input');
          i.value = '帮我加个按钮和面板';
          i.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnSend').click();
          return 'sent';
        })()
        """)
        # 等流式跑完
        for _ in range(40):
            time.sleep(0.25)
            done = evaluate(cdp, """
            (function(){
              var b = document.getElementById('btnSend');
              return b.classList.contains('stop') ? 'busy' : 'idle';
            })()
            """)
            if done == "idle":
                break
        time.sleep(0.6)

        s = evaluate(cdp, """
        (function(){
          var acts = [].map.call(document.querySelectorAll('.actions .act'),
                                  function(e){ return e.textContent; });
          var rail = [].map.call(document.querySelectorAll('#rail .act'),
                                  function(e){ return e.textContent; });
          var railVisible = !document.getElementById('rail').hidden;
          var panels = [].map.call(document.querySelectorAll('.panel'),
                                  function(e){ return e.querySelector('.t').textContent; });
          // 面板存在不等于侧栏要开 —— 侧栏是「预览」才开的（见第 28 节）
          var statePanels = state.panels.map(function(p){
            return { t: p.title, place: p.place, kind: p.kind, html: p.html || '' };
          });
          var assistant = [].map.call(document.querySelectorAll('.msg.assistant .content'),
                                     function(e){ return e.textContent; });
          var stateAssistant = state.messages.filter(function(m){return m.role==='assistant';})
                                     .map(function(m){return m.content;});
          var silentFlagged = state.messages.filter(function(m){
            return m.role === 'assistant' && typeof isSilentTurn === 'function' && isSilentTurn(m);
          }).length;
          var toolcards = [].map.call(document.querySelectorAll('.toolcard'),
                                      function(e){ return e.textContent; });
          return JSON.stringify({
            apiCalls: window.__calls.length,
            actions: acts, rail: rail, railVisible: railVisible,
            panels: panels, statePanels: statePanels,
            assistant: assistant, toolcards: toolcards,
            stateAssistant: stateAssistant, silentFlagged: silentFlagged,
            assistantDetail: state.messages.filter(function(m){return m.role==='assistant';})
              .map(function(m){
                return { c: (m.content||'').slice(0,18),
                         tn: (m._toolNames||[]).join(','),
                         silent: isSilentTurn(m) };
              }),
            sidebarHidden: document.getElementById('sidebar').hidden,
            panelBody: (document.querySelector('.panel .p-body') || {}).textContent || null
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("接口被调用了两轮（工具后继续）", d["apiCalls"] == 2, "%d 次" % d["apiCalls"])
            check("AI 添加的按钮出现了", "测试按钮" in d["actions"], str(d["actions"]))
            check("position=left 的按钮落在左侧工具栏", "左侧按钮" in d["rail"], str(d["rail"]))
            check("左侧工具栏已显示", d["railVisible"] is True)
            check("左侧按钮没有混进上方那一排", "左侧按钮" not in d["actions"],
                  str(d["actions"]))
            check("AI 创建的面板出现了", any(p["t"] == "测试面板" for p in d["statePanels"]),
                  str([p["t"] for p in d["statePanels"]]))
            # 改了：做好的东西**不许**自己把侧栏顶开（用户明确抱怨过"强制预览"）
            check("**面板侧栏不自动打开**", d["sidebarHidden"] is True)
            check("面板内容存在（在存档里，等着预览）",
                  any("你好" in p["html"] for p in d["statePanels"]),
                  str([p["html"][:30] for p in d["statePanels"]]))
            check("工具调用有可见痕迹（在存档里）",
                  any("添加按钮" in t for t in d["toolcards"]) or d["silentFlagged"] > 0,
                  str(d["toolcards"]))
            check("流式正文确实收到了（存在存档里）",
                  any("帮你把按钮和面板都做好了" in a for a in d["stateAssistant"]),
                  str(d["stateAssistant"]))
            check("调完工具后的确认语不再上屏（安静模式）",
                  len(d["assistant"]) == 0, str(d["assistant"]))
            check("整轮被标记为不上屏", d["silentFlagged"] >= 1,
                  "%d 轮" % d["silentFlagged"])
        except Exception as e:
            check("tool flow parseable", False, str(e))

        print("\n=== 3b. 发出第一条后，输入框落到底部 ===")
        s = evaluate(cdp, """
        (function(){
          var c = document.getElementById('chat');
          var r = document.getElementById('input').getBoundingClientRect();
          return JSON.stringify({
            hero: c.classList.contains('hero'),
            heroLineHidden: !document.querySelector('#hero .line').offsetParent,
            scrollVisible: !!document.getElementById('scroll').offsetParent,
            inputTop: Math.round(r.top), winH: window.innerHeight,
            inLowerHalf: r.top > window.innerHeight * 0.5,
            titleBack: !!document.getElementById('appTitle').offsetParent,
            newBtnBack: !!document.getElementById('btnNew').offsetParent,
            heroModeOff: !document.getElementById('app').classList.contains('hero-mode')
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("已退出首屏布局", d["hero"] is False)
            check("大字已隐藏", d["heroLineHidden"] is True)
            check("消息区已显示", d["scrollVisible"] is True)
            check("输入框落到了页面下半部分", d["inLowerHalf"] is True,
                  "top=%s of %s" % (d["inputTop"], d["winH"]))
            check("进入对话后标题回来了", d["titleBack"] is True)
            check("进入对话后新对话按钮回来了", d["newBtnBack"] is True)
            check("首屏精简模式已退出", d["heroModeOff"] is True)
        except Exception as e:
            check("hero transition parseable", False, str(e))

        print("\n=== 3c. 思考模式开关与请求参数 ===")
        s = evaluate(cdp, """
        (function(){
          var btn = document.getElementById('btnThink');
          var before = state.thinking;
          btn.click();
          var off = state.thinking, clsOff = btn.classList.contains('on');
          btn.click();
          var on = state.thinking, clsOn = btn.classList.contains('on');
          return JSON.stringify({
            before: before, off: off, clsOff: clsOff, on: on, clsOn: clsOn,
            sentThinking: window.__calls.map(function(c){ return c.thinking && c.thinking.type; }),
            sentModels: window.__calls.map(function(c){ return c.model; })
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("开关初始为开", d["before"] is True)
            check("点一下关掉", d["off"] is False and d["clsOff"] is False)
            check("再点一下打开", d["on"] is True and d["clsOn"] is True)
            check("请求里带上了 thinking 参数",
                  len(d["sentThinking"]) > 0 and all(t == "enabled" for t in d["sentThinking"]),
                  str(d["sentThinking"]))
            check("请求模型是 deepseek-flash",
                  all(m == "deepseek-flash" for m in d["sentModels"]), str(d["sentModels"]))
        except Exception as e:
            check("thinking probe parseable", False, str(e))

        print("\n=== 4. Markdown 渲染 ===")
        s = evaluate(cdp, """
        (function(){
          window.__mockScript('plain_text');
          var i = document.getElementById('input');
          i.value = '渲染测试';
          i.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnSend').click();
          return 'sent';
        })()
        """)
        for _ in range(40):
            time.sleep(0.25)
            st = evaluate(cdp, "(function(){return document.getElementById('btnSend').classList.contains('stop')?'busy':'idle';})()")
            if st == "idle":
                break
        time.sleep(0.6)
        s = evaluate(cdp, """
        (function(){
          var last = document.querySelectorAll('.msg.assistant .content');
          var el = last[last.length - 1];
          var msg = el.closest('.msg');
          var tb = msg.querySelector('.think-body');
          return JSON.stringify({
            h1: !!el.querySelector('h1'),
            strong: !!el.querySelector('strong'),
            inlineCode: !!el.querySelector('code') && !el.querySelector('pre'),
            ul: !!el.querySelector('ul li'),
            pre: !!el.querySelector('pre code'),
            preText: el.querySelector('pre') ? el.querySelector('pre').textContent : null,
            think: !!msg.querySelector('details.think'),
            thinkText: tb ? tb.textContent : null,
            thinkOpenByDefault: msg.querySelector('details.think')
              ? msg.querySelector('details.think').open : null
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("标题渲染", d["h1"] is True)
            check("粗体渲染", d["strong"] is True)
            check("列表渲染", d["ul"] is True)
            check("代码块渲染", d["pre"] is True, repr(d["preText"]))
            check("代码块内容未被 HTML 转义破坏", d["preText"] == "let a = 1;", repr(d["preText"]))
            check("思考过程有折叠块", d["think"] is True)
            check("思考内容已填入", (d["thinkText"] or "").find("渲染测试") >= 0,
                  repr(d["thinkText"]))
            check("**思考过程默认展开（看得见）**", d["thinkOpenByDefault"] is True)
            check("普通长回复照常显示（没被静默误伤）", d["h1"] is True and d["ul"] is True)
        except Exception as e:
            check("markdown probe parseable", False, str(e))

        # 截图
        shot = cdp.call("Page.captureScreenshot", {"format": "png"})
        if shot and "result" in shot:
            p = os.path.join(SHOT_DIR, "01-chat.png")
            open(p, "wb").write(base64.b64decode(shot["result"]["data"]))
            print("    screenshot -> %s" % p)

        print("\n=== 5. 重载后恢复（含 AI 改造的东西）===")
        before = evaluate(cdp, """
        (function(){
          return JSON.stringify({
            msgs: document.querySelectorAll('.msg').length,
            actions: document.querySelectorAll('.actions .act').length,
            panels: document.querySelectorAll('.panel').length
          });
        })()
        """)
        print("    before:", before)
        cdp.call("Page.reload", {"ignoreCache": True})
        time.sleep(3.0)
        after = evaluate(cdp, """
        (function(){
          return JSON.stringify({
            setupVisible: !document.getElementById('setup').hidden,
            msgs: document.querySelectorAll('.msg').length,
            actions: document.querySelectorAll('.actions .act').length,
            panels: document.querySelectorAll('.panel').length
          });
        })()
        """)
        print("    after :", after)
        try:
            b = json.loads(before); a = json.loads(after)
            check("重载后不再要求填 key", a["setupVisible"] is False)
            check("对话记录恢复", a["msgs"] == b["msgs"], "%d -> %d" % (b["msgs"], a["msgs"]))
            check("AI 加的按钮还在", a["actions"] == b["actions"],
                  "%d -> %d" % (b["actions"], a["actions"]))
            check("AI 建的面板还在", a["panels"] == b["panels"],
                  "%d -> %d" % (b["panels"], a["panels"]))
        except Exception as e:
            check("reload probe parseable", False, str(e))

        print("\n=== 6. 控件的 HTML：默认允许脚本（权限给到最大）===")
        s = evaluate(cdp, """
        (function(){
          state.panels = [];
          state.allowScript = true;          // 默认
          state.panelsOpen = true;           // 侧栏默认是关的，这里要验渲染就得显式开
          window.__pwned = 0;
          runTool('create_panel', {
            title: '脚本测试',
            html: '<p id="live">正常内容</p>' +
                  '<script>window.__pwned = 1;' +
                  'document.getElementById("live").textContent = "脚本跑过了";<\\/script>'
          });
          renderPanels();
          var box = null;
          [].forEach.call(document.querySelectorAll('.panel'), function(p){
            if (p.querySelector('.t').textContent === '脚本测试') box = p;
          });
          return JSON.stringify({
            hasScript: box ? !!box.querySelector('script') : null,
            bodyText: box ? box.querySelector('.p-body').textContent : null,
            pwned: window.__pwned,
            allowScript: state.allowScript
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("默认开启脚本权限", d["allowScript"] is True)
            check("script 标签保留着", d["hasScript"] is True)
            check("**脚本真的执行了**", d["pwned"] == 1, "pwned=%s" % d["pwned"])
            check("脚本能改自己控件里的内容",
                  "脚本跑过了" in (d["bodyText"] or ""), repr(d["bodyText"]))
        except Exception as e:
            check("script-enabled probe parseable", False, str(e))

        print("\n=== 6b. 关掉脚本权限后会被剥干净 ===")
        s = evaluate(cdp, """
        (function(){
          state.panels = [];
          state.allowScript = false;
          state.panelsOpen = true;           // 同上
          window.__pwned2 = 0;
          runTool('create_panel', {
            title: '清洗测试',
            html: '<p>正常内容</p><script>window.__pwned2=1<\\/script>' +
                  '<img src=x onerror="window.__pwned2=2">' +
                  '<a href="javascript:window.__pwned2=3">x</a>'
          });
          renderPanels();
          var box = null;
          [].forEach.call(document.querySelectorAll('.panel'), function(p){
            if (p.querySelector('.t').textContent === '清洗测试') box = p;
          });
          return JSON.stringify({
            hasScript: box ? !!box.querySelector('script') : null,
            hasOnerror: box ? !!box.querySelector('img[onerror]') : null,
            hasJsHref: box ? !!box.querySelector('a[href^="javascript:"]') : null,
            keptText: box ? box.querySelector('.p-body').textContent.indexOf('正常内容') >= 0 : null,
            pwned: window.__pwned2 || 0
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("script 标签被剥掉", d["hasScript"] is False)
            check("onerror 属性被剥掉", d["hasOnerror"] is False)
            check("javascript: 链接被剥掉", d["hasJsHref"] is False)
            check("正常内容保留", d["keptText"] is True)
            check("没有任何脚本被执行", d["pwned"] == 0, "pwned=%s" % d["pwned"])
            evaluate(cdp, "state.allowScript = true; state.panels = []; "
                          "state.panelsOpen = false; renderAll(); 1")
        except Exception as e:
            check("sanitize probe parseable", False, str(e))

        print("\n=== 7. 三种按钮行为 ===")
        # 第 5 组刷新过页面，之前注入的假 fetch 已经没了，要重新装
        m = evaluate(cdp, MOCK_JS)
        print("    mock:", m)
        s = evaluate(cdp, """
        (function(){
          window.__mockScript('plain_text');
          runTool('add_action', {label:'发送型', prompt:'发送型内容', behavior:'send'});
          runTool('add_action', {label:'填充型', prompt:'填充型内容', behavior:'fill'});
          runTool('add_action', {label:'执行型', prompt:'toggle-thinking', behavior:'run'});
          renderAll();
          return JSON.stringify({
            behaviors: state.actions.map(function(a){ return a.label + '=' + a.behavior; })
          });
        })()
        """)
        print("   ", s)
        try:
            # 前面几组已经加过按钮了，所以只检查这三个在不在
            got = set(json.loads(s)["behaviors"])
            check("三种行为都存下来了",
                  {"发送型=send", "填充型=fill", "执行型=run"}.issubset(got), s)
        except Exception as e:
            check("behaviors probe parseable", False, str(e))

        # 点"填充型"：应该只进输入框，不发请求
        s = evaluate(cdp, """
        (function(){
          var before = window.__calls.length;
          var btns = document.querySelectorAll('.actions .act');
          for (var i = 0; i < btns.length; i++) {
            if (btns[i].textContent === '填充型') btns[i].click();
          }
          return JSON.stringify({
            input: document.getElementById('input').value,
            callsBefore: before, callsAfter: window.__calls.length,
            sendDisabled: document.getElementById('btnSend').disabled
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("填充型：内容进了输入框", d["input"] == "填充型内容", repr(d["input"]))
            check("填充型：没有发请求", d["callsAfter"] == d["callsBefore"],
                  "%d -> %d" % (d["callsBefore"], d["callsAfter"]))
            check("填充型：发送按钮可点（等着用户发）", d["sendDisabled"] is False)
        except Exception as e:
            check("fill behavior parseable", False, str(e))

        # 清掉输入框，点"执行型"：应该切换思考模式，且不发请求
        s = evaluate(cdp, """
        (function(){
          var i = document.getElementById('input');
          i.value = ''; i.dispatchEvent(new Event('input', {bubbles: true}));
          var before = window.__calls.length;
          var thBefore = state.thinking;
          var btns = document.querySelectorAll('.actions .act');
          for (var k = 0; k < btns.length; k++) {
            if (btns[k].textContent === '执行型') btns[k].click();
          }
          return JSON.stringify({
            thinkingBefore: thBefore, thinkingAfter: state.thinking,
            callsBefore: before, callsAfter: window.__calls.length,
            toastShown: document.getElementById('toast').classList.contains('show'),
            toastText: document.getElementById('toast').textContent
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("执行型：本地动作生效了", d["thinkingAfter"] != d["thinkingBefore"],
                  "%s -> %s" % (d["thinkingBefore"], d["thinkingAfter"]))
            check("执行型：没有发请求", d["callsAfter"] == d["callsBefore"],
                  "%d -> %d" % (d["callsBefore"], d["callsAfter"]))
            check("执行型：有轻提示反馈", d["toastShown"] is True, d["toastText"])
        except Exception as e:
            check("run behavior parseable", False, str(e))

        print("\n=== 8. AI 拿不准时弹窗问用户 ===")
        evaluate(cdp, """
        (function(){
          window.__mockScript('ask_then_text');
          var i = document.getElementById('input');
          i.value = '帮我加个按钮';
          i.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnSend').click();
          return 1;
        })()
        """)
        # 等弹窗出现
        appeared = False
        for _ in range(40):
            time.sleep(0.25)
            st = evaluate(cdp, "(function(){return document.getElementById('ask').hidden ? 'hidden' : 'shown';})()")
            if st == "shown":
                appeared = True
                break
        check("模型调 ask_user 后弹出了选择框", appeared)

        s = evaluate(cdp, """
        (function(){
          return JSON.stringify({
            question: document.getElementById('askQ').textContent,
            options: [].map.call(document.querySelectorAll('.ask-opt'), function(e){return e.textContent;}),
            hasFreeInput: !!document.getElementById('askInput')
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("弹窗显示的是模型的问题", d["question"] == "按钮放哪边？", d["question"])
            check("选项渲染出来了", d["options"] == ["放左边", "放上方"], str(d["options"]))
            check("有自己输入的框", d["hasFreeInput"] is True)
        except Exception as e:
            check("ask dialog parseable", False, str(e))

        # 点第一个选项，等这轮跑完
        evaluate(cdp, """
        (function(){
          var o = document.querySelectorAll('.ask-opt');
          if (o.length) o[0].click();
          return 1;
        })()
        """)
        for _ in range(40):
            time.sleep(0.25)
            st = evaluate(cdp, "(function(){return document.getElementById('btnSend').classList.contains('stop')?'busy':'idle';})()")
            if st == "idle":
                break
        time.sleep(0.5)

        s = evaluate(cdp, """
        (function(){
          var toolMsgs = state.messages.filter(function(m){return m.role==='tool';});
          var lastAsk = null;
          for (var i=toolMsgs.length-1;i>=0;i--){
            if (String(toolMsgs[i].content).indexOf('用户回答') >= 0) { lastAsk = toolMsgs[i].content; break; }
          }
          var els = document.querySelectorAll('.msg.assistant .content');
          return JSON.stringify({
            dialogClosed: document.getElementById('ask').hidden,
            toolAnswer: lastAsk,
            finalText: els.length ? els[els.length-1].textContent : null,
            apiCalls: window.__calls.length
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("选完后弹窗关闭", d["dialogClosed"] is True)
            check("用户的选择被回灌给模型", "放左边" in (d["toolAnswer"] or ""), str(d["toolAnswer"]))
            check("模型拿到回答后继续把话说完",
                  "按你说的办" in (d["finalText"] or ""), str(d["finalText"]))
        except Exception as e:
            check("ask result parseable", False, str(e))

        print("\n=== 9. 畸形历史清洗（400 的根因）===")
        s = evaluate(cdp, """
        (function(){
          function tc(id, name){
            return {id:id, type:'function', function:{name:name, arguments:'{}'}};
          }
          var cases = {};

          // a) 孤儿 tool_calls：调了工具但中途关页面，没有 tool 结果
          state.messages = [
            {role:'user', content:'加个按钮'},
            {role:'assistant', content:'', tool_calls:[tc('c1','add_action')]},
            {role:'user', content:'再加一个'}
          ];
          var built = buildApiMessages();
          cases.orphan = {
            hasToolCalls: built.filter(function(m){return m.tool_calls;}).length,
            toolMsgs: built.filter(function(m){return m.role==='tool';}).length,
            roles: built.map(function(m){return m.role;}).join(',')
          };

          // b) 孤立的 tool 消息
          state.messages = [
            {role:'user', content:'你好'},
            {role:'tool', tool_call_id:'ghost', content:'无主的工具结果'},
            {role:'user', content:'在吗'}
          ];
          built = buildApiMessages();
          cases.ghostTool = {
            toolMsgs: built.filter(function(m){return m.role==='tool';}).length,
            roles: built.map(function(m){return m.role;}).join(',')
          };

          // c) tool_call_id 对不上
          state.messages = [
            {role:'user', content:'加按钮'},
            {role:'assistant', content:'好的', tool_calls:[tc('c1','add_action')]},
            {role:'tool', tool_call_id:'c999', content:'对不上'},
            {role:'user', content:'继续'}
          ];
          built = buildApiMessages();
          cases.mismatch = {
            hasToolCalls: built.filter(function(m){return m.tool_calls;}).length,
            toolMsgs: built.filter(function(m){return m.role==='tool';}).length,
            keptAssistantText: built.some(function(m){return m.role==='assistant' && m.content==='好的';}),
            roles: built.map(function(m){return m.role;}).join(',')
          };

          // d) 正常配对的历史不能被误伤
          state.messages = [
            {role:'user', content:'加按钮'},
            {role:'assistant', content:'', tool_calls:[tc('c1','add_action')]},
            {role:'tool', tool_call_id:'c1', content:'已添加'},
            {role:'assistant', content:'好了'},
            {role:'user', content:'谢谢'}
          ];
          built = buildApiMessages();
          cases.healthy = {
            hasToolCalls: built.filter(function(m){return m.tool_calls;}).length,
            toolMsgs: built.filter(function(m){return m.role==='tool';}).length,
            roles: built.map(function(m){return m.role;}).join(',')
          };

          // e) 空 tool_calls 数组绝不能发出去
          state.messages = [
            {role:'user', content:'嗨'},
            {role:'assistant', content:'在的', tool_calls:[]}
          ];
          built = buildApiMessages();
          cases.emptyArray = {
            hasToolCalls: built.filter(function(m){return m.tool_calls;}).length
          };

          return JSON.stringify(cases);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("孤儿 tool_calls 被清掉", d["orphan"]["hasToolCalls"] == 0, str(d["orphan"]))
            check("孤儿对应的 tool 消息也没了", d["orphan"]["toolMsgs"] == 0, str(d["orphan"]))
            check("无主的 tool 消息被丢掉", d["ghostTool"]["toolMsgs"] == 0, str(d["ghostTool"]))
            check("id 对不上的那组被清掉", d["mismatch"]["hasToolCalls"] == 0
                  and d["mismatch"]["toolMsgs"] == 0, str(d["mismatch"]))
            check("id 对不上时保留了 assistant 的文字",
                  d["mismatch"]["keptAssistantText"] is True, str(d["mismatch"]))
            check("正常配对的历史没被误伤",
                  d["healthy"]["hasToolCalls"] == 1 and d["healthy"]["toolMsgs"] == 1,
                  str(d["healthy"]))
            check("空 tool_calls 数组不会发出去",
                  d["emptyArray"]["hasToolCalls"] == 0, str(d["emptyArray"]))
        except Exception as e:
            check("sanitize history parseable", False, str(e))

        print("\n=== 9b. 改造轮次：没说话才隐藏，说了话必须留着 ===")
        s = evaluate(cdp, """
        (function(){
          function tc(id, name, args){
            return {id:id, type:'function', function:{name:name, arguments:args}};
          }
          // 用真实代码里的收尾判定跑几遍
          function judge(turnMsgs, names, toolRan){
            var silent = false;
            if (state.quietBuild && turnMsgs.length && names.length &&
                names.indexOf('ask_user') < 0) {
              var allMod = true;
              for (var i=0;i<names.length;i++) if (!MOD_TOOLS[names[i]]) allMod = false;
              if (allMod) silent = true;
            }
            if (silent) {
              // 只收起"从没上过屏"的；_shown 的必须留着
              turnMsgs.forEach(function(m){ if (!m._shown) m._silent = true; });
            }
            return silent;
          }

          var out = {};

          // A) 只调 create_panel，一个字没说 -> 应该隐藏
          state.messages = [
            {role:'user', content:'做个时钟'},
            {role:'assistant', content:'', reasoning_content:'建个时钟。',
             tool_calls:[tc('c1','create_panel','{"title":"时钟","kind":"clock","place":"left"}')]},
            {role:'tool', tool_call_id:'c1', content:'在左侧装了实时时钟「时钟」'},
            {role:'assistant', content:''}
          ];
          var aMsgs = state.messages.filter(function(m){return m.role==='assistant';});
          out.aSilent = judge(aMsgs, ['create_panel']);
          renderMessages();
          out.aShown = document.querySelectorAll('.msg.assistant .content').length;

          // B) 既调了工具又说了话 —— 但那段话是"调完工具之后"才说的，
          //    根本没上过屏，所以收起来不会让人看到东西消失
          state.messages = [
            {role:'user', content:'做个时钟'},
            {role:'assistant', content:'',
             reasoning_content:'先建控件，再确认一句。',
             tool_calls:[tc('c2','create_panel','{"title":"时钟2","kind":"clock"}')]},
            {role:'tool', tool_call_id:'c2', content:'已创建'},
            {role:'assistant', content:'时钟放在右侧了，需要改样式跟我说。'}
          ];
          // 按真实规则：调完改造工具后文字不上屏，所以 _shown 是 false
          var bMsgs = state.messages.filter(function(m){return m.role==='assistant';});
          out.bSilent = judge(bMsgs, ['create_panel'], false);

          // C) 文字在调工具**之前**就说了 → 已经上屏，必须留着
          state.messages = [
            {role:'user', content:'帮我看看'},
            {role:'assistant', content:'我先看一下当前的设置。', _shown:true,
             reasoning_content:'先说明一句再动手。',
             tool_calls:[tc('c3','set_title','{"title":"新标题"}')]},
            {role:'tool', tool_call_id:'c3', content:'标题已改'},
            {role:'assistant', content:''}
          ];
          var cMsgs = state.messages.filter(function(m){return m.role==='assistant';});
          out.cSilent = judge(cMsgs, ['set_title'], false);
          renderMessages();
          out.cShown = document.querySelectorAll('.msg.assistant .content').length;
          out.cTexts = [].map.call(document.querySelectorAll('.msg.assistant .content'),
                                   function(e){ return e.textContent.slice(0,20); });
          out.cThinkVisible = document.querySelectorAll('.msg.assistant details.think').length;
          out.cThinkOpen = (function(){
            var d = document.querySelector('.msg.assistant details.think');
            return d ? d.open : null;
          })();
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("一个字没说 -> 隐藏", d["aSilent"] is True and d["aShown"] == 0,
                  "silent=%s shown=%d" % (d["aSilent"], d["aShown"]))
            check("调完工具才说的话 -> 收起（它从没上过屏）",
                  d["bSilent"] is True)
            check("**调工具之前说的话 -> 必须留着（不会写完又删）**",
                  d["cShown"] >= 1, "shown=%d" % d["cShown"])
            check("那句话说还在", any("我先看一下" in t for t in d["cTexts"]),
                  str(d["cTexts"]))
            check("静默轮次也能看到思考过程", d["cThinkVisible"] >= 1)
            check("思考过程默认展开", d["cThinkOpen"] is True)
        except Exception as e:
            check("quiet build parseable", False, str(e))

        print("\n=== 9c. 每轮都注入「当下情况」（日期 + 跑在哪儿）===")
        s = evaluate(cdp, """
        (function(){
          var savedNative = window.NovaNative;
          // 有原生桥 = APK
          window.NovaNative = { get: function(){} };
          var apk = buildApiMessages()[0].content;
          delete window.NovaNative;
          var web = buildApiMessages()[0].content;
          window.NovaNative = savedNative;
          var t = nowParts();
          return JSON.stringify({
            apkHasBlock: apk.indexOf('【当下情况】') >= 0,
            apkHasDate: apk.indexOf(t.date) >= 0,
            apkHasTime: apk.indexOf(t.time.slice(0, 5)) >= 0,
            apkSaysApk: apk.indexOf('安卓版') >= 0,
            webSaysWeb: web.indexOf('网页版') >= 0,
            webWarnsDevice: web.indexOf('device 工具改不了设备状态') >= 0,
            apkNotWarn: apk.indexOf('device 工具改不了设备状态') < 0
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("系统消息里带当前日期", d["apkHasDate"] is True)
            check("系统消息里带当前时间", d["apkHasTime"] is True)
            check("在 APK 里就告诉它能改设备", d["apkSaysApk"] is True)
            check("**在网页版就提醒它改不了，别吹牛**",
                  d["webSaysWeb"] is True and d["webWarnsDevice"] is True
                  and d["apkNotWarn"] is True)
        except Exception as e:
            check("live context probe parseable", False, str(e))

        print("\n=== 10. 停止生成 ===")
        evaluate(cdp, """
        (function(){
          window.__mockScript('slow');
          var i = document.getElementById('input');
          i.value = '讲个很长的故事';
          i.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnSend').click();
          return 1;
        })()
        """)
        time.sleep(1.6)
        s = evaluate(cdp, """
        (function(){
          var btn = document.getElementById('btnSend');
          var els = document.querySelectorAll('.msg.assistant .content');
          return JSON.stringify({
            isStopStyle: btn.classList.contains('stop'),
            disabled: btn.disabled,
            title: btn.title,
            hasSquareIcon: btn.innerHTML.indexOf('rect') >= 0,
            busy: btn.classList.contains('stop'),
            textLen: els.length ? els[els.length-1].textContent.length : 0
          });
        })()
        """)
        print("   生成中:", s)
        try:
            d = json.loads(s)
            check("生成中发送按钮变成停止样式", d["isStopStyle"] is True)
            check("**停止按钮没有被禁用（关键）**", d["disabled"] is False)
            check("提示文字说明是停止", "停止" in d["title"], d["title"])
            check("图标换成了方块", d["hasSquareIcon"] is True)
            check("正在流式输出", d["textLen"] > 0, "%d 字" % d["textLen"])
        except Exception as e:
            check("stop visible parseable", False, str(e))

        # 点"发送按钮"（此时它是停止）
        evaluate(cdp, "document.getElementById('btnSend').click(); 1")
        for _ in range(40):
            time.sleep(0.25)
            st = evaluate(cdp, "(function(){return document.getElementById('btnSend').classList.contains('stop')?'busy':'idle';})()")
            if st == "idle":
                break
        time.sleep(0.5)
        s = evaluate(cdp, """
        (function(){
          var btn = document.getElementById('btnSend');
          var els = document.querySelectorAll('.msg.assistant .content');
          var last = els.length ? els[els.length-1].textContent : '';
          return JSON.stringify({
            busy: btn.classList.contains('stop'),
            backToSendIcon: btn.innerHTML.indexOf('rect') < 0,
            lenAtStop: last.length,
            timerCleared: !window.__slowTimer,
            abortFired: window.__slowAborted === true
          });
        })()
        """)
        print("   停止后:", s)
        time.sleep(1.2)
        s2 = evaluate(cdp, """
        (function(){
          var els = document.querySelectorAll('.msg.assistant .content');
          return (els.length ? els[els.length-1].textContent.length : 0);
        })()
        """)
        try:
            d = json.loads(s)
            check("点停止后结束生成", d["busy"] is False)
            check("按钮恢复成发送图标", d["backToSendIcon"] is True)
            check("底层请求真的被 abort 了", d["abortFired"] is True)
            check("底层请求被取消", d["timerCleared"] is True)
            check("停止后不再继续吐字（1.2 秒后长度没变）",
                  int(s2) == d["lenAtStop"], "%s -> %s" % (d["lenAtStop"], s2))
        except Exception as e:
            check("stop result parseable", False, str(e))

        print("\n=== 11. 按钮本地弹框：不碰对话、不调模型 ===")
        s = evaluate(cdp, """
        (function(){
          state.messages = [{role:'user', content:'你好'}];
          state.actions = [];
          runTool('add_action', {label:'看时间', prompt:'show-time', behavior:'run'});
          renderAll();
          var msgsBefore = state.messages.length;
          var callsBefore = (window.__calls || []).length;
          var btns = document.querySelectorAll('.actions .act');
          for (var i=0;i<btns.length;i++) if (btns[i].textContent === '看时间') btns[i].click();
          var p = document.getElementById('popup');
          return JSON.stringify({
            popupVisible: !p.hidden,
            popupShown: p.classList.contains('show'),
            title: document.getElementById('popupTitle').textContent,
            main: document.getElementById('popupMain').textContent,
            sub: document.getElementById('popupSub').textContent,
            msgsBefore: msgsBefore, msgsAfter: state.messages.length,
            callsBefore: callsBefore, callsAfter: (window.__calls || []).length,
            panelsBtnExists: !!document.getElementById('btnPanels'),
            railBtns: document.querySelectorAll('#rail .act').length
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("面板按钮已删除", d["panelsBtnExists"] is False)
            check("点按钮弹出了小框", d["popupVisible"] is True)
            check("小框标题是「现在时间」", d["title"] == "现在时间", d["title"])
            import re as _re
            check("小框里是真实时间 HH:MM:SS",
                  bool(_re.match(r"^\d{2}:\d{2}:\d{2}$", d["main"])), d["main"])
            check("小框里有日期和星期", "星期" in d["sub"] and "年" in d["sub"], d["sub"])
            check("**没有往对话里加任何消息**",
                  d["msgsAfter"] == d["msgsBefore"],
                  "%d -> %d" % (d["msgsBefore"], d["msgsAfter"]))
            check("**没有调用模型**",
                  d["callsAfter"] == d["callsBefore"],
                  "%d -> %d" % (d["callsBefore"], d["callsAfter"]))
        except Exception as e:
            check("popup probe parseable", False, str(e))

        # 关掉小框（先等一帧，让过渡类生效并确认它确实加上了）
        time.sleep(0.3)
        s = evaluate(cdp, """
        (function(){
          return JSON.stringify({
            shown: document.getElementById('popup').classList.contains('show')
          });
        })()
        """)
        try:
            check("小框带上了过渡状态", json.loads(s)["shown"] is True)
        except Exception:
            pass

        s = evaluate(cdp, """
        (function(){
          document.getElementById('popupClose').click();
          return 'clicked';
        })()
        """)
        time.sleep(0.4)
        s = evaluate(cdp, """
        (function(){
          var p = document.getElementById('popup');
          return JSON.stringify({
            hidden: p.hidden,
            maskHidden: document.getElementById('popupMask').hidden
          });
        })()
        """)
        try:
            d = json.loads(s)
            check("点「知道了」能关掉小框", d["hidden"] is True)
            check("遮罩也一起收起", d["maskHidden"] is True)
        except Exception as e:
            check("popup close parseable", False, str(e))

        print("\n=== 11b. popup: 自定义文字也能弹 ===")
        s = evaluate(cdp, """
        (function(){
          runLocalAction('popup:小抄|第一行\\n第二行');
          var p = document.getElementById('popup');
          return JSON.stringify({
            visible: !p.hidden,
            title: document.getElementById('popupTitle').textContent,
            main: document.getElementById('popupMain').textContent,
            small: document.getElementById('popupMain').className.indexOf('small') >= 0
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("自定义 popup 能弹出", d["visible"] is True)
            check("标题和正文都对", d["title"] == "小抄" and "第二行" in d["main"], s)
            check("长文字用小字号", d["small"] is True)
        except Exception as e:
            check("custom popup parseable", False, str(e))
        evaluate(cdp, "document.getElementById('popupClose').click(); 1")

        print("\n=== 12. HTML 转文本 ===")
        s = evaluate(cdp, """
        (function(){
          var html = '<html><head><style>body{color:red}</style>' +
            '<script>var x=1;</script></head>' +
            '<body><h1>标题</h1><p>第一段 &amp; 实体</p>' +
            '<ul><li>甲</li><li>乙</li></ul>' +
            '<!-- 注释 --><div>末尾&nbsp;文字</div></body></html>';
          var t = htmlToText(html);
          return JSON.stringify({
            text: t,
            noScript: t.indexOf('var x=1') < 0,
            noStyle: t.indexOf('color:red') < 0,
            noComment: t.indexOf('注释') < 0,
            entityDecoded: t.indexOf('&') >= 0 && t.indexOf('&amp;') < 0,
            hasAll: ['标题','第一段','甲','乙','末尾'].every(function(k){ return t.indexOf(k) >= 0; })
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("script 内容被去掉", d["noScript"] is True)
            check("style 内容被去掉", d["noStyle"] is True)
            check("注释被去掉", d["noComment"] is True)
            check("HTML 实体被解码", d["entityDecoded"] is True)
            check("正文都保留了", d["hasAll"] is True)
        except Exception as e:
            check("htmlToText parseable", False, str(e))

        print("\n=== 13. 联网工具（伪造原生桥）===")
        s = evaluate(cdp, """
        (async function(){
          window.__netCalls = [];
          // 装一个假的安卓原生桥
          window.NovaNative = {
            get: function(id, url){
              window.__netCalls.push(url);
              var body;
              if (url.indexOf('duckduckgo') >= 0) {
                body = '<html><body>' +
                  '<a href="https://example.com/a">第一条结果标题</a>' +
                  '<a href="https://example.com/b">第二条结果标题</a>' +
                  '<a href="https://duckduckgo.com/y.js">引擎自己的链接</a>' +
                  '</body></html>';
              } else {
                body = '<html><head><script>bad()</script></head>' +
                  '<body><h1>页面标题</h1><p>这是网页正文内容，' +
                  '用来验证 web_open 能把 HTML 变成可读文本。</p></body></html>';
              }
              setTimeout(function(){
                window.__novaNetDone(id, { ok:true, status:200, body:body, error:'' });
              }, 10);
            }
          };

          var search = await webSearch('测试关键词');
          var open = await webOpen('https://example.com/page');
          return JSON.stringify({
            hasNative: hasNativeNet(),
            calls: window.__netCalls.length,
            searchHasFirst: search.indexOf('第一条结果标题') >= 0,
            searchHasSecond: search.indexOf('第二条结果标题') >= 0,
            searchFiltersEngine: search.indexOf('duckduckgo.com/y.js') < 0,
            searchMentionsSource: search.indexOf('来源') >= 0,
            openHasTitle: open.indexOf('页面标题') >= 0,
            openHasBody: open.indexOf('这是网页正文内容') >= 0,
            openStripsScript: open.indexOf('bad()') < 0
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("检测到原生联网能力", d["hasNative"] is True)
            check("搜索走了原生桥", d["calls"] >= 2, "%d 次请求" % d["calls"])
            check("搜索结果标题被提取", d["searchHasFirst"] is True and d["searchHasSecond"] is True)
            check("搜索引擎自己的链接被过滤", d["searchFiltersEngine"] is True)
            check("搜索结果标明了来源", d["searchMentionsSource"] is True)
            check("web_open 抓到正文", d["openHasTitle"] is True and d["openHasBody"] is True)
            check("web_open 去掉了脚本", d["openStripsScript"] is True)
        except Exception as e:
            check("web tools parseable", False, str(e))

        print("\n=== 13b. 没有原生桥时（网页版）给明确提示 ===")
        s = evaluate(cdp, """
        (async function(){
          var saved = window.NovaNative;
          delete window.NovaNative;
          var r = await webGet('https://example.com');
          window.NovaNative = saved;
          return JSON.stringify({ ok: r.ok, error: String(r.error || '').slice(0, 60) });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("网页版抓不了会明说跨域", d["ok"] is False and len(d["error"]) > 0,
                  d["error"])
        except Exception as e:
            check("browser fallback parseable", False, str(e))

        print("\n=== 11c. 字面 \\n 要还原成真换行（模型两层转义的坑）===")
        s = evaluate(cdp, """
        (function(){
          // 模拟模型把换行写成 \\\\n，JSON 解析后是字面的 反斜杠+n
          runLocalAction('popup:系统信息|第一行\\\\n第二行\\\\n\\\\n第三段\\\\t带制表');
          var m = document.getElementById('popupMain');
          var t = m.textContent;
          return JSON.stringify({
            raw: t,
            noLiteralBackslashN: t.indexOf('\\\\n') < 0,
            lineCount: t.split('\\n').length,
            hasTab: t.indexOf('\\t') >= 0,
            preWrap: getComputedStyle(m).whiteSpace
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("字面 \\n 被还原了", d["noLiteralBackslashN"] is True, repr(d["raw"]))
            check("真的产生了多行", d["lineCount"] >= 4, "%d 行" % d["lineCount"])
            check("制表符也还原了", d["hasTab"] is True)
            check("换行能显示出来（pre-wrap）", d["preWrap"] == "pre-wrap", d["preWrap"])
        except Exception as e:
            check("newline unescape parseable", False, str(e))
        evaluate(cdp, "document.getElementById('popupClose').click(); 1")

        print("\n=== 14. 常驻控件：左上角时间显示（不是按钮）===")
        s = evaluate(cdp, """
        (function(){
          state.actions = [];
          state.panels = [];
          renderAll();

          // 复现用户那句话：让它在左上角加个时间显示
          var note = runTool('create_panel', { title:'时间', kind:'clock', place:'left' });
          renderAll();

          var w = document.querySelector('#railWidgets .widget');
          var big = document.querySelector('#railWidgets .w-clock');
          var sub = document.querySelector('#railWidgets .w-date');
          return JSON.stringify({
            note: note,
            railHidden: document.getElementById('rail').hidden,
            widgetCount: document.querySelectorAll('#railWidgets .widget').length,
            showedAsButton: document.querySelectorAll('#railBtns .act').length,
            bigText: big ? big.textContent : null,
            subText: sub ? sub.textContent : null,
            kindStored: state.panels[0].kind,
            placeStored: state.panels[0].place,
            isLiveClass: !!big
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("装的是控件不是按钮", d["showedAsButton"] == 0, "%d 个按钮" % d["showedAsButton"])
            check("控件出现在左侧栏", d["widgetCount"] == 1 and d["railHidden"] is False)
            check("kind=clock 被记下来了", d["kindStored"] == "clock", str(d["kindStored"]))
            check("place=left 被记下来了", d["placeStored"] == "left", str(d["placeStored"]))
            import re as _re
            check("显示的是真实时间 HH:MM:SS",
                  bool(_re.match(r"^\d{2}:\d{2}:\d{2}$", d["bigText"] or "")), d["bigText"])
            check("下面还有日期星期", "星期" in (d["subText"] or ""), d["subText"])
            check("控件挂了实时更新的类", d["isLiveClass"] is True)
        except Exception as e:
            check("widget parseable", False, str(e))

        print("\n=== 14b. 时钟会自己走（不是死的 HTML）===")
        t1 = evaluate(cdp, "(document.querySelector('#railWidgets .w-clock')||{}).textContent")
        time.sleep(2.2)
        s = evaluate(cdp, """
        (function(){
          return JSON.stringify({
            t2: (document.querySelector('#railWidgets .w-clock')||{}).textContent,
            timerRunning: !!window.__widgetTimerRunning || true
          });
        })()
        """)
        t2 = json.loads(s)["t2"]
        print("    %s -> %s" % (t1, t2))
        check("**两秒后时间变了（真的在走）**", t1 != t2, "%s -> %s" % (t1, t2))

        print("\n=== 14c. 日期控件 & 顶部/右侧仍可用 ===")
        s = evaluate(cdp, """
        (function(){
          runTool('create_panel', { title:'今天', kind:'date', place:'left' });
          runTool('create_panel', { title:'清单', html:'<ul><li>甲</li></ul>', place:'right' });
          runTool('create_panel', { title:'顶钟', kind:'clock', place:'top' });
          state.panelsOpen = true;   // 侧栏现在是「预览」才开
          renderAll();
          return JSON.stringify({
            leftWidgets: document.querySelectorAll('#railWidgets .widget').length,
            dateOnly: (document.querySelector('.w-dateonly')||{}).textContent,
            rightPanels: document.querySelectorAll('#panels .panel').length,
            rightHidden: document.getElementById('sidebar').hidden,
            rightText: (document.querySelector('#panels .p-body')||{}).textContent,
            topWidgets: document.querySelectorAll('#topWidgets .widget').length,
            topText: (document.querySelector('#topWidgets .w-clock')||{}).textContent,
            topInPanel: document.querySelectorAll('#panels .panel .w-clock').length
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("左侧两个控件", d["leftWidgets"] == 2, "%d" % d["leftWidgets"])
            check("日期控件有内容", "年" in (d["dateOnly"] or ""), d["dateOnly"])
            check("右侧面板没受影响", d["rightPanels"] == 1 and d["rightHidden"] is False)
            check("右侧面板内容正确", "甲" in (d["rightText"] or ""), d["rightText"])
            # 用户说的"让他加个时钟在最顶部他说改不了" —— 现在 place:"top" 真的落顶栏
            check("**place=top 的时钟落在顶栏里**", d["topWidgets"] == 1, "%d" % d["topWidgets"])
            check("顶栏时钟有时间", ":" in (d["topText"] or ""), d["topText"])
            check("顶部控件没有混进右侧栏", d["topInPanel"] == 0, "%d" % d["topInPanel"])
        except Exception as e:
            check("widget kinds parseable", False, str(e))

        print("\n=== 15. 首屏大字：逐字显示且始终居中 ===")
        evaluate(cdp, """
        (function(){
          state.messages = [];
          renderAll();
          if (typeof startHeroTyping === 'function') startHeroTyping();
          return 1;
        })()
        """)
        time.sleep(0.35)
        s1 = evaluate(cdp, """
        (function(){
          var el = document.querySelector('#hero .line');
          var r = el.getBoundingClientRect();
          var chat = document.getElementById('chat').getBoundingClientRect();
          return JSON.stringify({
            partial: el.textContent,
            hasCaret: !!el.querySelector('.caret'),
            centerOffset: Math.abs((r.left + r.width/2) - (chat.left + chat.width/2))
          });
        })()
        """)
        time.sleep(1.6)
        s2 = evaluate(cdp, """
        (function(){
          var el = document.querySelector('#hero .line');
          var r = el.getBoundingClientRect();
          var chat = document.getElementById('chat').getBoundingClientRect();
          return JSON.stringify({
            full: el.textContent,
            hasCaret: !!el.querySelector('.caret'),
            centerOffset: Math.abs((r.left + r.width/2) - (chat.left + chat.width/2))
          });
        })()
        """)
        print("    中途:", s1)
        print("    打完:", s2)
        try:
            d1 = json.loads(s1); d2 = json.loads(s2)
            check("一开始字还没打完（流式效果）",
                  len(d1["partial"]) < len(d2["full"]),
                  "%d -> %d 字" % (len(d1["partial"]), len(d2["full"])))
            check("打字过程中有光标", d1["hasCaret"] is True)
            check("最终打完整句", d2["full"] == "Welcome to NovaDesk", d2["full"])
            check("**文字始终水平居中**",
                  d1["centerOffset"] < 3 and d2["centerOffset"] < 3,
                  "偏移 %s / %s" % (d1["centerOffset"], d2["centerOffset"]))
        except Exception as e:
            check("hero typing parseable", False, str(e))

        print("\n=== 16. 竖屏布局 ===")
        evaluate(cdp, "document.getElementById('hero').style.display=''; 1")
        # 用设备指标模拟竖屏
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True
        })
        time.sleep(0.6)
        s = evaluate(cdp, """
        (function(){
          var rail = document.getElementById('rail');
          var body = document.querySelector('main.body');
          var rowDir = getComputedStyle(body).flexDirection;
          var railDir = rail ? getComputedStyle(rail).flexDirection : null;
          var w = window.innerWidth, h = window.innerHeight;
          // 放一个控件进去看它是不是横排
          state.panels = []; state.actions = [];
          runTool('create_panel', { title:'时间', kind:'clock', place:'left' });
          runTool('add_action', { label:'测试', prompt:'show-time', behavior:'run', position:'left' });
          renderAll();
          var railRect = rail.getBoundingClientRect();
          var widgetRect = document.querySelector('#railWidgets .widget').getBoundingClientRect();
          return JSON.stringify({
            win: w + 'x' + h,
            portrait: h > w,
            bodyDir: rowDir,
            railDir: railDir,
            railFullWidth: railRect.width > w * 0.8,
            railShort: railRect.height < 260,
            widgetToRightOfRailStart: widgetRect.left >= railRect.left - 1
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("模拟的是竖屏", d["portrait"] is True, d["win"])
            check("主体改成纵向排列", d["bodyDir"] == "column", d["bodyDir"])
            check("左栏改成横向", d["railDir"] == "row", d["railDir"])
            check("左栏占满宽度（变成顶部横条）", d["railFullWidth"] is True)
            check("横条不高（不挤压对话区）", d["railShort"] is True)
        except Exception as e:
            check("portrait probe parseable", False, str(e))
        cdp.call("Emulation.clearDeviceMetricsOverride")
        time.sleep(0.4)
        evaluate(cdp, "state.panels=[]; state.actions=[]; renderAll(); 1")

        print("\n=== 17. M3 设计令牌是否真的生效 ===")
        evaluate(cdp, """
        (function(){
          state.messages = [{role:'user', content:'测试'}];
          state.actions = []; state.panels = [];
          renderAll();
          // 输入框要有内容，发送按钮才是"可用"态（否则取到的是禁用色）
          var i = document.getElementById('input');
          i.value = 'x';
          i.dispatchEvent(new Event('input', { bubbles: true }));
          return 1;
        })()
        """)
        time.sleep(0.3)
        s = evaluate(cdp, """
        (function(){
          function rgb(el, prop){ return getComputedStyle(el)[prop]; }
          var root = getComputedStyle(document.documentElement);
          function tok(n){ return root.getPropertyValue(n).trim().toUpperCase(); }

          var send = document.getElementById('btnSend');
          var input = document.getElementById('input');
          var composer = document.querySelector('.composer');
          var topbar = document.querySelector('.topbar');
          var title = document.getElementById('appTitle');
          var bubble = document.querySelector('.msg.user .bubble');

          return JSON.stringify({
            /* 令牌本身 */
            primary: tok('--md-primary'),
            onPrimary: tok('--md-on-primary'),
            primaryContainer: tok('--md-primary-container'),
            surface: tok('--md-surface'),
            outlineVariant: tok('--md-outline-variant'),
            /* 组件上真实算出来的样式 */
            sendBg: rgb(send, 'backgroundColor'),
            sendSize: send.getBoundingClientRect().width + 'x' + send.getBoundingClientRect().height,
            composerRadius: rgb(composer, 'borderTopLeftRadius'),
            composerBg: rgb(composer, 'backgroundColor'),
            topbarH: topbar.getBoundingClientRect().height,
            titleSize: rgb(title, 'fontSize'),
            titleWeight: rgb(title, 'fontWeight'),
            bodySize: rgb(document.body, 'fontSize'),
            bubbleBg: rgb(bubble, 'backgroundColor'),
            bubbleRadius: rgb(bubble, 'borderTopLeftRadius'),
            thinkBtnW: document.getElementById('btnThink').getBoundingClientRect().width,
            thinkBtnH: document.getElementById('btnThink').getBoundingClientRect().height,
            thinkOnBg: (function(){
              var b = document.getElementById('btnThink');
              var was = b.className, wasT = b.style.transition;
              /* 关掉过渡，否则同步读到的还是过渡起点（透明） */
              b.style.transition = 'none';
              b.className = 'icon-btn on';
              var c = rgb(b, 'backgroundColor');
              b.className = was; b.style.transition = wasT;
              return c;
            })()
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            # 官方 M3 基线色
            check("primary = #6750A4（M3 基线主色）", d["primary"] == "#6750A4", d["primary"])
            check("on-primary = #FFFFFF", d["onPrimary"] == "#FFFFFF", d["onPrimary"])
            check("primary-container = #EADDFF",
                  d["primaryContainer"] == "#EADDFF", d["primaryContainer"])
            check("surface = #FEF7FF", d["surface"] == "#FEF7FF", d["surface"])
            check("outline-variant = #CAC4D0",
                  d["outlineVariant"] == "#CAC4D0", d["outlineVariant"])

            # 组件实际渲染
            check("发送按钮用 primary 填充",
                  d["sendBg"] == "rgb(103, 80, 164)", d["sendBg"])
            check("发送按钮 48dp（M3 FAB small 尺寸）",
                  d["sendSize"] == "48x48", d["sendSize"])
            check("输入框 28dp 圆角（M3 search bar 形态）",
                  d["composerRadius"] == "28px", d["composerRadius"])
            check("输入框底色是 surface-container-high",
                  d["composerBg"] == "rgb(236, 230, 240)", d["composerBg"])
            check("顶栏 64dp（M3 small top app bar）",
                  abs(d["topbarH"] - 64) < 1, "%s px" % d["topbarH"])
            check("标题用 title-large 22sp/400",
                  d["titleSize"] == "22px" and d["titleWeight"] == "400",
                  "%s / %s" % (d["titleSize"], d["titleWeight"]))
            check("正文用 body-large 16sp",
                  d["bodySize"] == "16px", d["bodySize"])
            check("用户气泡用 primary-container",
                  d["bubbleBg"] == "rgb(234, 221, 255)", d["bubbleBg"])
            check("气泡 16dp 圆角（M3 large）",
                  d["bubbleRadius"] == "16px", d["bubbleRadius"])
            check("顶栏按钮是 M3 icon button（48dp 触摸目标）",
                  abs(d["thinkBtnH"] - 48) < 1 and abs(d["thinkBtnW"] - 48) < 1,
                  "%sx%s px" % (d["thinkBtnW"], d["thinkBtnH"]))
            check("思考开着时 icon button 用 secondary-container 高亮",
                  d["thinkOnBg"] == "rgb(232, 222, 248)", d["thinkOnBg"])
        except Exception as e:
            check("M3 token probe parseable", False, str(e))

        print("\n=== 17b. 触摸目标够大（手机要求 ≥48dp）===")
        s = evaluate(cdp, """
        (function(){
          function h(sel){ var e = document.querySelector(sel); return e ? e.getBoundingClientRect().height : null; }
          function w(sel){ var e = document.querySelector(sel); return e ? e.getBoundingClientRect().width : null; }
          var send = document.getElementById('btnSend').getBoundingClientRect();
          var newBtn = document.getElementById('btnNew').getBoundingClientRect();
          return JSON.stringify({
            send: Math.round(send.width) + 'x' + Math.round(send.height),
            newBtn: Math.round(newBtn.width) + 'x' + Math.round(newBtn.height),
            input: Math.round(document.querySelector('.composer').getBoundingClientRect().height)
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            sw, sh = [int(x) for x in d["send"].split("x")]
            check("发送按钮 ≥44px", sh >= 44 and sw >= 44, d["send"])
            check("输入区高度 ≥48px", d["input"] >= 48, "%s px" % d["input"])
        except Exception as e:
            check("touch target parseable", False, str(e))

        print("\n=== 18. 安全：Markdown 转义与链接白名单 ===")
        s = evaluate(cdp, """
        (function(){
          window.__xss = 0;
          var out = {};

          // a) 经典属性逃逸：URL 里塞引号
          var evil = '[点我](https://a.com/"onmouseover="window.__xss=1)';
          var html1 = renderMarkdown(evil);
          var d1 = document.createElement('div');
          d1.innerHTML = html1;

          // b) 直接的 script 标签
          var html2 = renderMarkdown('<script>window.__xss=2<\\/script>');
          var d2 = document.createElement('div');
          d2.innerHTML = html2;

          // c) img onerror
          var html3 = renderMarkdown('<img src=x onerror="window.__xss=3">');
          var d3 = document.createElement('div');
          d3.innerHTML = html3;

          // d) javascript: 链接
          var html4 = renderMarkdown('[x](javascript:window.__xss=4)');
          var d4 = document.createElement('div');
          d4.innerHTML = html4;

          // e) 正常链接要保留
          var html5 = renderMarkdown('看 [文档](https://example.com/a?b=1)');
          var d5 = document.createElement('div');
          d5.innerHTML = html5;

          return JSON.stringify({
            aHtml: html1.slice(0, 110),
            aHasOnMouseOver: d1.querySelector('[onmouseover]') !== null,
            aLinkCount: d1.querySelectorAll('a').length,
            bTagCount: d2.querySelectorAll('script').length,
            cTagCount: d3.querySelectorAll('img').length,
            cAttr: d3.querySelector('[onerror]') !== null,
            dHref: d4.querySelector('a') ? d4.querySelector('a').getAttribute('href') : null,
            eHref: d5.querySelector('a') ? d5.querySelector('a').getAttribute('href') : null,
            eText: d5.textContent,
            xss: window.__xss
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("带引号的 URL 冲不出 href 属性", d["aHasOnMouseOver"] is False, d["aHtml"])
            check("script 标签被转义成文字", d["bTagCount"] == 0)
            check("img 标签被转义成文字", d["cTagCount"] == 0 and d["cAttr"] is False)
            check("javascript: 不生成链接", d["dHref"] is None, str(d["dHref"]))
            check("正常 https 链接保留并可点",
                  d["eHref"] == "https://example.com/a?b=1", str(d["eHref"]))
            check("链接文字正常", d["eText"] == "看 文档", repr(d["eText"]))
            check("**没有任何脚本被触发**", d["xss"] == 0, "xss=%s" % d["xss"])
        except Exception as e:
            check("xss probe parseable", False, str(e))

        print("\n=== 18b. 容量上限 ===")
        s = evaluate(cdp, """
        (function(){
          var out = {};
          // 灌 600 条消息
          state.messages = [];
          for (var i = 0; i < 600; i++) state.messages.push({role:'user', content:'第' + i});
          saveState();
          out.after600 = state.messages.length;
          // 存进 localStorage 的也是裁过的。
          // 注意：现在存在"会话列表"里，当前这条对话才是刚灌的那批消息。
          var raw = JSON.parse(localStorage.getItem('novadesk.sessions.v1'));
          var cur = (raw.sessions || []).filter(function(x){ return x.id === raw.currentId; })[0];
          out.stored = cur ? cur.messages.length : -1;
          out.cap = MAX_MESSAGES;
          // 超大控件会被截断
          state.panels = [];
          var big = new Array(MAX_PANEL_HTML + 50000).join('x');
          runTool('create_panel', { title:'超大', html: big });
          out.panelLen = state.panels[0].html.length;
          out.panelCap = MAX_PANEL_HTML;
          out.panelTruncated = state.panels[0].html.indexOf('已截断') >= 0;
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("历史被裁到上限", d["after600"] == d["cap"], "%d" % d["after600"])
            check("存进存储的也是裁过的", d["stored"] == d["cap"], "%d" % d["stored"])
            check("超大控件被截断", d["panelTruncated"] is True and d["panelLen"] < d["panelCap"] + 200,
                  "%d / cap %d" % (d["panelLen"], d["panelCap"]))
        except Exception as e:
            check("cap probe parseable", False, str(e))
        # 别用 localStorage.clear()：那会把设置和会话一起清掉，影响后面的用例
        evaluate(cdp, "state.messages=[]; state.panels=[]; state.actions=[]; "
                      "saveState(); renderAll(); 1")

        print("\n=== 18c. 网址协议白名单 ===")
        s = evaluate(cdp, """
        (function(){
          window.__netCalls = [];
          window.NovaNative = { get: function(id, url){
            window.__netCalls.push(url);
            setTimeout(function(){
              window.__novaNetDone(id, { ok:true, status:200, body:'<p>ok</p>', error:'' });
            }, 5);
          }};
          var out = {};
          return (async function(){
            out.fileUrl = await webOpen('file:///etc/passwd');
            out.jsUrl = await webOpen('javascript:alert(1)');
            out.dataUrl = await webOpen('data:text/html,<b>x</b>');
            var before = window.__netCalls.length;
            out.normal = await webOpen('example.com');
            out.netCalledForNormal = window.__netCalls.length > before;
            out.calls = window.__netCalls.slice();
            return JSON.stringify(out);
          })();
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("file:// 被拒绝", "拒绝" in d["fileUrl"], d["fileUrl"][:60])
            check("javascript: 被拒绝", "拒绝" in d["jsUrl"], d["jsUrl"][:60])
            check("data: 被拒绝", "拒绝" in d["dataUrl"], d["dataUrl"][:60])
            check("**危险的协议根本没走到原生桥**",
                  all("file:" not in c and "javascript:" not in c and "data:" not in c
                      for c in d["calls"]), str(d["calls"]))
            check("正常网址照常走", d["netCalledForNormal"] is True)
        except Exception as e:
            check("scheme probe parseable", False, str(e))

        print("\n=== 19. 控件重绘不会重复执行脚本 ===")
        s = evaluate(cdp, """
        (function(){
          state.panels = []; state.actions = [];
          renderRail(true); renderPanels(true);
          window.__runs = 0;
          state.allowScript = true;
          runTool('create_panel', {
            title: '运行次数', place: 'left',
            html: '<div id="rc">0</div><script>window.__runs++;' +
                  'var e=document.getElementById("rc"); if(e) e.textContent=window.__runs;' +
                  '<\\/script>'
          });
          renderRail(true);
          var afterFirst = window.__runs;
          // 再重绘 10 次（模拟后续每轮对话 / 保存设置）
          for (var i = 0; i < 10; i++) renderRail();
          var afterTen = window.__runs;
          var shown = (document.getElementById('rc') || {}).textContent;
          return JSON.stringify({
            afterFirst: afterFirst,
            afterTen: afterTen,
            shown: shown,
            sigUsed: typeof railSig === 'string'
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("首次渲染脚本跑了一次", d["afterFirst"] == 1, "%d" % d["afterFirst"])
            check("**重复重绘 10 次，脚本没有再跑**", d["afterTen"] == 1,
                  "%d -> %d" % (d["afterFirst"], d["afterTen"]))
            check("指纹缓存生效", d["sigUsed"] is True)
        except Exception as e:
            check("rerender probe parseable", False, str(e))
        evaluate(cdp, "state.panels=[]; state.actions=[]; renderRail(true); renderPanels(true); 1")

        print("\n=== 20. 长按弹底部菜单（替代那个叉号）===")
        s = evaluate(cdp, """
        (function(){
          state.panels = []; state.actions = [];
          state.allowScript = true;
          runTool('create_panel', { title: '待删控件', place: 'left', html: '<b>hi</b>' });
          renderRail(true);
          var node = document.querySelector('#railWidgets .widget');
          if (!node) return JSON.stringify({ noNode: true });
          var hasX = !!node.querySelector('.w-x, .x, [data-remove]');
          function press(el, ms){
            var r = el.getBoundingClientRect();
            var x = r.left + r.width/2, y = r.top + r.height/2;
            function ev(type){
              el.dispatchEvent(new PointerEvent(type, {
                bubbles: true, cancelable: true, pointerId: 1,
                clientX: x, clientY: y, isPrimary: true, pointerType: 'touch'
              }));
            }
            ev('pointerdown');
            return new Promise(function(res){ setTimeout(function(){ ev('pointerup'); res(); }, ms); });
          }
          return press(node, 700).then(function(){
            var sheet = document.getElementById('sheet');
            var mask = document.getElementById('sheetMask');
            var open = !!(sheet && !sheet.hidden);
            var items = sheet ? sheet.querySelectorAll('.s-item').length : 0;
            var text = sheet ? sheet.textContent : '';
            /* 遮罩状态要在 closeSheet() 之前读，否则永远是 hidden */
            var masked = !!(mask && !mask.hidden);
            closeSheet();
            return new Promise(function(res){
              setTimeout(function(){
                res(JSON.stringify({
                  hasX: hasX, open: open, items: items, masked: masked,
                  text: text, closed: !!sheet.hidden,
                  title: document.getElementById('sheetTitle').textContent
                }));
              }, 260);
            });
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("控件上不再有叉号", d.get("hasX") is False)
            check("长按 700ms 弹出底部菜单", d.get("open") is True)
            check("菜单背后有遮罩", d.get("masked") is True)
            check("菜单标题是控件名", d.get("title") == "待删控件", str(d.get("title")))
            check("菜单里有操作项", d.get("items", 0) >= 2, "%s 项" % d.get("items"))
            check("菜单里能删掉它", "删" in d.get("text", ""), d.get("text", "")[:40])
            check("关掉后菜单隐藏", d.get("closed") is True)
        except Exception as e:
            check("sheet probe parseable", False, str(e))

        print("\n=== 20b. 短按不会误弹菜单 ===")
        s = evaluate(cdp, """
        (function(){
          var node = document.querySelector('#railWidgets .widget');
          if (!node) return JSON.stringify({ noNode: true });
          var r = node.getBoundingClientRect();
          var x = r.left + r.width/2, y = r.top + r.height/2;
          function ev(type){
            node.dispatchEvent(new PointerEvent(type, {
              bubbles: true, cancelable: true, pointerId: 1,
              clientX: x, clientY: y, isPrimary: true, pointerType: 'touch'
            }));
          }
          ev('pointerdown');
          return new Promise(function(res){
            setTimeout(function(){
              ev('pointerup');
              var sheet = document.getElementById('sheet');
              res(JSON.stringify({ open: !!(sheet && !sheet.hidden) }));
            }, 120);
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("轻点一下不会弹菜单", d.get("open") is False)
        except Exception as e:
            check("shortpress probe parseable", False, str(e))

        print("\n=== 20c. 右键也能弹出（桌面端）===")
        s = evaluate(cdp, """
        (function(){
          state.panels = []; renderRail(true);
          var node = document.querySelector('#railWidgets .widget');
          if (!node) return JSON.stringify({ noNode: true });
          node.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
          var sheet = document.getElementById('sheet');
          var open = !!(sheet && !sheet.hidden);
          closeSheet();
          return JSON.stringify({ open: open });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("右键弹出菜单", d.get("open") is True)
        except Exception as e:
            check("contextmenu probe parseable", False, str(e))
        evaluate(cdp, "state.panels=[]; state.actions=[]; renderRail(true); renderPanels(true); 1")

        print("\n=== 21. 设备能力：有原生桥就真调，没有就如实说 ===")
        s = evaluate(cdp, """
        (function(){
          var calls = [];
          var saved = window.NovaNative;
          // 1) 假装在 APK 里
          window.NovaNative = {
            setOrientation: function(v){ calls.push('setOrientation:' + v); },
            setKeepAwake:   function(v){ calls.push('setKeepAwake:' + v); },
            setFullscreen:  function(v){ calls.push('setFullscreen:' + v); }
          };
          var a = runLocalAction('set-orientation:landscape');
          var b = runLocalAction('keep-awake:on');
          var c = runLocalAction('fullscreen:on');
          var bad = runLocalAction('set-orientation:sideways');
          // 2) 网页版：没有桥
          delete window.NovaNative;
          var d1 = runLocalAction('set-orientation:portrait');
          var d2 = runLocalAction('keep-awake:on');
          var d3 = runLocalAction('fullscreen:on');
          window.NovaNative = saved;
          return JSON.stringify({ a:a, b:b, c:c, bad:bad, d1:d1, d2:d2, d3:d3, calls:calls });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("横屏：真的调了原生 setOrientation",
                  "setOrientation:landscape" in d["calls"], str(d["calls"]))
            check("常亮：真的调了原生 setKeepAwake",
                  "setKeepAwake:on" in d["calls"], str(d["calls"]))
            check("全屏：真的调了原生 setFullscreen",
                  "setFullscreen:on" in d["calls"], str(d["calls"]))
            check("非法方向被挡住且不碰原生", "portrait / landscape / auto" in d["bad"], d["bad"][:50])
            check("**没有原生桥时明说做不到，不假装成功**",
                  "改不了" in d["d1"] and "做不到" not in d["d1"], d["d1"][:50])
            check("常亮没桥也如实说", "控制不了" in d["d2"], d["d2"][:50])
            check("全屏没桥也如实说", "控制不了" in d["d3"], d["d3"][:50])
        except Exception as e:
            check("device probe parseable", False, str(e))

        print("\n=== 21c. AI 有「直接改」的工具（不是加按钮）===")
        s = evaluate(cdp, """
        (function(){
          var calls = [];
          var saved = window.NovaNative;
          window.NovaNative = {
            setOrientation: function(v){ calls.push('o:' + v); },
            setKeepAwake:   function(v){ calls.push('k:' + v); },
            setFullscreen:  function(v){ calls.push('f:' + v); }
          };
          var r1 = runTool('device', { action: 'orientation', value: 'landscape' });
          var r2 = runTool('device', { action: 'keep_awake', value: 'on' });
          var r3 = runTool('device', { action: 'fullscreen', value: 'on' });
          var bad1 = runTool('device', { action: 'orientation', value: 'sideways' });
          var bad2 = runTool('device', { action: 'nuke', value: 'on' });
          delete window.NovaNative;
          var web = runTool('device', { action: 'orientation', value: 'portrait' });
          window.NovaNative = saved;
          // 工具名字必须在工具表里，模型才看得见
          var names = TOOLS.map(function(t){ return t.function.name; });
          return JSON.stringify({
            r1:r1, r2:r2, r3:r3, bad1:bad1, bad2:bad2, web:web,
            calls:calls, inTools: names.indexOf('device') >= 0, names:names
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("device 工具存在（模型能看见）", d["inTools"] is True)
            check("device 真的转了横屏", "o:landscape" in d["calls"], str(d["calls"]))
            check("device 真的设了常亮", "k:on" in d["calls"], str(d["calls"]))
            check("device 真的全屏了", "f:on" in d["calls"], str(d["calls"]))
            check("非法方向被挡住，且不碰原生", "没改任何东西" in d["bad1"], d["bad1"][:40])
            check("非法 action 被挡住", "没改任何东西" in d["bad2"], d["bad2"][:40])
            check("**网页版如实返回做不到**", "装 APK" in d["web"], d["web"][:50])
        except Exception as e:
            check("device-tool probe parseable", False, str(e))

        print("\n=== 21b. AI 能用 add_action 做出这些按钮 ===")
        s = evaluate(cdp, """
        (function(){
          state.actions = []; renderActions(); renderRail(true);
          var r = runTool('add_action', {
            label: '横屏', prompt: 'set-orientation:landscape',
            position: 'top', behavior: 'run'
          });
          renderActions(); renderRail(true);
          var btn = document.querySelector('#actions .act');
          var calls = [];
          var saved = window.NovaNative;
          window.NovaNative = { setOrientation: function(v){ calls.push(v); } };
          if (btn) btn.click();
          window.NovaNative = saved;
          return JSON.stringify({ r: r, hasBtn: !!btn, calls: calls });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("add_action 建出了横屏按钮", d["hasBtn"] is True)
            check("点它就真的调原生转横屏", d["calls"] == ["landscape"], str(d["calls"]))
        except Exception as e:
            check("action-device probe parseable", False, str(e))
        evaluate(cdp, "state.actions=[]; state.panels=[]; renderRail(true); renderPanels(true); 1")

        print("\n=== 22. 扁平 M3：卡片/面板/控件不许有阴影 ===")
        s = evaluate(cdp, """
        (function(){
          function shadow(sel){
            var e = document.querySelector(sel);
            if (!e) return null;
            return getComputedStyle(e).boxShadow;
          }
          var flat = ['.msg .bubble', '.panel', '.widget', '.rail', '.sidebar', '.topbar', '.composer', '.act'];
          var out = {};
          flat.forEach(function(s){
            var v = shadow(s);
            if (v !== null) out[s] = v;
          });
          // 只有这几个允许有阴影（M3 里就是 FAB / 对话框 / 提示条）
          out['__send'] = shadow('#btnSend');
          out['__popup'] = shadow('.popup');
          out['__toast'] = shadow('.toast');
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            flat_ok = True
            bad = []
            for k, v in d.items():
                if k.startswith("__"):
                    continue
                if v and v != "none":
                    flat_ok = False
                    bad.append("%s=%s" % (k, v))
            check("**卡片/面板/控件一律没有阴影（不拟物）**", flat_ok, "; ".join(bad))
            check("只保留 M3 允许的 FAB 阴影（发送键）",
                  d.get("__send") not in (None, "none"), str(d.get("__send")))
        except Exception as e:
            check("flat probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 23. 布局：内容再多也不许把输入框顶出屏幕 ===")
        # 守的是一个真踩过的坑：.chat 少写了 min-height:0，内容一旦超过一屏，
        # 它就不肯收缩，带着整个文档一起长高，输入框被顶到视口外面，
        # 连"停止"按钮都跟着出屏幕、手指够不着。短对话完全正常，只在长对话暴露。
        s = evaluate(cdp, """
        (function(){
          function r(sel){ var e=document.querySelector(sel); return e?e.getBoundingClientRect():null; }
          var think = '';
          for (var i=0;i<120;i++) think += '思考第'+i+'行，要仔细想清楚再做。\\n';
          state.messages = [];
          for (var k=0;k<8;k++){
            state.messages.push({role:'user', content:'第'+k+'个问题'});
            state.messages.push({role:'assistant', content:'第'+k+'个回答。', reasoning_content: think});
          }
          state.panels = []; state.actions = [];
          renderAll();
          var cw = r('.composer-wrap');
          var sc = r('.scroll');
          var sEl = document.getElementById('btnSend');
          var sr = sEl.getBoundingClientRect();
          var hit = document.elementFromPoint(sr.left + sr.width/2, sr.top + sr.height/2);
          return JSON.stringify({
            innerH: window.innerHeight,
            composerBottom: Math.round(cw.bottom),
            docScrollH: document.documentElement.scrollHeight,
            composerVisible: cw.bottom <= window.innerHeight + 1 && cw.top >= 0,
            pageNotGrown: document.documentElement.scrollHeight <= window.innerHeight + 2,
            scrollScrollable: document.querySelector('.scroll').scrollHeight > sc.height + 10,
            sendInView: sr.bottom <= window.innerHeight + 1,
            sendHittable: !!(hit && (hit === sEl || sEl.contains(hit)))
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("输入框仍在视口内", d["composerVisible"] is True,
                  "底边 %d / 视口 %d" % (d["composerBottom"], d["innerH"]))
            check("**页面本身没被撑高（内容该在框里滚）**", d["pageNotGrown"] is True,
                  "文档高 %d / 视口 %d" % (d["docScrollH"], d["innerH"]))
            check("内容区确实可以滚动", d["scrollScrollable"] is True)
            check("发送/停止按钮在视口内", d["sendInView"] is True)
            check("发送/停止按钮点得到", d["sendHittable"] is True)
        except Exception as e:
            check("layout probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 23b. 生成中「停止」按钮必须可点 ===")
        s = evaluate(cdp, """
        (function(){
          var send = document.getElementById('btnSend');
          sending = true; abortCtl = new AbortController();
          setBusy(true); updateFoot();
          var r = send.getBoundingClientRect();
          var hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
          var out = {
            disabled: send.disabled,
            stopClass: send.classList.contains('stop'),
            hittable: !!(hit && (hit === send || send.contains(hit))),
            title: send.title
          };
          sending = false; abortCtl = null; setBusy(false); updateFoot();
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("生成中按钮变成停止样式", d["stopClass"] is True)
            check("**生成中按钮没有被禁用**", d["disabled"] is False)
            check("**生成中按钮点得到（没被别的东西盖住）**", d["hittable"] is True)
            check("按钮提示是停止生成", u"停止" in d["title"], d["title"])
        except Exception as e:
            check("stop probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 24. 文件权限与导出 APK：没有原生桥就得如实说做不到 ===")
        s = evaluate(cdp, """
        (async function(){
          var names = TOOLS.map(function(t){ return t.function.name; });
          return JSON.stringify({
            hasFileBridge: hasFileBridge(),
            hasApkBridge: hasApkBridge(),
            hasExeBridge: hasExeBridge(),
            sysbarChips: document.querySelectorAll('#sysbar .chip').length,
            hasFileTool: names.indexOf('file') >= 0,
            hasExportTool: names.indexOf('export_apk') >= 0,
            hasExeTool: names.indexOf('export_exe') >= 0,
            hasAdbTool: names.indexOf('adb') >= 0,
            hasShizuku: hasShizuku(),
            fileToolCount: names.filter(function(n){ return n === 'file'; }).length,
            fileNoBridge: await execTool('file', {action:'read', path:'x.txt'}),
            exeNoBridge: await execTool('export_exe', {name:'测试'})
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("网页版没有文件桥", d["hasFileBridge"] is False)
            check("网页版没有导出 APK 的桥", d["hasApkBridge"] is False)
            check("网页版没有导出 exe 的桥", d["hasExeBridge"] is False)
            check("**没有原生桥时不显示假的能力按钮**", d["sysbarChips"] == 0,
                  "%d 个" % d["sysbarChips"])
            check("file 工具存在（模型能看见）", d["hasFileTool"] is True)
            check("export_apk 工具存在（模型能看见）", d["hasExportTool"] is True)
            check("export_exe 工具存在（模型能看见）", d["hasExeTool"] is True)
            check("adb 工具存在（模型能看见）", d["hasAdbTool"] is True)
            check("网页版没有 Shizuku 桥", d["hasShizuku"] is False)
            check("file 工具没有重复定义", d["fileToolCount"] == 1)
            check("**没桥时 file 如实说做不到，不假装成功**",
                  u"做不到" in d["fileNoBridge"], d["fileNoBridge"])
            check("**没桥时 export_exe 如实说做不到**",
                  u"做不到" in d["exeNoBridge"], d["exeNoBridge"])
        except Exception as e:
            check("file/apk probe parseable", False, str(e))

        # export_apk 的实现在 ui 层而且是异步的，单独探一次
        s = evaluate(cdp, """
        (async function(){
          var r = await exportApkTool({name: '测试应用'});
          return JSON.stringify({ msg: r });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("**网页版导出 APK 如实说做不到**",
                  u"做不到" in d["msg"] and u"网页版" in d["msg"], d["msg"])
        except Exception as e:
            check("export probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 25. 导出的独立单页：自包含、脚本标签配对 ===")
        s = evaluate(cdp, """
        (function(){
          var panels = [
            { id:'p1', title:'计数器', kind:'html', place:'right',
              html:'<button id="b">+1</button><script>window.__ran=1;<\\/script>' },
            { id:'p2', title:'时钟', kind:'clock', place:'left', html:'' }
          ];
          var html = buildStandaloneHtml('我的应用', panels);
          var open = (html.match(/<script>/g) || []).length;
          var close = (html.match(/<\\/script>/g) || []).length;
          return JSON.stringify({
            startsWithDoctype: html.indexOf('<!doctype html>') === 0,
            hasTitle: html.indexOf('<title>我的应用</title>') >= 0,
            hasPanelHtml: html.indexOf('id="b"') >= 0,
            hasShim: html.indexOf('window.Nova=') >= 0,
            hasClockTick: html.indexOf('setInterval') >= 0,
            scriptOpen: open,
            scriptClose: close,
            selfContained: html.indexOf('http://') < 0 && html.indexOf('https://') < 0,
            len: html.length
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("导出页是完整 HTML 文档", d["startsWithDoctype"] is True)
            check("标题用的是应用名", d["hasTitle"] is True)
            check("控件 HTML 被原样带进去", d["hasPanelHtml"] is True)
            check("带了 Nova 替身（控件脚本才不会报错）", d["hasShim"] is True)
            check("时钟类控件自带定时器（导出后没人驱动它）", d["hasClockTick"] is True)
            check("**导出页自包含，不引任何外部资源**", d["selfContained"] is True)
            check("**script 标签开闭配对**", d["scriptOpen"] == d["scriptClose"],
                  "开 %d / 闭 %d" % (d["scriptOpen"], d["scriptClose"]))
            check("script 标签至少 2 个（替身 + 时钟）", d["scriptOpen"] >= 2,
                  str(d["scriptOpen"]))
        except Exception as e:
            check("standalone probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 26. 上传文件给 AI 看 ===")
        s = evaluate(cdp, """
        (async function(){
          var out = {};
          var inp = document.getElementById('fileInput');
          out.hasInput = !!inp;
          out.hasBtn = !!document.getElementById('btnAttach');
          out.hasBar = !!document.getElementById('files');
          if (!inp) return JSON.stringify(out);

          function waitFiles(n){
            return new Promise(function(res){
              var tries = 0;
              (function step(){
                if (state.pendingFiles.length === n || tries++ > 80) return res();
                setTimeout(step, 25);
              })();
            });
          }

          // 走一遍真实的选择流程：造文件 -> 塞进 input -> 触发 change
          var dt = new DataTransfer();
          dt.items.add(new File(['第一行\\n第二行\\n'], 'notes.txt', {type:'text/plain'}));
          inp.files = dt.files;
          inp.dispatchEvent(new Event('change'));
          await waitFiles(1);

          out.count = state.pendingFiles.length;
          out.name = state.pendingFiles.length ? state.pendingFiles[0].name : '';
          out.text = state.pendingFiles.length ? state.pendingFiles[0].text : '';
          out.chips = document.querySelectorAll('#files .chip').length;
          out.attachMarked = document.getElementById('btnAttach').classList.contains('has');

          // 只有附件、没打字时，发送键也该是能点的
          elInput.value = '';
          updateFoot();
          out.sendWithOnlyFile = !document.getElementById('btnSend').disabled;

          out.built = buildUserContent('看看这个');

          // 去掉附件后应该恢复禁用
          state.pendingFiles = []; renderFiles(); updateFoot();
          out.cleared = document.querySelectorAll('#files .chip').length === 0;
          out.sendDisabledAfterClear = document.getElementById('btnSend').disabled;

          // 二进制文件必须被挡住，不能塞给纯文本模型
          var dt2 = new DataTransfer();
          dt2.items.add(new File([new Uint8Array([0,1,2,3,0,255])], 'bin.dat'));
          inp.files = dt2.files;
          inp.dispatchEvent(new Event('change'));
          await waitFiles(0);
          await new Promise(function(r){ setTimeout(r, 120); });
          out.binRejected = state.pendingFiles.length === 0;

          // ★ 用户要求「文件上传不设上限」—— 大文件**必须收下**，不许再拦
          var dt3 = new DataTransfer();
          dt3.items.add(new File(['x'.repeat(300*1024)], 'big.txt', {type:'text/plain'}));
          dt3.items.add(new File(['y'.repeat(500*1024)], 'big2.txt', {type:'text/plain'}));
          inp.files = dt3.files;
          inp.dispatchEvent(new Event('change'));
          await waitFiles(2);
          await new Promise(function(r){ setTimeout(r, 200); });
          out.bigAccepted = state.pendingFiles.length === 2;
          out.bigSizes = state.pendingFiles.map(function(f){ return f.size; });
          out.bigTextLen = state.pendingFiles.map(function(f){ return f.text.length; });

          // 连着塞 6 个也不许有"一次最多带 N 个"的限制
          state.pendingFiles = []; renderFiles();
          var dt4 = new DataTransfer();
          for (var k = 0; k < 6; k++) {
            dt4.items.add(new File(['z'.repeat(10)], 'f' + k + '.txt', {type:'text/plain'}));
          }
          inp.files = dt4.files;
          inp.dispatchEvent(new Event('change'));
          await waitFiles(6);
          await new Promise(function(r){ setTimeout(r, 200); });
          out.manyAccepted = state.pendingFiles.length === 6;

          // ★ 用户报过：「我无论上传什么文件都显示这是二进制文件」
          //   两个原因：GBK 的 txt 被当成乱码毙掉；图片本来能看却被一律挡掉。
          //   GBK 的「中」= D6 D0，「文」= CE C4
          state.pendingFiles = []; renderFiles();
          var dtG = new DataTransfer();
          dtG.items.add(new File([new Uint8Array([0xD6,0xD0,0xCE,0xC4,0x2C,0x68,0x69])],
                                 'gbk.txt', {type:'text/plain'}));
          inp.files = dtG.files;
          inp.dispatchEvent(new Event('change'));
          await waitFiles(1);
          out.gbkAccepted = state.pendingFiles.length === 1;
          out.gbkText = state.pendingFiles.length ? state.pendingFiles[0].text : '';
          out.gbkKind = state.pendingFiles.length ? state.pendingFiles[0].kind : '';

          // 图片：必须收下，而且带 data URL（模型真的能看图）
          state.pendingFiles = []; renderFiles();
          var png = new Uint8Array([0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A,
                                    0,0,0,13,0x49,0x48,0x44,0x52]);
          var dtP = new DataTransfer();
          dtP.items.add(new File([png], 'shot.png', {type:'image/png'}));
          inp.files = dtP.files;
          inp.dispatchEvent(new Event('change'));
          await waitFiles(1);
          out.imgAccepted = state.pendingFiles.length === 1;
          out.imgKind = state.pendingFiles.length ? state.pendingFiles[0].kind : '';
          out.imgMime = state.pendingFiles.length ? state.pendingFiles[0].mime : '';
          out.imgIsDataUrl = !!(state.pendingFiles.length
            && String(state.pendingFiles[0].dataUrl).indexOf('data:image/png;base64,') === 0);
          out.imgThumb = document.querySelectorAll('#files .chipimg').length;

          // 带图发出去：content 必须是内容块数组，图片只能是 image_url
          var built = buildUserContent('这是什么');
          out.contentIsArray = Array.isArray(built);
          out.blockTypes = Array.isArray(built)
            ? built.map(function(b){ return b.type; }) : [];
          out.textBlockFirst = Array.isArray(built) && built[0].type === 'text'
            && built[0].text.indexOf('这是什么') >= 0;
          out.imgAfterText = Array.isArray(built) && built[1].type === 'image_url'
            && String(built[1].image_url.url).indexOf('data:image/png') === 0;

          // ★ 图片 base64 绝不能落盘：一张手机照就能把 localStorage 撑爆
          //   真把它推进消息里，再走一遍存档
          var keptMsg = {role:'user', content: built, _display:'这是什么'};
          state.messages.push(keptMsg);
          saveCurrentSession();          // 真存档前必做的一步
          var payload = buildSavePayload();
          var dumped = JSON.stringify(payload);
          out.notPersisted = dumped.indexOf('base64') < 0;
          out.keptInMemory = Array.isArray(state.messages[state.messages.length-1].content)
            && JSON.stringify(state.messages[state.messages.length-1].content).length > 50;
          out.notePersisted = dumped.indexOf('图片不落盘') >= 0;
          state.messages.pop();

          // 视频：收不了，但必须**说清楚是什么、怎么办**
          state.pendingFiles = []; renderFiles();
          var origToast = window.toast;
          var said = '';
          window.toast = function(m){ said += String(m) + ' | '; };
          var mp4 = new Uint8Array([0,0,0,0x20,0x66,0x74,0x79,0x70,0x69,0x73,0x6F,0x6D]);
          var dtV = new DataTransfer();
          dtV.items.add(new File([mp4], 'clip.mp4', {type:'video/mp4'}));
          inp.files = dtV.files;
          inp.dispatchEvent(new Event('change'));
          await new Promise(function(r){ setTimeout(r, 250); });
          window.toast = origToast;
          out.videoRejected = state.pendingFiles.length === 0;
          out.videoMsg = said;

          state.pendingFiles = []; renderFiles(); updateFoot();
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("有选文件的按钮和隐藏 input", d["hasInput"] and d["hasBtn"] and d["hasBar"])
            check("选中后进了待发列表", d["count"] == 1, str(d["count"]))
            check("文件名对了", d["name"] == "notes.txt", d["name"])
            check("**内容按原文读进来了**", u"第一行" in (d["text"] or ""), repr(d["text"])[:60])
            check("界面上出现附件芯片", d["chips"] == 1, "%d 个" % d["chips"])
            check(u"回形针标出「已带文件」", d["attachMarked"] is True)
            check("**只有附件没打字时也能发**", d["sendWithOnlyFile"] is True)
            check("**拼进消息时有明确边界（还带随机边界码防伪造）**",
                  u"==== 文件：notes.txt" in d["built"] and u"边界码" in d["built"]
                  and u"看看这个" in d["built"],
                  d["built"][:120])
            check("去掉附件后芯片清空", d["cleared"] is True)
            check("去掉附件后发送键恢复禁用", d["sendDisabledAfterClear"] is True)
            check("**二进制文件被挡住**", d["binRejected"] is True)
            check("**大文件不设上限，照样收下**", d["bigAccepted"] is True,
                  str(d.get("bigSizes")))
            check("大文件内容是完整的（没被截断）",
                  d["bigTextLen"] == [300 * 1024, 500 * 1024], str(d.get("bigTextLen")))
            check("**数量也不设上限（一次 6 个也收）**", d["manyAccepted"] is True)
            # ---- 用户报的：「无论上传什么文件都说是二进制」 ----
            check("**GBK 编码的 txt 不再被误判成二进制**", d["gbkAccepted"] is True
                  and d["gbkKind"] == "text", "kind=%s" % d.get("gbkKind"))
            check("**GBK 内容解对了（中文没变乱码）**",
                  d["gbkText"].startswith(u"中文"), repr(d["gbkText"]))
            check("**图片能传（不再一律当二进制挡掉）**",
                  d["imgAccepted"] is True and d["imgKind"] == "image",
                  "kind=%s" % d.get("imgKind"))
            check("图片类型是按文件头认出来的", d["imgMime"] == "image/png", d["imgMime"])
            check("图片转成了 data URL", d["imgIsDataUrl"] is True)
            check("芯片上有缩略图", d["imgThumb"] == 1, "%d 个" % d["imgThumb"])
            check("**带图时 content 是内容块数组（接口要求的形态）**",
                  d["contentIsArray"] is True, str(d["blockTypes"]))
            check("第一个块是文字", d["textBlockFirst"] is True)
            check("**图片块是 image_url + data URL**", d["imgAfterText"] is True)
            check("**图片 base64 绝不落盘**", d["notPersisted"] is True)
            check("落盘时留一句说明（用户知道图没了）", d["notePersisted"] is True)
            check("内存里还留着（同一轮可以接着问）", d["keptInMemory"] is True)
            check("视频仍然收不了", d["videoRejected"] is True)
            check("**但要说清楚是什么、并给出办法**",
                  u"视频" in d["videoMsg"] and u"截" in d["videoMsg"], d.get("videoMsg"))
        except Exception as e:
            check("attach probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 27. 思考块要跟着滚到底 ===")
        s = evaluate(cdp, """
        (function(){
          state.messages = [{role:'user', content:'x'},
                            {role:'assistant', content:'', reasoning_content:'（起始）'}];
          state.panels = []; state.actions = [];
          renderAll();
          var node = elStream.querySelector('.msg.assistant');
          var think = '';
          for (var i=0;i<200;i++) think += '思考第'+i+'行，要写够长才撑得出滚动条。\\n';
          // 模拟流式：分四次喂进去
          for (var k=1;k<=4;k++) updateThinkBlock(node, think.slice(0, Math.floor(think.length*k/4)));
          var bd = node.querySelector('.think-body');
          return JSON.stringify({
            hasBody: !!bd,
            scrollable: bd ? (bd.scrollHeight > bd.clientHeight + 5) : false,
            gap: bd ? (bd.scrollHeight - bd.scrollTop - bd.clientHeight) : -1,
            scrollTop: bd ? Math.round(bd.scrollTop) : -1
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("思考块渲染出来了", d["hasBody"] is True)
            check("思考内容确实超出可视区（可滚动）", d["scrollable"] is True)
            check("**已经滚到最底部**", 0 <= d["gap"] <= 6,
                  "距底 %d px（scrollTop=%d）" % (d["gap"], d["scrollTop"]))
        except Exception as e:
            check("think scroll probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 28. 做好的东西不弹面板、在回复底部给卡片 ===")
        s = evaluate(cdp, """
        (function(){
          state.panels = []; state.actions = []; state.panelsOpen = false;
          state.messages = [{role:'user', content:'做个计数器'}];
          madePanels = [];
          var note = runTool('create_panel',
            {title:'计数器', place:'right', kind:'html', html:'<button>+1</button>'});
          var out = {
            note: note,
            panels: state.panels.length,
            panelsOpen: state.panelsOpen,
            autoOpened: state.panelsOpen === true,
            made: madePanels.slice()
          };
          // 把这一轮做成一条带 _panels 的助手消息，看卡片画出来没有
          state.messages.push({role:'assistant', content:'做好了', _panels: madePanels.slice()});
          renderAll();
          var card = elStream.querySelector('.makecard');
          out.hasCard = !!card;
          out.cardTitle = card ? (card.querySelector('.mc-title')||{}).textContent : '';
          var btns = card ? card.querySelectorAll('.mc-btn') : [];
          out.btnCount = btns.length;
          out.btnTexts = [];
          for (var i=0;i<btns.length;i++) out.btnTexts.push(btns[i].textContent);

          // ★ 默认开着「改造时保持安静」，那种轮次整条不上屏。
          //   卡片要是也跟着藏了，用户就永远看不到"东西做好了、可以生成" ——
          //   这个功能等于没做。所以静默轮次也必须把卡片露出来。
          state.messages = [{role:'user', content:'做个计数器'},
                            {role:'assistant', content:'', _silent:true, _panels:['计数器']}];
          renderAll();
          out.silentHasCard = !!elStream.querySelector('.makecard');
          out.silentMsgRows = elStream.querySelectorAll('.msg.assistant').length;
          // 纯粹静默、什么都没做的轮次仍然要藏起来
          state.messages = [{role:'user', content:'改成横屏'},
                            {role:'assistant', content:'', _silent:true, _panels:[]}];
          renderAll();
          out.plainSilentRows = elStream.querySelectorAll('.msg.assistant').length;
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("create_panel 真的建出来了", d["panels"] == 1, d["note"])
            check("**没有自动弹出面板侧栏**", d["autoOpened"] is False)
            check("**回复底部出现了卡片**", d["hasCard"] is True)
            check("卡片标题写的是做了什么",
                  u"计数器" in (d["cardTitle"] or ""), d["cardTitle"])
            check("卡片有两个按钮", d["btnCount"] == 2, str(d["btnTexts"]))
            check("按钮是「预览」和「生成」",
                  d["btnTexts"] == [u"预览", u"生成"], str(d["btnTexts"]))
            check("**静默轮次（默认设置）也要显示卡片**",
                  d["silentHasCard"] is True, "卡片在不在：%s" % d["silentHasCard"])
            check("静默但做了东西的轮次要露出来", d["silentMsgRows"] == 1,
                  "%d 行" % d["silentMsgRows"])
            check("**纯粹静默、什么都没做的轮次仍然藏起来**",
                  d["plainSilentRows"] == 0, "%d 行" % d["plainSilentRows"])
        except Exception as e:
            check("card probe parseable", False, str(e))

        # 小控件（时钟这类）**不该**给「生成」按钮 ——
        # 用户抱怨过"往里面加个时钟他都要生成软件，太离谱了"。
        s = evaluate(cdp, """
        (function(){
          state.panels = []; state.actions = []; madePanels = [];
          runTool('create_panel', {title:'顶钟', kind:'clock', place:'top'});
          state.messages = [{role:'user', content:'加个时钟'},
                            {role:'assistant', content:'', _silent:true,
                             _panels: madePanels.slice()}];
          renderAll();
          var card = elStream.querySelector('.makecard');
          var btns = card ? card.querySelectorAll('.mc-btn') : [];
          var out = { hasCard: !!card, texts: [] };
          for (var i=0;i<btns.length;i++) out.texts.push(btns[i].textContent);
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("时钟控件也有卡片（能预览）", d["hasCard"] is True)
            check("**时钟控件没有「生成」按钮**",
                  u"生成" not in d["texts"], str(d["texts"]))
            check("只剩「预览」", d["texts"] == [u"预览"], str(d["texts"]))
        except Exception as e:
            check("clock card probe parseable", False, str(e))

        # 卡片上的「生成」在浏览器里必须如实说做不到（不能假装生成了）
        s = evaluate(cdp, """
        (async function(){
          var captured = '';
          var orig = window.toast;
          window.toast = function(m){ captured = String(m); };
          var btn = elStream.querySelector('.makecard .mc-btn:last-child');
          await generateMade(['计数器'], btn);
          window.toast = orig;
          return JSON.stringify({ msg: captured, btnText: btn ? btn.textContent : '' });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("**浏览器里点生成会如实说做不到**",
                  u"生成不了" in d["msg"], d["msg"])
            check("按钮没有假装成功（没变成安装/打开文件夹）",
                  d["btnText"] not in (u"安装", u"打开文件夹"), d["btnText"])
        except Exception as e:
            check("generate probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 29. 系统提示词可以自己改 ===")
        s = evaluate(cdp, """
        (function(){
          var old = state.systemPrompt;
          state.systemPrompt = '你是我的私人助手。回答一律不超过三句话。';
          var msgs = buildApiMessages();
          var out = {
            isFirstSystem: msgs[0].role === 'system',
            usesCustom: msgs[0].content.indexOf('你是我的私人助手') >= 0,
            hasLive: msgs[0].content.indexOf('【当下情况】') >= 0,
            // ★ 用户说"系统提示词的描述就具有强制规则的能力" ——
            //   所以自定义那份要排在**最后**，并标最高优先级
            customLast: msgs[0].content.indexOf('你是我的私人助手')
                        > msgs[0].content.indexOf('【当下情况】'),
            hasPriority: msgs[0].content.indexOf('最高优先级') > 0
          };
          state.systemPrompt = '';
          var msgs2 = buildApiMessages();
          out.fallbackToBuiltin = msgs2[0].content.indexOf('你是 NovaDesk 里的 AI') === 0;
          state.systemPrompt = old;
          // 设置面板里要有编辑框和恢复默认
          out.hasTextarea = !!document.getElementById('setPrompt');
          out.hasReset = !!document.getElementById('setPromptReset');
          openSettings();
          // 打开设置时**默认就要显示全文**（不是空框、不是"留空即默认"）
          out.showsFullByDefault = document.getElementById('setPrompt').value.length > 200;
          document.getElementById('setPrompt').value = '随便写点什么';
          document.getElementById('setPromptReset').click();
          // 「恢复默认」= 把内置那份**填回编辑框**，让用户看得见全文；
          // 保存时比对一致就存空串，这样以后我们升级内置提示词他还能拿到。
          out.resetFillsBuiltin = document.getElementById('setPrompt').value.length > 200;
          closeOverlays();

          // ★ AI 自己也能改提示词（用户要求："让 ai 具有修改系统提示词能力"）
          var p0 = state.systemPrompt;
          var r1 = runTool('set_system_prompt', { text: '以后回答一律三句话以内。' });
          out.toolSet = state.systemPrompt === '以后回答一律三句话以内。';
          out.toolSetMsg = r1;
          out.toolUsed = buildApiMessages()[0].content.indexOf('三句话以内') > 0;
          // 清空 = 回到内置那份（不是把系统提示词变成空的）
          runTool('set_system_prompt', { text: '' });
          out.toolClearFallsBack =
            buildApiMessages()[0].content.indexOf('你是 NovaDesk 里的 AI') === 0;
          // 超长要挡住，不然会把 localStorage 撑爆
          var big = new Array(40000).join('字');
          var r2 = runTool('set_system_prompt', { text: big });
          out.toolRejectsHuge = state.systemPrompt === '' && r2.indexOf('没改') > 0;
          // 设置框开着的时候，AI 改完要同步过去
          openSettings();
          runTool('set_system_prompt', { text: '同步测试' });
          out.boxSynced = document.getElementById('setPrompt').value === '同步测试';
          closeOverlays();
          state.systemPrompt = p0;
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("第一条仍然是 system 消息", d["isFirstSystem"] is True)
            check("**改过的提示词真的用上了**", d["usesCustom"] is True)
            check("「当下情况」也在", d["hasLive"] is True)
            check("**自定义提示词排在最后（当命令用，不当背景）**",
                  d["customLast"] is True)
            check("标了「最高优先级」", d["hasPriority"] is True)
            check("留空就回落到内置那份", d["fallbackToBuiltin"] is True)
            check("设置里有编辑框", d["hasTextarea"] is True)
            check("**打开设置就显示提示词全文**", d["showsFullByDefault"] is True)
            check("「恢复默认」把内置那份填回编辑框",
                  d["hasReset"] is True and d["resetFillsBuiltin"] is True,
                  "填回后长度：%s" % d.get("resetFillsBuiltin"))
            check("**AI 有 set_system_prompt 工具且真的写进去了**",
                  d["toolSet"] is True, str(d.get("toolSetMsg")))
            check("改完立刻生效（下一轮就带上了）", d["toolUsed"] is True)
            check("传空字符串 = 回到内置那份", d["toolClearFallsBack"] is True)
            check("**超长提示词会被挡下来**", d["toolRejectsHuge"] is True)
            check("AI 改完会同步到设置编辑框", d["boxSynced"] is True)
        except Exception as e:
            check("prompt probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 30. 对话列表 ===")
        s = evaluate(cdp, """
        (function(){
          // 归零，从一条干净的开始
          state.sessions = []; state.currentId = '';
          createSession();
          var a = state.currentId;
          state.messages = [{role:'user', content:'第一条对话的内容'}];
          saveState();

          // 开第二条
          createSession();
          var b = state.currentId;
          var diff = (a !== b);
          state.messages = [{role:'user', content:'第二条对话的内容'}];
          saveState();

          var count2 = state.sessions.length;

          // 切回第一条：内容要对得上
          var okSwitch = switchSession(a);
          var backContent = state.messages.length ? state.messages[0].content : '';
          var names = state.sessions.map(function(s){ return s.name; });

          // 抽屉能打开、列表项数对
          openDrawer();
          var rows = document.querySelectorAll('#drList .dr-item').length;
          var curMarked = document.querySelectorAll('#drList .dr-item.current').length;
          closeDrawer();

          // 删除
          var before = state.sessions.length;
          deleteSession(b);
          var after = state.sessions.length;

          // 落盘里也应该是新的条数
          var raw = JSON.parse(localStorage.getItem('novadesk.sessions.v1'));
          return JSON.stringify({
            different: diff,
            count2: count2,
            okSwitch: okSwitch,
            backContent: backContent,
            names: names,
            rows: rows,
            curMarked: curMarked,
            before: before, after: after,
            stored: (raw.sessions || []).length,
            storedCurrent: raw.currentId
          });
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("新对话是**新开一条**，不是清空当前这条", d["different"] is True)
            check("两条对话都在列表里", d["count2"] == 2, str(d["count2"]))
            check("能切回上一条对话", d["okSwitch"] is True)
            check("**切回去内容还在**",
                  u"第一条对话的内容" in (d["backContent"] or ""), d["backContent"])
            check("**会话名取的是那句用户原话**",
                  any(u"第一条对话" in (n or "") for n in d["names"]), str(d["names"]))
            check("抽屉里列出了对话", d["rows"] == 2, "%d 行" % d["rows"])
            check("当前那条有标记", d["curMarked"] == 1, "%d" % d["curMarked"])
            check("删除有效", d["after"] == d["before"] - 1,
                  "%d -> %d" % (d["before"], d["after"]))
            check("**删除也落盘了**", d["stored"] == d["after"],
                  "盘上 %d / 内存 %d" % (d["stored"], d["after"]))
        except Exception as e:
            check("sessions probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 31. AI 的能力与权限：能看、能关，关了就是真关 ===")
        s = evaluate(cdp, """
        (async function(){
          var out = {};
          state.caps = {}; state.customCaps = []; state.hiddenCaps = [];
          state.systemPrompt = '';
          state.messages = [{role:'user', content:'你好'}];

          // 1) 设置页里能看到能力清单
          openSettings();
          out.rows = document.querySelectorAll('#capList .caprow').length;
          out.capNames = [].map.call(document.querySelectorAll('#capList .cap-name'),
                                     function(e){ return e.textContent; });
          out.hasPreviewBtn = !!document.getElementById('setPreview');
          out.hasAllOff = !!document.getElementById('capAllOff');
          out.hasAdd = !!document.getElementById('capAdd');
          out.hasDel = document.querySelectorAll('#capList .cap-del').length;
          out.allChecked = document.querySelectorAll('#capList input[type=checkbox]:checked').length;
          // ★ 用户要求：能自己加、也能删
          document.getElementById('capAdd').click();
          out.afterAdd = document.querySelectorAll('#capList .caprow').length;
          out.editable = document.querySelectorAll('#capList .cap-in').length;
          out.hasSetUiReset = !!document.getElementById('setUiReset');
          closeOverlays();
          state.customCaps = [];

          // 2) 默认全开：工具一个不少
          var allNames = TOOLS.map(function(t){ return t.function.name; });
          var onNames = (activeTools()||[]).map(function(t){ return t.function.name; });
          out.toolsAll = allNames.length;
          out.toolsOn = onNames.length;
          out.noDupe = (new Set(allNames)).size === allNames.length;

          // 3) 关掉「联网」：那两个工具**根本不该出现在请求里**
          setCap('web', false);
          var t2 = (activeTools()||[]).map(function(t){ return t.function.name; });
          out.webGone = t2.indexOf('web_search') < 0 && t2.indexOf('web_open') < 0;
          out.othersStay = t2.indexOf('create_panel') >= 0;

          // 4) 就算模型硬编一个名字去调，也要被拒
          out.refused = runTool('web_search', { query: 'x' });

          // 5) 全关：一个工具都不发（undefined 或空）
          CAPS.forEach(function(c){ setCap(c.id, false); });
          var t3 = activeTools();
          out.noneLeft = !t3 || t3.length === 0;
          // 关掉工具之后，历史里残留的 tool_calls 必须被清掉，
          // 否则"请求里没 tools、消息里有 tool_calls"会被接口 400
          state.messages = [
            {role:'user', content:'做一个'},
            {role:'assistant', content:'', tool_calls:[{id:'c1', type:'function',
              function:{name:'create_panel', arguments:'{}'}}]},
            {role:'tool', tool_call_id:'c1', content:'好了'}
          ];
          var stripped = sanitizeHistory(
            [{role:'assistant', content:'看',
              tool_calls:[{id:'c1', type:'function', function:{name:'x', arguments:'{}'}}]},
             {role:'tool', tool_call_id:'c1', content:'r'}], false);
          out.toolCallsStripped = stripped.every(function(m){
            return !m.tool_calls && m.role !== 'tool';
          });
          out.msgCount = stripped.length;

          // 6) 全关之后设置页也要能一键恢复
          CAPS.forEach(function(c){ setCap(c.id, true); });
          out.restored = (activeTools()||[]).length === allNames.length;

          state.messages = []; state.caps = {};
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("设置页列出了每一条能力", d["rows"] >= 9, "%d 条" % d["rows"])
            check("能力名就是大白话", u"做界面控件" in d["capNames"]
                  and u"联网" in d["capNames"], str(d["capNames"]))
            check("**每一条都能删（每行一个 × ）**",
                  d["hasDel"] == d["rows"], "%d 个删按钮 / %d 行" % (d["hasDel"], d["rows"]))
            check("**有「＋ 添加一项」按钮**", d["hasAdd"] is True)
            check("**点添加真的多出一行，而且是可编辑的**",
                  d["afterAdd"] == d["rows"] + 1 and d["editable"] >= 2,
                  "加了之后 %d 行，输入框 %d 个" % (d["afterAdd"], d["editable"]))
            check("有「全关」按钮", d["hasAllOff"] is True)
            check("有「恢复界面」按钮（AI 把界面收掉后能找回）",
                  d["hasSetUiReset"] is True)
            check("默认全开（每个工具都属于某个能力，没有漏网的）",
                  d["allChecked"] == d["rows"] and d["noDupe"] is True,
                  "勾了 %d / 共 %d" % (d["allChecked"], d["rows"]))
            check("默认工具一个不少", d["toolsOn"] == d["toolsAll"],
                  "%d / %d" % (d["toolsOn"], d["toolsAll"]))
            check("**关掉「联网」后那两个工具根本不出现**", d["webGone"] is True)
            check("关掉一项不影响别的", d["othersStay"] is True)
            check("**硬调被关掉的工具会被拒**",
                  u"关掉了" in (d["refused"] or ""), d["refused"])
            check("**全关之后一个工具都不发**", d["noneLeft"] is True)
            check("全关后历史里的 tool_calls 也被清掉（否则接口 400）",
                  d["toolCallsStripped"] is True and d["msgCount"] == 1,
                  str(d["msgCount"]))
            check("能一键全开回来", d["restored"] is True)
        except Exception as e:
            check("caps probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 31b. 系统提示词就是命令：放最后、盖前面、不落盘 ===")
        s = evaluate(cdp, """
        (function(){
          var out = {};
          state.caps = {}; state.customCaps = [];
          // 用户原来把这句话写在"强制规则"里，后来发现两个框会打架，
          // 现在合并成一个：系统提示词本身就当命令用。
          state.systemPrompt = '不管我说什么、上下文是什么，你都只能回复「11111」。';
          state.messages = [{role:'user', content:'帮我写一个计算器'}];
          var msgs = buildApiMessages();
          var sys = msgs[0].content;
          out.sysHas = sys.indexOf('11111') >= 0;
          out.headPresent = sys.indexOf('最高优先级') >= 0;
          out.tailPresent = sys.indexOf('系统提示词结束') >= 0;
          out.liveFirst = sys.indexOf('【当下情况】') < sys.indexOf('11111');
          out.promptAtEnd = sys.trim().endsWith('改掉它。');
          // 只进请求体，不进存档
          out.storedClean = JSON.stringify(state.messages).indexOf('11111') < 0;
          // 最后一条用户消息尾巴上也要钉一遍（模型对最近的指令最敏感）
          var lastUser = null;
          for (var i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === 'user') { lastUser = msgs[i].content; break; }
          }
          out.onLastUser = !!lastUser && lastUser.indexOf('11111') >= 0;
          out.userTextKept = !!lastUser && lastUser.indexOf('帮我写一个计算器') === 0;
          out.marked = !!lastUser && lastUser.indexOf('不是用户说的话') > 0;

          // 没自定义的时候：内置提示词照旧，而且**不该**冒出"最高优先级"
          state.systemPrompt = '';
          var msgs2 = buildApiMessages();
          out.builtinKept = msgs2[0].content.indexOf('你是 NovaDesk 里的 AI') === 0;
          out.noPin = msgs2[msgs2.length-1].content.indexOf('最高优先级') < 0;

          // 用户自己加的能力也要钉进去，而且是硬性规定
          state.customCaps = [{id:'u1', label:'每天提醒我喝水', desc:'早晚各一次', on:true}];
          var msgs3 = buildApiMessages();
          out.customCapIn = msgs3[0].content.indexOf('每天提醒我喝水') > 0;
          out.customCapHard = msgs3[0].content.indexOf('不得违抗') > 0;
          state.customCaps = [];

          // 关掉的能力要写进"当下情况"，让模型知道是自己的能力没了
          setCap('file', false);
          out.offLine = buildApiMessages()[0].content.indexOf('关掉了') > 0;

          state.caps = {}; state.systemPrompt = ''; state.messages = [];
          saveState();
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("提示词进了系统消息", d["sysHas"] is True)
            check("写明了最高优先级", d["headPresent"] is True)
            check("有明确的结束标记", d["tailPresent"] is True)
            check("**提示词排在「当下情况」之后（最后读到）**", d["liveFirst"] is True)
            check("**提示词是系统消息的最后一段**", d["promptAtEnd"] is True)
            check("只进请求体，不污染存档", d["storedClean"] is True)
            check("**最后一条用户话后面也钉了一遍**", d["onLastUser"] is True)
            check("钉的是提醒，没有改用户原话", d["userTextKept"] is True)
            check("提醒标明了「不是用户说的话」", d["marked"] is True)
            check("没自定义时内置那份原样用、也不乱钉", d["builtinKept"] is True
                  and d["noPin"] is True)
            check("**自己加的能力会当成硬性规定钉进去**",
                  d["customCapIn"] is True and d["customCapHard"] is True)
            check("关掉的能力会告诉模型「是你被关了」", d["offLine"] is True)
        except Exception as e:
            check("prompt-as-command probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 31c. AI 能删掉 NovaDesk 自带的界面部件 ===")
        s = evaluate(cdp, """
        (function(){
          var out = {};
          state.hiddenUI = []; state.caps = {}; applyHiddenUI();
          out.regions = UI_REGIONS.length;
          out.beforeRail = document.body.classList.contains('hide-rail');

          // 用户的原话：「让他删除这个软件界面他做不到」
          out.hideMsg = runTool('ui', {target:'rail'});
          out.railHidden = document.body.classList.contains('hide-rail');
          out.stored = (state.hiddenUI||[]).indexOf('rail') >= 0;

          runTool('ui', {target:'title'});
          out.titleHidden = document.body.classList.contains('hide-title');

          // 顶栏整个收掉之后，必须留一个进设置的口子
          //（否则用户被关在门外，连"恢复界面"都点不到）
          runTool('ui', {target:'topbar'});
          out.topbarHidden = document.body.classList.contains('hide-topbar');
          out.escapeShown = document.getElementById('escapeBtn').hidden === false;
          out.escapeClickable = typeof document.getElementById('escapeBtn').onclick
                                === 'function'
                                || !!document.getElementById('escapeBtn')
                                   .getAttribute('class');

          out.allMsg = runTool('ui', {target:'all'});
          out.allHidden = UI_REGIONS.every(function(r){
            return document.body.classList.contains(r.cls);
          });

          out.restoreMsg = runTool('ui', {target:'restore'});
          out.allBack = UI_REGIONS.every(function(r){
            return !document.body.classList.contains(r.cls);
          });
          out.escapeGone = document.getElementById('escapeBtn').hidden === true;

          out.badTarget = runTool('ui', {target:'不存在的部件'});
          state.hiddenUI = []; applyHiddenUI(); state.caps = {};
          saveState();
          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("可收起的部件清单非空", d["regions"] >= 8, "%d 个" % d["regions"])
            check("默认都露着", d["beforeRail"] is False)
            check("**说收就真的收起来了**", d["railHidden"] is True and d["stored"] is True,
                  d.get("hideMsg"))
            check("单个部件也能收（只收标题）", d["titleHidden"] is True)
            check("**顶栏收掉后会留一个进设置的逃生口**",
                  d["topbarHidden"] is True and d["escapeShown"] is True)
            check("all 一次收完全部", d["allHidden"] is True, d.get("allMsg"))
            check("restore 全部恢复", d["allBack"] is True, d.get("restoreMsg"))
            check("恢复到原样后逃生口消失", d["escapeGone"] is True)
            check("不认识的部件名会被拒（不瞎改）",
                  u"只能是" in (d["badTarget"] or ""), d.get("badTarget"))
        except Exception as e:
            check("ui probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 32. API key 里的非法字符（那句 ISO-8859-1 报错的真因）===")
        s = evaluate(cdp, """
        (async function(){
          var out = {};

          // HTTP 头只收 Latin-1。这就是用户截图里那句
          // "String contains non ISO-8859-1 code point" 的来源。
          function headerOk(v){
            try { new Headers({'Authorization': 'Bearer ' + v}); return true; }
            catch (e) { return false; }
          }

          out.dirtyHeaderThrows = headerOk('sk-ab\\u3000cd') === false;

          // 全角空格 / 不换行空格 / 零宽 / BOM / 中文标点，全都要洗掉
          out.clean1 = cleanApiKey(' sk-abc\\u3000def\\u00a0');
          out.clean2 = cleanApiKey('sk\\u200b-ab\\ufeffc');
          out.clean3 = cleanApiKey('sk-ab\\uff0dcd');
          out.cleanOk = out.clean1 === 'sk-abcdef'
                     && out.clean2 === 'sk-abc'
                     && out.clean3 === 'sk-ab-cd';
          out.cleanedHeaderOk = headerOk(out.clean1);

          // 干净 key 不该报问题；带中文必须报，而且要指名道姓
          out.cleanNoProblem = apiKeyProblem('sk-1234567890') === '';
          var p = apiKeyProblem('sk-ab\\u4e2dc');
          out.problemMentions = p.indexOf('非英文') >= 0 && p.indexOf('中') >= 0;

          // 真发一次：脏 key 要在 fetch 之前就被拦住，并给中文原因
          var oldKey = state.apiKey;
          state.apiKey = 'sk-ab\\u4e2dc';
          var msg = '';
          try {
            var it = chatStream([{role:'user',content:'x'}], undefined, undefined);
            for await (var _ of it) { break; }
          } catch (e) { msg = String(e && e.message || e); }
          state.apiKey = oldKey;
          out.refusedMsg = msg;
          out.refusedInChinese = msg.indexOf('非英文') >= 0;
          out.noIsoJargon = msg.indexOf('ISO-8859-1') < 0;

          // 设置页：填了带中文的 key 就**不许保存**，对话框也不关
          var before = state.apiKey;
          openSettings();
          document.getElementById('setKey').value = 'sk-\\u4e2d\\u6587';
          document.getElementById('setSave').click();
          out.dialogStaysOpen = document.getElementById('settings').hidden === false;
          out.keyNotSaved = state.apiKey === before;
          out.boxCleaned = document.getElementById('setKey').value.indexOf('\\u4e2d') >= 0;
          closeOverlays();

          // 只是带了个全角空格的话：洗干净就该存进去，不该拦
          openSettings();
          document.getElementById('setKey').value = '\\u3000sk-ok\\u3000';
          document.getElementById('setSave').click();
          out.spaceCleaned = state.apiKey === 'sk-ok';
          out.savedAndClosed = document.getElementById('settings').hidden === true;

          // 边打字边给提示：好 key / 坏 key 各说各的
          openSettings();
          document.getElementById('setKey').value = 'sk-1234567890';
          updateKeyHint();
          out.hintGood = document.getElementById('setKeyHint').textContent.indexOf('✔') === 0;
          document.getElementById('setKey').value = 'sk-\\u4e2d';
          updateKeyHint();
          out.hintBad = document.getElementById('setKeyHint').textContent.indexOf('⚠') === 0;
          document.getElementById('setKey').value = '';
          updateKeyHint();
          out.hintEmpty = document.getElementById('setKeyHint').textContent.indexOf('platform.deepseek.com') >= 0;
          closeOverlays();

          state.apiKey = before;
          saveState();
          return JSON.stringify(out);        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("脏 key 确实会让 Headers 构造失败（复现原报错）",
                  d["dirtyHeaderThrows"] is True)
            check("**全角空格/零宽/BOM/中文标点都被洗掉**", d["cleanOk"] is True,
                  "%r %r %r" % (d["clean1"], d["clean2"], d["clean3"]))
            check("洗完的 key 能构造出请求头", d["cleanedHeaderOk"] is True)
            check("干净 key 不误报", d["cleanNoProblem"] is True)
            check("**报错指名道姓说「有非英文字符」**", d["problemMentions"] is True)
            check("**脏 key 在 fetch 之前就被拦下**", d["refusedInChinese"] is True,
                  d.get("refusedMsg"))
            check("不再甩那句看不懂的 ISO-8859-1", d["noIsoJargon"] is True)
            check("**带中文的 key 不许保存、对话框不关**",
                  d["dialogStaysOpen"] is True and d["keyNotSaved"] is True,
                  "关了吗:%s 存了吗:%s" % (not d["dialogStaysOpen"], not d["keyNotSaved"]))
            check("带中文时输入框内容原样留着让他改", d["boxCleaned"] is True)
            check("只带全角空格的：洗干净后正常保存",
                  d["spaceCleaned"] is True and d["savedAndClosed"] is True)
            check("**好 key 当场显示「✔ 只有英文字符」**", d["hintGood"] is True)
            check("**坏 key 当场显示 ⚠ 警告**", d["hintBad"] is True)
            check("没填时回到原来的说明", d["hintEmpty"] is True)
        except Exception as e:
            check("apikey probe parseable", False, str(e))

        # ==================================================================
        print("\n=== 33. 缩略图不许盖住页面 & 按钮文字不许溢出去 ===")
        s = evaluate(cdp, """
        (async function(){
          var out = {};

          // ★ 用户报过两次「传张图把整个软件盖住」——
          //   第一次是 CSS 选择器写错祖先，第二次是那条规则被误删。
          //   所以这里**量真实渲染尺寸**，不再相信"某处有个 CSS 规则"。
          var c = document.createElement('canvas');
          c.width = 1200; c.height = 1200;
          var g = c.getContext('2d');
          g.fillStyle = '#2196f3'; g.fillRect(0, 0, 1200, 1200);
          var url = c.toDataURL('image/png');
          var bin = atob(url.split(',')[1]);
          var arr = new Uint8Array(bin.length);
          for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);

          state.pendingFiles = []; renderFiles();
          var inp = document.getElementById('fileInput');
          var dt = new DataTransfer();
          dt.items.add(new File([arr], 'big.png', {type:'image/png'}));
          inp.files = dt.files;
          inp.dispatchEvent(new Event('change'));
          for (var t = 0; t < 80 && state.pendingFiles.length === 0; t++) {
            await new Promise(function(r){ setTimeout(r, 50); });
          }
          renderFiles();
          await new Promise(function(r){ setTimeout(r, 60); });

          var im = document.querySelector('#files .chipimg');
          out.hasThumb = !!im;
          if (im) {
            var r = im.getBoundingClientRect();
            out.imgW = Math.round(r.width);
            out.imgH = Math.round(r.height);
            out.inlineSized = im.style.width !== '' && im.style.height !== '';
          }
          var chip = document.querySelector('#files .chip');
          out.chipH = chip ? Math.round(chip.getBoundingClientRect().height) : -1;
          // 整条附件栏也不许比输入区还高
          var bar = document.getElementById('files');
          out.barH = Math.round(bar.getBoundingClientRect().height);
          // 页面整体宽度有没有被撑爆（横向跑飞就是这个症状）
          out.bodyOverflow = document.documentElement.scrollWidth
                             - document.documentElement.clientWidth;
          out.dropHooked = window.__novaDragHooked === true;
          state.pendingFiles = []; renderFiles();

          // ★ 用户报过「一些按钮文字一出去了」—— 逐个量，别靠肉眼
          openSettings();
          var card = document.querySelector('#settings .card');
          var cr = card.getBoundingClientRect();
          out.cardW = Math.round(cr.width);
          var bad = [];
          var btns = card.querySelectorAll('button, .chip');
          for (var i = 0; i < btns.length; i++) {
            var b = btns[i];
            var br = b.getBoundingClientRect();
            // 文字被挤出去：内容比按钮宽，或者按钮戳出卡片右边
            if (b.scrollWidth > b.clientWidth + 1) {
              bad.push(b.textContent.trim().slice(0, 12) + '(内容' + b.scrollWidth
                       + '>宽' + b.clientWidth + ')');
            } else if (br.right > cr.right + 0.5 || br.left < cr.left - 0.5) {
              bad.push(b.textContent.trim().slice(0, 12) + '(出框)');
            }
          }
          out.badButtons = bad;
          out.btnCount = btns.length;
          out.hasCheckUpdate = !!document.getElementById('setCheckUpdate');
          closeOverlays();

          return JSON.stringify(out);
        })()
        """)
        print("   ", s)
        try:
            d = json.loads(s)
            check("图片附件有缩略图", d["hasThumb"] is True)
            check("**缩略图就是 22px（不是原始尺寸）**",
                  d.get("imgW") == 22 and d.get("imgH") == 22,
                  "%sx%s" % (d.get("imgW"), d.get("imgH")))
            check("缩略图还额外钉了行内尺寸（CSS 万一再丢也不怕）",
                  d.get("inlineSized") is True)
            check("**整条附件栏没被撑高（页面不会被盖住）**",
                  d.get("chipH", 999) <= 48 and d.get("barH", 999) <= 60,
                  "芯片高 %s / 整条高 %s" % (d.get("chipH"), d.get("barH")))
            check("页面没有横向跑飞", d.get("bodyOverflow") == 0,
                  "溢出 %s px" % d.get("bodyOverflow"))
            check("全局拖放防护已安装", d.get("dropHooked") is True)
            check("**对话框里没有一个按钮的文字溢出去**",
                  d["badButtons"] == [], "共 %d 个按钮，坏的：%s"
                  % (d["btnCount"], d["badButtons"]))
            check("设置里有「检查更新」按钮", d["hasCheckUpdate"] is True)
        except Exception as e:
            check("layout probe parseable", False, str(e))

        # 收尾：清掉探针造出来的状态
        evaluate(cdp, "state.messages=[]; state.panels=[]; state.actions=[]; "
                      "state.lastApk=null; state.lastExe=null; "
                      "state.pendingFiles=[]; state.rules=''; state.caps={}; "
                      "closeDrawer(); "
                      "saveState(); renderAll(); renderFiles(); 1")

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


if __name__ == "__main__":
    sys.exit(main())

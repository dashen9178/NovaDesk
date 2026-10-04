# NovaDesk 交接文档

> **给下一个 AI 看的。** 这个用户不懂编程，是产品负责人。
> 新会话不用让他重讲一遍，读完本文就能接上活。
>
> **每次对话结束前都要更新这份文档** —— 尤其是"当前进度""下一步""已知问题"三节。
> 不更新的话它会从"记忆"变成"误导"。

---

## 一、和这个用户打交道

1. **全程说中文**，说人话，不要堆术语。他会直接说"听不懂，一句话讲完"。
2. **他比你更清楚哪里难用**。这个项目几乎所有功能细节都是他一条条报出来的，
   而且**他基本是对的**。不要辩解，去查代码。
3. **别让他为你的失误买单**。他反复被"越改越差"折磨过，原话：
   > 「你能不能一次性做好啊？怎么越做越差」
   > 「我求你了，你一次性改好可以不，已经改了好多次了，token要见底了😭」

   所以：改之前先摸清影响面，**改完自己跑验证**，别把半成品发给他。
4. **他性子急**。会催「快点啊」。给进度要具体，别含糊。
5. **发现问题要主动修**。他会说"顺便扫描所有代码检查漏洞并修复bug"——
   这是他给的长期授权，不是一次性的。
6. **不懂就问他**。他会主动说"不懂问我"，而且问了他真的会答。

---

## 二、这是什么

**NovaDesk** —— 一个"能自由创造的 AI 应用"。你跟它说想要什么，它**当场做出来**，
做出来的东西留在界面上，下次打开还在。

三种形态，**共用同一份网页代码**（`NovaDesk.html` 是单文件交付物）：

| 形态 | 产物 | 外壳 |
|---|---|---|
| 电脑桌面版 | `NovaDesk.exe`（0.64 MB） | Win32 + WebView2（C++），页面**内嵌在 exe 尾部** |
| 安卓版 | `NovaDesk-2.7.apk`（3.87 MB） | WebView 壳 + 4 个原生桥（Kotlin） |
| 网页版 | `NovaDesk.html`（约 219 KB） | 双击即用，但**没有文件读写、没有导出、没有 ADB** |

**它和普通聊天 AI 的根本区别：它有工具，能真的改东西。**

---

## 三、代码在哪、怎么跑

```
D:\DSH工作\NovaDesk\
  NovaDesk.html          ← 网页版交付物（由 ai/build.py 生成，别手改）
  README.md              ← 面向用户的说明（比本文详细，但视角是"产品"）
  HANDOFF.md             ← 就是本文（面向 AI 的交接）
  ai/                    ← 网页端全部源码
    index.html           ← 骨架 + 全部 CSS（M3 设计令牌）
    core.js              ← 状态/存档/多会话/DeepSeek 客户端/工具定义与执行
    ui.js                ← 系统提示词/Markdown/界面渲染/流式与工具循环/各原生桥的界面逻辑
    build.py             ← 把上面几个内联成单文件（ES module 在 file:// 下会被 CORS 拦）
    release.py           ← 打包 → 同步 assets → 编译 APK → 校验 → 拷到分享目录
    verify.py            ← **376 项**浏览器实测断言（伪造接口，不烧 token）
    real_rules.py        ← 真实接口验「强制规则」管不管用（会花钱，每次几十 token）
    real_image.py        ← 真实接口验「图片能不能看」（画一张图发过去问）
    verify_web_lib.py    ← 无头 Edge 的 CDP 客户端
    real_*.py            ← 真实接口测试（会花钱，慎跑）
  android/               ← 安卓壳
    app/                 ← 主应用
      src/main/java/com/novadesk/app/
        MainActivity.kt  ← 沉浸/刘海/返回键/文件选择器/SSRF 拦截/Shizuku 回调
        FileBridge.kt    ← 文件读写（off/folder/all 三档）
        NetBridge.kt     ← 联网（在 MainActivity.kt 里，不是单独文件）—— 绕开跨域
        ApkBridge.kt     ← 导出 APK 的入口
        ApkExporter.kt   ← 套壳 → 改清单 → 塞页面 → 规范 ZIP → 重签名 → 自检
        ShizukuBridge.kt ← **ADB 权限**（shell 身份）
        DeviceKey.kt     ← 设备签名身份（首次导出时现生成）
        AxmlRewriter.java← 二进制 AndroidManifest 字符串池重写（纯 Java，可 PC 测）
        DerX509.java     ← 最小 X.509 自签证书生成器（纯 Java，不引 BouncyCastle）
      src/test/          ← JVM 单元测试（拿真壳 APK 走完整条导出链路）
    shell/               ← 导出 APK 用的"模具"
  desktop/               ← 电脑桌面版
    build.ps1            ← 一条命令：编壳 → 打包网页端 → 追加页脚 → 自检
    verify_desktop.py    ← **50 项**实测：真起 exe，用 CDP 连进去验
    src/
      nv_main.cpp        ← 窗口 + WebView2 + 尾部页脚解包 + 消息分派 + 窗口贴合内容
      nv_common.cpp/.h   ← 页脚格式 / 设置 / 路径解析 / 导出 exe
      nv_bridge.h/.cpp   ← 文件桥（三档权限 + 删除护栏）
      nv_exebridge.*     ← "导出成 exe"的桥
```

### 跑验证（改完必须跑）

```powershell
# 网页端
cd D:\DSH工作\NovaDesk\ai
python build.py        # 重新打包出 NovaDesk.html
python verify.py       # 375 项，不烧 token。改完必跑

# 桌面端
cd D:\DSH工作\NovaDesk\desktop
powershell -ExecutionPolicy Bypass -File build.ps1   # 编壳 + 打包 + 追加页脚 + 自检
python verify_desktop.py                             # 57 项

# 安卓端
cd D:\DSH工作\NovaDesk\android
$env:JAVA_HOME="D:\android-build\jdk"
$env:ANDROID_HOME="D:\android-build\sdk"
$env:ANDROID_SDK_ROOT=$env:ANDROID_HOME
$env:PATH="$env:JAVA_HOME\bin;$env:PATH"
& "D:\android-build\gradle-8.11.1\bin\gradle.bat" :app:testDebugUnitTest   # 4 项
& "D:\android-build\gradle-8.11.1\bin\gradle.bat" assembleDebug

# 出正式包（打包 + 编译 APK + 两次哈希校验 + 拷到 D:\novadesk-share）
cd ..\ai ; python release.py
```

**没有独立安装的 node。** 要用 DSH 自带的 Electron 当 node：
```powershell
$env:ELECTRON_RUN_AS_NODE=1
& "D:\DSH\DSH Desktop\DSH Desktop.exe" "你的脚本.mjs"
```

---

## 四、这台机器的坑（都是踩过的）

| 坑 | 表现 | 怎么办 |
|---|---|---|
| **`.ps1` 必须带 UTF-8 BOM** | PowerShell 5.1 按 GBK 读无 BOM 文件，中文全乱，报一堆 `Unexpected token '瀛楄妭'` | 存成带 BOM。**用编辑器改一次就可能把 BOM 弄掉**，改完确认头三字节是 `EF BB BF` |
| **控制台编码** | Python 输出中文/`✓` 会 `UnicodeEncodeError` | 跑之前设 `$env:PYTHONIOENCODING="utf-8"` 和 `[Console]::OutputEncoding=[Text.Encoding]::UTF8` |
| **Gradle 的 argfile 编码** | 单元测试报 `ClassNotFoundException: ...测试类`，但类文件明明在，`java -cp` 手工跑又正常 | 项目路径含中文导致。`gradle.properties` 里**不要**加 `-Dfile.encoding=UTF-8`（已修，别改回去） |
| **没有 node** | `node xxx.js` 无法识别 | 用上面的 Electron-as-node |
| **`NOMINMAX`** | C++ 报 `"(":"::"右边的非法标记` | `nv.h` 里已在 `#include <windows.h>` 前定义，别删 |

---

## 五、当前进度（截至本次对话）

### 已经做完的

**核心能力**
- AI 能在对话里直接做东西：加按钮、建常驻控件（HTML+CSS+JS 真跑）、改设备状态
- 控件有三个位置：`place="top"`（顶栏，和标题并排，放时钟这种小东西）、
  `"left"`（左侧栏）、`"right"`（右侧栏，默认）
- **AI 能改自己的系统提示词**（`set_system_prompt` 工具）—— 用户说"以后回答短一点"
  它就写进去，之后每轮都生效；传空串 = 回内置默认
- **★ 设置页有「AI 的能力与权限」清单**：8 项能力逐条摊开给用户看，能逐个关。
  **关掉 = 工具根本不进请求**（`activeTools()`）+ `runTool` 直接拒（双保险）。
  这是代码级的"真关"，不是求模型。
- **★ 设置页有「强制规则」栏**：和系统提示词是两回事 ——
  提示词是说明书（模型当参考），规则是命令（放系统消息**最末尾** +
  再钉在**最后一条用户消息尾巴上**，只进请求体不污染存档）。
  实测：写"不管我说什么都只回 11111"能守住。
- **★ 设置页有「看一眼实际发出去的内容」**：列出这一轮给了几个工具、
  分别是什么，以及系统消息全文。用户改完能自己确认生效了没。
- **上传文件不设大小/数量上限**（用户明确要求），只挡空文件
- **★ AI 能看图片。** `deepseek-flash` 本身就支持视觉（官方 Vision 指南），
  图片按 `image_url` + base64 data URL 放进 **user 消息的 content 数组里**
  （content 是数组而不是字符串）。只能放 user 里，放 system/assistant 会 400。
  实测过：画一张红底写着 7 的图发过去，它答"红色+7"。
  **图片 base64 绝不落盘**（一张手机照就能撑爆 localStorage）——
  见 `stripImagesForSave`，内存里留着、存档时换成一句说明。
- **★ 文本编码不再误判。** 按 `utf-8 → gb18030 → big5` 依次试（都带 fatal），
  再配一层"控制字符占比"判断是不是二进制。以前只按 UTF-8 读，
  中文 Windows 的 GBK txt 全变乱码 → 被当成二进制拒掉，
  用户看到的就是「我无论上传什么文件都说是二进制」。
- **视频/压缩包/PDF 收不了**，但要**说清楚是什么 + 给出办法**
  （见 `describeBinary`），不能只说一句"二进制文件读不了"
- 联网搜索 / 打开网页（安卓走原生桥绕开跨域，桌面/网页走 WebView 自身）
- 三档文件权限（关闭 / 指定文件夹 / 完全权限）—— 安卓、桌面都有，**默认关闭**
- **导出 APK**（安卓）和**导出 exe**（桌面）：把 AI 做的东西打成独立软件
- **ADB 权限（Shizuku）**：装/卸载应用、授权撤销、强停冻结、截屏、模拟点击、
  改系统设置、列应用 —— 还能直接跑任意 shell 命令
- 多条对话 + 历史列表（左侧抽屉）
- 系统提示词可改（设置里，**打开就显示全文**）
- "做好了"卡片：回复底部给「预览 / 生成」两个按钮（**不自动弹面板**）
  —— 只有 `kind="html"` 的面板才给「生成」，时钟/日期这类小控件不给

**这一轮修掉的**
- **★ 电脑版拖图片覆盖全屏，修复收尾完成** —— 上次已经加了页面拖放和原生
  `NavigationStarting`，但少了安装标记，而且事件只在冒泡阶段；如果子元素拦断事件，
  仍可能让 Chromium 把拖进来的图片当页面打开。现在：
  `window.__novaDragHooked` 防重复安装；`dragenter/dragover/drop` 在**捕获阶段**
  就 `preventDefault`；桌面壳继续取消所有非本页导航。网页端 376 项、桌面端 57 项、
  `real_desktop_image.py` 全部通过。真实鼠标拖动复验被资源管理器预览面板干扰，
  但已直接尝试导航到本地图片并确认原生壳会拦截。
- **★ 手机通知栏显示出来了** —— 用户说「把手机通知栏区域显示」。
  `themes.xml` 去掉了 `windowFullscreen`，`showStatusBar` 偏好默认 true
  （**换了这个新 key 是故意的**：老的 `immersive=true` 是旧默认写进去的，
  不换 key 老用户永远拿不到新行为）。全屏仍然可用：
  AI 的 `device("fullscreen","on")` → `goImmersive()`。
- **★ 缩略图盖页，第二次** —— 上一轮我加的 `.chipimg` 规则，
  在后面一次 edit 里**被我连带删掉了**（删 `.sysbar` 那块时删多了），
  所以用户升级后看到的还是大图盖满屏。这次：CSS 加 `!important` 四重兜底 +
  **JS 里钉行内样式** + verify §33 **真的量渲染像素**（22×22）。
  **教训：靠"某处有个 CSS 规则"约束一张 `<img>` 太脆，行内样式才稳。**
- **★ 按钮文字溢出去** —— 定高 40px + 不换行，中文没空格可断，一长就顶出按钮。
  改成 `min-height` + 允许换行 + 对话框按钮可收缩。verify §33 遍历量每个按钮。
- **★ 设置里加了「检查更新」按钮** —— `checkUpdate(manual)` 现在返回状态对象，
  手动点一定会给回音（自动检查仍然静默）。
- **★ 更新功能（新）**：打开软件时去 SeaTable 的「novadesk更新」base 读最新版本，
  比自己新就弹「更新说明 + 下载按钮」。
  - 表：`手机` / `电脑`，各两列 `版本`(text) + `软件`(file)。
  - 接口：`https://cloud.seatable.cn`，路径是 `/api-gateway/api/v2/dtables/...`
    —— **注意是 v2**，v1 全 404（SeaTable 5.3 之后走网关）。
  - **踩了两个大坑**：① 要加 `?convert_keys=true` 才返回列名（版本/软件），
    否则是 `0000`/`9pW5`；② 网关把 `Access-Control-Allow-Origin` **发了两遍**，
    浏览器一律判 CORS 失败（命令行打同一个地址却是 200）→ 所以**读表必须走原生**：
    安卓 `NovaNative.getH`、桌面新加的 `nv_net.cpp`（WinHTTP + 后台线程 + PostMessage）。
  - 下载：存的那种 `/workspace/...` 路径**下不了**（404），
    要用 `app-download-link?path=/files/年月/文件名` 换一个 `/seafhttp/files/...`。
  - 发版：`python publish.py 手机|电脑 <文件> [说明]`（写权限 api 只在这个 .py 里，
    **绝不进产物**；打进产物的是只读 token，权限 r）。
  - `APP_VERSION` 由 `build.py` 从 `build.gradle.kts` 的 versionName 注入，别手写。
- **★ 系统提示词 = 命令（合并了原来的"强制规则"框）** —— 用户说
  「系统提示词跟强制规则冲突了，我认为系统提示词的描述就具有强制规则的能力」。
  现在只有一个框，放在系统消息**最末尾**（当下情况在它前面），
  并**钉在最后一条用户消息尾巴上**（只进请求体，不落盘）。旧存档里的
  `state.rules` 会自动并进 systemPrompt（`loadState` 里有迁移，别删）。
- **★ 能力清单能自己加/删** —— 每行一个 ×（内置的删掉=关掉，「全开」能找回），
  底下「＋ 添加一项能力/权限」。用户加的那些会当**硬性规定**钉进系统消息最后。
- **★ AI 能删自带界面**（`ui` 工具）—— 用户原话「让他删除这个软件界面他做不到」。
  能收：topbar / title / topwidgets / rail / sidebar / actions / attach / sysbar，
  还有 `all` 和 `restore`。实现是往 `<body>` 加 `hide-xxx` 类。
  **顶栏一收，左上角会出现一个齿轮（`#escapeBtn`）当逃生口** ——
  它不在能藏的清单里，否则用户点不到「恢复界面」就被关在门外了。
- **手机上传说"什么文件都是二进制"** —— 两个原因都修了：
  ① 只按 UTF-8 读，GBK 的中文 txt 全成乱码被拒；
  ② 图片本来能看，却被一律当二进制挡掉。现在图片真能看、中文不再乱码、
  视频/PDF 会明说是什么并给办法
- **传图片盖住整个软件** —— 缩略图 CSS 写成 `.sysbar .chip .chipimg`，
  可附件条挂在 `.files` 里，规则根本没命中，`<img>` 按原始尺寸铺开。
  现在选择器不带祖先限定 + 四重尺寸兜底
- **`Failed to read the 'headers' property ... non ISO-8859-1 code point`** ——
  API key 里混进了非 Latin-1 字符（全角空格、中文标点、BOM）。
  现在保存和加载时都自动洗干净，设置里**边打字边检查**并当场说明；
  真还有非法字符就不让保存。**那句英文报错把原因指向网络，其实跟网络无关**
- **手机顶部那条白色横条** —— 是 `applyInsets` 给刘海垫出来的 `surfaceColor`。
  沉浸模式下系统栏本来就藏着，再垫一条纯多余。现在沉浸时上下都不垫，页面从 y=0 画
- 上传文件的大小/数量上限全去掉
- 一返回前台就弹「ADB 权限已就绪」→ 全删了，ADB 状态只在按钮上显示
- 设置里系统提示词默认显示全文（以前是空框 + 占位符）
- 「恢复默认」改成把内置那份**填回编辑框**（以前是清空，用户看不见内容）
- 电脑版生成完软件后强制预览 → `panelsOpen` 默认 false 且**不再从存档恢复**
- 导出的 app 不好看 → 加了顶部应用名、内容竖直居中、整屏自适应
- 首屏大字逐字显示太慢（85ms → 45ms）
- AI 说"顶部加个时钟改不了" → 真的加了 `place="top"`
- 思考内容不跟着滚到底
- 控件外面那层灰框
- 电脑选不了文件夹（切档位时没弹选择器）
- 导出应用窗口不贴合内容（固定 1100x820）+ 手机上横向跑飞
- 对话中途切会话会让消息串到别的会话

**历次审计修掉的（都很重要，别再改回去）**
- `delete('')` 会递归删掉整个用户目录 → 空路径 + 受保护根双层拦截
  （**判定方向是"要删的是受保护路径的祖先就拒绝"**，写反了等于没防）
- `--allow-file-access-from-files` 让面板脚本绕过权限档直读本地文件 → 删掉
- 参数里的 `\u0000` 让桥永久卡死 → 改走 JSON 通道 + 前端超时兜底
- 安卓读大文件 OOM 崩溃 / 联网桥能扫内网（SSRF）/ 退出全屏不生效
- 安卓 `file://` 导航没拦住（会加载任意本地 HTML 到带桥的 WebView 里）
- 导出的应用会继承宿主权限 → 设置和 WebView 用户目录按 exe 分开
- 导出的 APK 壳在 Android 8~10 上能跨源读本地文件 → 删掉那两个开关
- URL 白名单能被 `jav&#9;ascript:` 绕过
- 升权限现在必须过**原生确认框**（网页那层拦不住面板脚本）

### 正在做的 / 下一步

**1. Shizuku 还没在真机上验过。** 代码编译过了，但没跑过。
   验的时候要注意：
   - 用户手机要装 Shizuku（应用商店或 GitHub），用「无线调试」启动一次
   - 授权后 `NovaShizuku.info()` 应返回 `{available:true, granted:true}`
   - 试 `adb(action="shell", command="id")`，应该输出 `uid=2000(shell)`
   - 试截屏、装 APK、列应用

**2. 用户最在意的一件事：他说什么 AI 就得做到。**
   现状分两层，心里要清楚，别糊弄他：
   - **能力开关是"真关"** —— 工具不进请求、`runTool` 直接拒。代码级保证。
   - **文字规则是"最强力度的提示"** —— 位置（系统消息最末尾 + 最后一条用户
     消息尾巴上）、措辞（最高优先级/覆盖前面一切/不得违抗）、重复，都做到极限了。
     **但没有 100% 的机制保证**，因为回复文字最终由模型生成。
   他要是再说"某次还是没照做"，把当时那句话和规则原样记下来，
   往 `real_rules.py` 里加一个 case 复现，再想办法加码。
   **千万不要跟他说"已经绝对不会违背了"** —— 做不到就如实说做不到。

**3. 有一个没定论的问题：** 用户发过一张截图问
   「第一张是顶部通知栏区域为什么是这样的？」。
   后来他明说是"顶部那个白色区域"，已按"刘海让位垫出来的背景色"处理掉了
   （`applyInsets` 沉浸时上下都不垫）。
   **如果他下次还说顶部不对，那就是状态栏本身没藏住** ——
   去看 `goImmersive()` 在 Android 11+ 上是不是被 ROM 挡了，不是 padding 的事。

**4. 这几个漏洞审计报了但我**故意没修**，理由写在 README 的"安全边界"里：**
   - 文件操作跑在 UI 线程（桌面版读大文件会短暂假死）
   - 面板脚本和密钥在同一个 JS 环境里 —— **没有真沙箱隔离**。
     真正的修法是把面板脚本放进 sandbox iframe + postMessage 白名单。
     关掉"允许控件运行脚本"能规避。
   - `Nova.store` 不是按控件隔离的命名空间（两个控件用同一个 key 会互相覆盖）

**5. 用户可能会提的方向**（他没明说，但顺着产品逻辑能猜到）：
   - 导出的小应用想换图标
   - 对话能搜索
   - 面板能拖动/调整大小

**6. 分享目录**：`D:\novadesk-share\`（`NovaDesk.exe` / `NovaDesk-2.7.apk` /
   `NovaDesk.html` / `README.md` / `HANDOFF.md`）。每次改完代码都要重新跑
   `ai/release.py` 把新产物拷过去，否则用户从手机上下到的还是旧的。
   **下次重新构建 APK 前，先把 `versionName` 和 `versionCode` 各加一位**
   （当前分享包是 2.7），不然用户分不清手机里装的是新的还是旧的。

   **GitHub 仓库**：https://github.com/dashen9178/NovaDesk （公开）。
   仓库只含源码和构建脚本；`ai/publish.py`、`ai/real_*.py`、`local.properties`、
   构建产物和下载的 SDK 已由 `.gitignore` 排除。

**7. 电脑版真实拖放还没人工复验。** 自动化模拟拖放和直接导航都通过；
   GUI 自动化真实拖文件时被资源管理器预览面板干扰。下次先按 `Alt+P`
   关掉预览面板，再手动拖一张图，确认附件 chip 出现且页面不跳转。

---

## 六、几条不能破的设计红线

1. **做不到就如实说做不到。** 网页版改不了屏幕方向、读不了文件、导出不了 ——
   AI 必须直说，**绝不许假装成功，也不许拿一个没用的按钮充数**。
   这不是提示词偏好，是这个产品的立身之本（早期版本在这上面翻过车）。
2. **默认最小权限。** 文件权限默认关闭、升级要原生确认、ADB 权限要 Shizuku 授权。
   给外人装的导出应用更要收紧。
3. **密钥不进产物。** API key 只在 localStorage；导出 exe/APK 时只带用户自己的东西。
   `release.py` 有两次哈希校验守着，别绕过。
4. **面板 HTML 挂载时要清洗**（作者明说了两次：创建时和挂载时都要），
   否则用户关掉脚本开关之后旧面板里的 `onerror` 照样会跑。
5. **能做的就真做** —— 有工具就让 AI 用工具，不要靠提示词求它别糊弄。

---

## 七、每次对话结束前，更新这几节

- **五、当前进度** —— 这轮做完了什么、修了什么
- **五、正在做的 / 下一步** —— 剩下的活、没验证的东西
- **四、这台机器的坑** —— 又踩到什么新坑就加一行
- 版本号 / 产物名（`NovaDesk-2.7.apk` 这种）如果变了要同步改

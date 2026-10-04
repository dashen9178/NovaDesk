# NovaDesk / ai —— 源码与工具链

面向用户和功能的说明在交付目录那份 README 里，这里只讲怎么改、怎么验。

- 用户手册 / 完整说明：`D:\novadesk-share\README.md`
- 交付物：`D:\DSH工作\NovaDesk\NovaDesk.html`（单文件，双击即用）
- 安卓版：`D:\novadesk-share\NovaDesk-1.9.apk`

## 文件

| 文件 | 干什么 |
|---|---|
| `index.html` | 页面骨架 + **全部 CSS**（M3 设计令牌都在这） |
| `core.js` | 状态与持久化、DeepSeek 客户端、工具定义与执行、联网层 |
| `ui.js` | **系统提示词**、Markdown 渲染、界面渲染、流式与工具调用循环、启动 |
| `build.py` | 把 `core.js` + `ui.js` 内联进 `index.html`，产出 `NovaDesk.html` |
| `release.py` | 打包 → 同步 android assets → 编译 APK → 三重哈希校验 → 拷到分享目录 |
| `verify.py` | 218 项浏览器实测断言（伪造接口，不烧 token） |
| `verify_web_lib.py` | 无头 Edge 的 CDP 客户端（`start_edge` / `wait_debug` / `evaluate`） |
| `real_test.py` | 一次真实接口连通性测试 |
| `real_device.py` | 真实模型：听见"改成横屏"到底改不改（而不是加按钮） |
| `real_create.py` | 真实模型：做的控件是不是活的、会不会乱塞控件、日期会不会瞎猜 |
| `audit.py` | 静态安全审计（扫危险模式） |

## 改代码的固定流程

```powershell
cd "D:\DSH工作\NovaDesk\ai"
python build.py        # 只是打包，快
python verify.py       # 必须过 —— 出错时它会指出是哪一组
python release.py      # 要发 APK 时才跑（会编 gradle，慢）
```

**只改 `index.html` / `core.js` / `ui.js`，永远不要手改 `NovaDesk.html`** ——
那是打包产物，下次 `build.py` 就覆盖了。同理，
`android/app/src/main/assets/NovaDesk.html` 由 `release.py` 自动同步，也别手改。

## 打包顺序有讲究

`build.py` 里的 `ORDER = ["core.js", "ui.js"]` ——
core 定义 `state` / `TOOLS` / `runTool`，ui 依赖它们。
两个模块被内联进**同一个作用域**，所以 ui 里的函数声明对 core 是可见的
（`runTool('device')` 就是直接调 ui 里的 `runLocalAction`）。

## 几个容易踩的点

- **工具没定义的参数，模型编不出来。** 要加能力就加参数或加工具，
  不要靠改提示词求模型绕过去。
- **`verify.py` 第 0 组是保命的。** 整段 bundle 只要有一个语法错误，
  一行都不会执行，但页面静态 HTML 看起来完全正常。
- **`buildApiMessages()` 必须在插入占位 assistant 消息之前调用**，
  否则思考模式会因为"空的 assistant 轮次"报 400。
- 改完 `versionName` 记得在 `android/app/build.gradle.kts` 里同步
  `versionCode`，否则手机上「应用信息」看到的版本号不会变。

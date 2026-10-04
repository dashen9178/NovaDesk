# NovaDesk

一个横屏的电脑式操作系统，跑在安卓手机的一个 App 里（沙箱内，不刷机）。

安卓在这里被降级为**硬件抽象层**：它只提供一块屏幕、触摸事件、一个时钟、
一个私有目录。窗口、文字、文件系统、应用——全部由 NovaDesk 自己实现。

## 架构

```
┌─────────────────────────────────────────────┐
│ 安卓 (HAL + 引导器)                          │
│  ┌───────────────────────────────────────┐  │
│  │ nova/host/  ← 唯一的安卓代码            │  │
│  │   NovaActivity  开机、全屏、主循环      │  │
│  │   NovaView      Surface ← 像素数组      │  │
│  │   AndroidHost   文件/时钟/事件队列       │  │
│  └────────────────┬──────────────────────┘  │
│                   │ ABI：8 个系统调用         │
│  ┌────────────────▼──────────────────────┐  │
│  │ nova/kernel/ + nova/apps/  ← 我们的系统 │  │
│  │   Kernel  调度/输入分发/合成/任务栏      │  │
│  │   Canvas  软件光栅化器                  │  │
│  │   Font    5x7 点阵字库                  │  │
│  │   Window  窗口几何与命中测试             │  │
│  │   Vfs     内存文件系统 + 沙箱持久化      │  │
│  │   apps/   终端 文件 关于 时钟 笔记 画板  │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**边界规则**：`nova/kernel/` 和 `nova/apps/` 里不允许出现任何 `android.*`。
这条线就是"这是不是一个操作系统"的分水岭。可以在 PC 上运行 `:tools:run`
来验证这条边界——同一份内核源码，脱离安卓直接跑在 JVM 上。

## ABI（`nova/abi/NovaHost.kt`）

宿主只实现这 8 个原语，永远不增长：

```
present(argb)                 把一帧像素贴到屏幕上
clockMs()                     单调时钟
pollEvent()                   取出一个输入事件
open/read/write/close/list    沙箱私有目录内的文件访问
exit(code)                    退出
```

关键点：`present()` 收到的是**已经画好的一整屏**。安卓不知道什么是"窗口"。

## 构建

工具链：JDK 17 + Gradle 8.11.1 + Android SDK 34（在 `D:\android-build`）。

```powershell
$env:JAVA_HOME='D:\android-build\jdk'
$env:ANDROID_HOME='D:\android-build\sdk'
$env:GRADLE_USER_HOME='D:\android-build\gradle-home'
$env:Path="$env:JAVA_HOME\bin;$env:Path"

# 出 APK
gradle -p D:\DSH工作\NovaDesk :app:assembleDebug

# 在 PC 上跑内核（不需要手机）
gradle -p D:\DSH工作\NovaDesk :tools:run

# 检查渲染出来的画面
gradle -p D:\DSH工作\NovaDesk :tools:inspect
```

产物：`app/build/outputs/apk/debug/app-debug.apk`

## 内核验证台（`tools/`）

因为内核零安卓依赖，它可以直接在 PC 上跑。`tools/KernelVerify.kt` 模拟一次
完整开机与交互，把每一帧导出成 PNG 到 `_verify/`，并做像素级断言。

这不是"跑起来看看"，而是可复现的回归测试。

## 已实现

- 软件光栅化：矩形、圆角矩形、直线(Bresenham)、alpha 混合、位块传输
- 5x7 点阵字库，ASCII 全集，整数倍缩放
- 窗口管理器：拖动、右下角缩放、最大化、关闭、最小化、Z 序、焦点
- 合成器：桌面渐变网格、窗口阴影/边框、任务栏、开始菜单、触摸指示
- 输入分发：任务栏 / 开始菜单 / 桌面图标 / 窗口标题 / 按钮 / 内容区
- 内存 VFS，可经 ABI 持久化到安卓沙箱
- 6 个内置应用：终端(shell)、文件管理器、关于本机、时钟、记事本、画板

## 未实现（明确记录）

- 多任务抢占调度（当前是单线程协作式）
- 中文显示与输入法（字库只有 ASCII）
- 网络
- 硬件加速（纯软件渲染）
- 自有可执行文件格式（应用目前编译进内核）

## 后续可做

1. 接入 Android 输入法，实现中文候选词
2. 把应用抽成可加载模块，定义 NovaDesk 的可执行格式
3. 真正的进程模型与抢占式调度
4. 把 `tools/` 的宿主换成浏览器 Canvas，同一内核跑在网页里

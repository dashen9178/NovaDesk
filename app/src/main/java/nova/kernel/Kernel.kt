package nova.kernel

import nova.abi.NovaEvent
import nova.abi.NovaHost
import nova.abi.NovaKey

/**
 * NovaDesk 内核。
 *
 * 职责：
 *   1. 合成：把所有窗口画到一块全屏画布上（桌面 → 窗口 → 任务栏 → 光标）
 *   2. 输入分发：触摸坐标 → 命中哪个窗口 → 标题拖动 / 按钮 / 内容
 *   3. 生命周期：创建、聚焦、关闭窗口
 *   4. 服务：时钟、文件系统、启动菜单
 *
 * 这个类只依赖 nova.abi 和 nova.kernel，不允许出现 android.*。
 */
class Kernel(private val host: NovaHost) {

    val vfs = Vfs(host)

    private var screen = Canvas(host.width, host.height)

    /** 桌面图标命中区，每帧重建 */
    private val desktopIconHit = ArrayList<IntArray>()

    /** 窗口栈，末尾是最上层（也是焦点窗口） */
    val windows = ArrayList<Window>()

    /** 可启动的应用工厂 */
    private val factories = LinkedHashMap<String, () -> App>()

    // 拖动状态
    private var dragWin: Window? = null
    private var dragOffX = 0
    private var dragOffY = 0
    private var dragging = false

    // 缩放状态
    private var resizeWin: Window? = null
    private var resizeStartX = 0
    private var resizeStartY = 0
    private var resizeStartW = 0
    private var resizeStartH = 0

    // 启动菜单
    var startMenuOpen = false
        private set

    // 光标（触摸位置指示）
    private var pointerX = -1
    private var pointerY = -1
    private var pointerVisible = false

    private var lastFrameMs = 0L
    private var needsRedraw = true
    private var uptimeMs = 0L

    /** 已按下的触摸目标，用于把 Move/Up 派发给同一个窗口 */
    private var touchWin: Window? = null

    // ---------------- 初始化 ----------------

    init {
        registerBuiltinApps()
        // 开机自动打开终端，让用户第一眼就看到系统活着
        launch("terminal")
        launch("files")
        val w0 = windows[0]
        moveTo(w0, 60, 60)
        val w1 = windows[1]
        moveTo(w1, 300, 160)
        // 终端是主界面：键盘输入必须落到它身上，
        // 所以让文件管理器先起来、终端后置顶。
        focus(w0)
    }

    private fun registerBuiltinApps() {
        factories["terminal"] = { nova.apps.TerminalApp() }
        factories["files"] = { nova.apps.FilesApp() }
        factories["about"] = { nova.apps.AboutApp() }
        factories["clock"] = { nova.apps.ClockApp() }
        factories["notes"] = { nova.apps.NotesApp() }
        factories["paint"] = { nova.apps.PaintApp() }
    }

    fun appNames(): List<String> = factories.keys.toList()

    // ---------------- 窗口管理 ----------------

    fun launch(name: String): Window? {
        val f = factories[name] ?: return null
        val app = f()
        val size = app.defaultSize()
        // 级联摆放，避免完全重叠
        val off = (windows.size % 6) * 32
        val ww = size[0].coerceAtMost(screen.w - 40)
        val wh = size[1].coerceAtMost(screen.h - Theme.TASKBAR_H - 20)
        val wx = (40 + off).coerceAtMost(maxOf(0, screen.w - ww - 10))
        val wy = (30 + off).coerceAtMost(maxOf(0, screen.h - Theme.TASKBAR_H - wh - 10))
        val win = Window(app.name, wx, wy, ww, wh, app)
        app.win = win
        app.sys = this
        windows.add(win)
        win.resizeSurface()
        focus(win)
        needsRedraw = true
        return win
    }

    fun close(win: Window) {
        win.app.onClose()
        windows.remove(win)
        if (windows.isNotEmpty()) focus(windows.last())
        needsRedraw = true
    }

    fun focus(win: Window) {
        if (windows.remove(win)) windows.add(win)
        needsRedraw = true
    }

    fun moveTo(win: Window, nx: Int, ny: Int) {
        win.x = nx
        win.y = ny
        needsRedraw = true
    }

    private fun clampWindow(win: Window) {
        // 保证窗口始终有一块可抓取的区域留在屏幕内。
        // 之前只保证 80px，导致窗口能被拖到几乎完全出界（内容看不见也抓不回来）。
        val keep = minOf(120, maxOf(80, win.w * 2 / 5))
        val maxX = screen.w - keep
        val maxY = screen.h - Theme.TASKBAR_H - Theme.TITLE_H
        if (win.x > maxX) win.x = maxX
        if (win.y > maxY) win.y = maxY
        if (win.x < -(win.w - keep)) win.x = -(win.w - keep)
        if (win.y < 0) win.y = 0
    }

    // ---------------- 事件 ----------------

    fun handleEvent(e: NovaEvent) {
        when (e) {
            is NovaEvent.Resize -> {
                // 屏幕尺寸变化：重建画布并夹紧所有窗口
                screen = Canvas(e.w, e.h)
                for (w in windows) {
                    if (w.x + w.w > e.w) w.x = maxOf(0, e.w - w.w)
                    if (w.y + w.h > e.h - Theme.TASKBAR_H) w.y = maxOf(0, e.h - Theme.TASKBAR_H - w.h)
                }
                needsRedraw = true
            }
            is NovaEvent.Key -> handleKey(e)
            is NovaEvent.Text -> sendChar(e.ch)
            is NovaEvent.Down -> handleDown(e.x, e.y)
            is NovaEvent.Move -> handleMove(e.x, e.y)
            is NovaEvent.Up -> handleUp(e.x, e.y)
        }
    }

    private fun handleKey(e: NovaEvent.Key) {
        if (!e.down) return
        if (e.code == NovaKey.BACK) {
            if (startMenuOpen) { startMenuOpen = false; needsRedraw = true; return }
            val top = windows.lastOrNull()
            if (top != null) close(top)
            return
        }
        // 焦点窗口优先
        val top = windows.lastOrNull() ?: return
        if (top.app.onKey(e.code)) { needsRedraw = true; return }
        when (e.code) {
            NovaKey.ESC -> if (!startMenuOpen) { launch("terminal") }
            NovaKey.TAB -> if (windows.size > 1) {
                val cur = windows.removeAt(windows.size - 1)
                windows.add(0, cur)
                focus(cur)
            }
        }
        needsRedraw = true
    }

    fun sendChar(ch: Char) {
        val top = windows.lastOrNull() ?: return
        if (top.app.onChar(ch)) needsRedraw = true
    }

    private fun handleDown(px: Int, py: Int) {
        pointerX = px; pointerY = py; pointerVisible = true
        needsRedraw = true

        // 1. 任务栏最高优先级
        if (py >= screen.h - Theme.TASKBAR_H) {
            handleTaskbar(px, py)
            return
        }

        // 2. 启动菜单覆盖层
        if (startMenuOpen) {
            if (handleStartMenuClick(px, py)) return
            startMenuOpen = false
            // 落在菜单外则吞掉这次点击
            return
        }

        // 3.5 桌面图标
        val names = appNames()
        for (r in desktopIconHit) {
            if (px >= r[0] && px < r[0] + r[2] && py >= r[1] && py < r[1] + r[3]) {
                if (r[4] < names.size) {
                    val w = launch(names[r[4]])
                    // 双击语义：直接聚焦并置顶
                    if (w != null) focus(w)
                }
                return
            }
        }

        // 4. 从上到下找命中的窗口
        for (i in windows.indices.reversed()) {
            val w = windows[i]
            if (!w.visible || !w.contains(px, py)) continue
            focus(w)

            if (w.hitClose(px, py)) { close(w); return }
            if (w.hitMin(px, py)) { w.visible = false; needsRedraw = true; return }
            if (w.hitMax(px, py)) { toggleMaximize(w); return }

            if (w.hitTitle(px, py)) {
                // 标题栏空白处 → 拖动
                dragWin = w
                dragging = false
                dragOffX = px - w.x
                dragOffY = py - w.y
                return
            }

            // 右下角缩放热区
            if (px >= w.x + w.w - 22 && py >= w.y + w.h - 22) {
                resizeWin = w
                resizeStartX = px; resizeStartY = py
                resizeStartW = w.w; resizeStartH = w.h
                return
            }

            // 内容区
            val local = w.toLocal(px, py)
            if (local != null) {
                touchWin = w
                w.app.onTouch(local[0], local[1], App.PHASE_DOWN)
                needsRedraw = true
            }
            return
        }
    }

    private fun handleMove(px: Int, py: Int) {
        pointerX = px; pointerY = py; pointerVisible = true

        dragWin?.let { w ->
            val nx = px - dragOffX
            val ny = py - dragOffY
            if (kotlin.math.abs(nx - w.x) > 1 || kotlin.math.abs(ny - w.y) > 1) dragging = true
            w.x = nx; w.y = ny
            clampWindow(w)
            needsRedraw = true
            return
        }

        resizeWin?.let { w ->
            val nw = (resizeStartW + (px - resizeStartX)).coerceAtLeast(Theme.MIN_W)
            val nh = (resizeStartH + (py - resizeStartY)).coerceAtLeast(Theme.MIN_H)
            w.w = nw; w.h = nh
            w.resizeSurface()
            needsRedraw = true
            return
        }

        touchWin?.let { w ->
            val local = w.toLocal(px, py)
            if (local != null) {
                w.app.onTouch(local[0], local[1], App.PHASE_MOVE)
                needsRedraw = true
            }
            return
        }
        needsRedraw = true
    }

    private fun handleUp(px: Int, py: Int) {
        if (dragging) needsRedraw = true
        dragWin = null
        dragging = false
        resizeWin = null
        touchWin?.let { w ->
            val local = w.toLocal(px, py)
            if (local != null) w.app.onTouch(local[0], local[1], App.PHASE_UP)
            needsRedraw = true
        }
        touchWin = null
    }

    private fun toggleMaximize(w: Window) {
        if (w.maximized) {
            w.x = w.restoreX; w.y = w.restoreY; w.w = w.restoreW; w.h = w.restoreH
            w.maximized = false
        } else {
            w.restoreX = w.x; w.restoreY = w.y; w.restoreW = w.w; w.restoreH = w.h
            w.x = 0; w.y = 0
            w.w = screen.w
            w.h = screen.h - Theme.TASKBAR_H
            w.maximized = true
        }
        w.resizeSurface()
        needsRedraw = true
    }

    // ---------------- 任务栏 ----------------

    private fun taskbarItemRects(): List<Pair<Window, IntArray>> {
        val out = ArrayList<Pair<Window, IntArray>>()
        var x = 52
        val y = screen.h - Theme.TASKBAR_H + 5
        val hh = Theme.TASKBAR_H - 10
        for (w in windows) {
            val ww = (80 + w.app.name.length * 7).coerceAtMost(150)
            out.add(w to intArrayOf(x, y, ww, hh))
            x += ww + 6
        }
        return out
    }

    private fun handleTaskbar(px: Int, py: Int) {
        // 开始按钮
        if (px >= 4 && px <= 46) {
            startMenuOpen = !startMenuOpen
            needsRedraw = true
            return
        }
        // 时钟区域
        if (px >= screen.w - 90) { needsRedraw = true; return }

        for ((w, r) in taskbarItemRects()) {
            if (px >= r[0] && px < r[0] + r[2] && py >= r[1] && py < r[1] + r[3]) {
                if (!w.visible) { w.visible = true; focus(w) }
                else if (windows.lastOrNull() === w) w.visible = false
                else focus(w)
                needsRedraw = true
                return
            }
        }
        needsRedraw = true
    }

    private fun startMenuItemRects(): List<Pair<String, IntArray>> {
        val names = appNames()
        val out = ArrayList<Pair<String, IntArray>>()
        val mw = 190
        val ih = 34
        val mx = 6
        val totalH = 30 + names.size * ih + 8
        val my = screen.h - Theme.TASKBAR_H - totalH
        for ((i, n) in names.withIndex()) {
            out.add(n to intArrayOf(mx + 4, my + 30 + i * ih, mw - 8, ih - 2))
        }
        return out
    }

    private fun handleStartMenuClick(px: Int, py: Int): Boolean {
        for ((name, r) in startMenuItemRects()) {
            if (px >= r[0] && px < r[0] + r[2] && py >= r[1] && py < r[1] + r[3]) {
                launch(name)
                startMenuOpen = false
                needsRedraw = true
                return true
            }
        }
        // 点击开始按钮本身
        if (px >= 4 && px <= 46 && py >= screen.h - Theme.TASKBAR_H) {
            startMenuOpen = false
            needsRedraw = true
            return true
        }
        return false
    }

    // ---------------- 主循环 ----------------

    /**
     * 处理所有待办事件 + 更新逻辑 + 需要时重绘。
     * 宿主每帧调用一次。
     */
    fun tick() {
        val now = host.clockMs()
        val dt = if (lastFrameMs == 0L) 16L else (now - lastFrameMs).coerceIn(0, 100)
        lastFrameMs = now
        uptimeMs += dt

        // 排空输入队列
        var guard = 0
        while (guard++ < 256) {
            val e = host.pollEvent() ?: break
            handleEvent(e)
        }

        // 更新应用
        for (w in windows) {
            if (w.visible && w.app.update(dt)) needsRedraw = true
        }
        if (startMenuOpen) needsRedraw = true

        if (needsRedraw) {
            compose()
            host.present(screen.px)
            needsRedraw = false
        }
    }

    // ---------------- 合成 ----------------

    private fun compose() {
        // 1. 先让每个应用把界面画进自己的窗口表面。
        //    这是内核的职责：应用只负责"画内容"，不管自己在屏幕哪。
        for (w in windows) {
            if (w.visible) {
                w.surface.clear(0x00000000)
                w.app.draw(w.surface)
            }
        }
        // 2. 再合成整屏
        drawDesktop()
        for (w in windows) if (w.visible) drawWindow(w)
        drawTaskbar()
        if (startMenuOpen) drawStartMenu()
        drawPointer()
    }

    private fun drawDesktop() {
        desktopIconHit.clear()
        // 纵向渐变
        for (y in 0 until screen.h) {
            val t = y.toFloat() / screen.h
            val r = lerp((Theme.DESK_TOP shr 16) and 0xFF, (Theme.DESK_BOTTOM shr 16) and 0xFF, t)
            val g = lerp((Theme.DESK_TOP shr 8) and 0xFF, (Theme.DESK_BOTTOM shr 8) and 0xFF, t)
            val b = lerp(Theme.DESK_TOP and 0xFF, Theme.DESK_BOTTOM and 0xFF, t)
            val c = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
            for (x in 0 until screen.w) screen.px[y * screen.w + x] = c
        }
        // 桌面上的隐形网格，给点"电脑"的感觉
        val grid = 0x14FFFFFF
        var gx = 0
        while (gx < screen.w) { for (y in 0 until screen.h - Theme.TASKBAR_H step 1) screen.blend(gx, y, grid); gx += 80 }
        var gy = 0
        while (gy < screen.h - Theme.TASKBAR_H) { for (x in 0 until screen.w step 1) screen.blend(x, gy, grid); gy += 80 }

        // 桌面图标（左上角一列）
        val icons = appNames()
        var iy = 24
        for ((i, name) in icons.withIndex()) {
            val ix = 24
            val sel = false
            screen.fillRoundRect(ix - 8, iy - 6, 76, 66, 8, if (sel) 0x40FFFFFF else 0x18FFFFFF)
            val app = factories[name]?.invoke()
            val glyph = app?.icon() ?: "?"
            screen.text(glyph, ix + 30 - screen.textWidth(glyph, 3) / 2, iy + 2, Theme.ACCENT, 3)
            screen.textCentered(name, ix + 30, iy + 34, Theme.TEXT_DIM, 1)
            desktopIconHit.add(intArrayOf(ix - 8, iy - 6, 76, 66, i))
            iy += 76
        }
    }

    private fun drawWindow(w: Window) {
        val focused = windows.lastOrNull() === w

        // 阴影
        screen.fillRoundRect(w.x + 4, w.y + 5, w.w, w.h, 6, Theme.WIN_SHADOW)
        // 主体
        screen.fillRoundRect(w.x, w.y, w.w, w.h, 6, Theme.WIN_BG)
        // 标题栏
        screen.fillRect(w.x + 1, w.y + 1, w.w - 2, Theme.TITLE_H - 1,
            if (focused) Theme.WIN_TITLE_ACTIVE else Theme.WIN_TITLE_INACTIVE)
        // 标题文字
        screen.text(w.title, w.x + 10, w.y + 9, if (focused) Theme.TEXT_TITLE else Theme.TEXT_DIM, 1)

        // 三个按钮
        dot(w.closeBtnCx(), w.closeBtnCy(), Theme.BTN_CLOSE)
        dot(w.minBtnCx(), w.minBtnCy(), Theme.BTN_MIN)
        dot(w.maxBtnCx(), w.maxBtnCy(), Theme.BTN_MAX)

        // 边框
        val bc = if (focused) Theme.WIN_BORDER_ACTIVE else Theme.WIN_BORDER_INACTIVE
        screen.strokeRect(w.x, w.y, w.w, w.h, bc)

        // 内容
        screen.blit(w.surface, 0, 0, w.surface.w, w.surface.h, w.contentX(), w.contentY())

        // 右下角缩放抓手
        val hx = w.x + w.w - 4
        val hy = w.y + w.h - 4
        for (i in 0 until 3) {
            screen.line(hx - i * 4, hy, hx, hy - i * 4, 0x60FFFFFF)
        }
    }

    private fun dot(cx: Int, cy: Int, color: Int) {
        for (dy in -4..4) for (dx in -4..4) {
            if (dx * dx + dy * dy <= 16) screen.blend(cx + dx, cy + dy, color)
        }
    }

    private fun drawTaskbar() {
        val ty = screen.h - Theme.TASKBAR_H
        screen.fillRect(0, ty, screen.w, Theme.TASKBAR_H, Theme.TASKBAR_BG)
        screen.fillRect(0, ty, screen.w, 1, 0x30FFFFFF)

        // 开始按钮
        val active = startMenuOpen
        screen.fillRoundRect(6, ty + 6, 40, Theme.TASKBAR_H - 12, 5,
            if (active) Theme.ACCENT else Theme.TASKBAR_ITEM)
        // 用四个小方块拼一个"窗口"图标
        val ic = if (active) 0xFFFFFFFF.toInt() else Theme.ACCENT
        screen.fillRect(15, ty + 13, 8, 6, ic)
        screen.fillRect(29, ty + 13, 8, 6, ic)
        screen.fillRect(15, ty + 22, 8, 6, ic)
        screen.fillRect(29, ty + 22, 8, 6, ic)

        // 窗口按钮
        for ((w, r) in taskbarItemRects()) {
            val isTop = windows.lastOrNull() === w
            screen.fillRoundRect(r[0], r[1], r[2], r[3], 4,
                if (isTop && w.visible) Theme.TASKBAR_ITEM_ACTIVE else Theme.TASKBAR_ITEM)
            if (!w.visible) {
                // 最小化的窗口文字变暗
                screen.text(w.app.name, r[0] + 8, r[1] + 10, Theme.TEXT_DIM, 1)
            } else {
                screen.text(w.app.name, r[0] + 8, r[1] + 10, Theme.TEXT, 1)
            }
            if (isTop && w.visible) screen.fillRect(r[0], r[1] + r[3] - 2, r[2], 2, Theme.ACCENT)
        }

        // 右侧时钟
        val secs = uptimeMs / 1000
        val hh = (secs / 3600) % 100
        val mm = (secs / 60) % 60
        val ss = secs % 60
        val clock = pad(hh) + ":" + pad(mm) + ":" + pad(ss)
        screen.text(clock, screen.w - 70, ty + 16, Theme.TEXT_DIM, 1)
    }

    private fun pad(v: Long): String = if (v < 10) "0$v" else "$v"

    private fun drawStartMenu() {
        val names = appNames()
        val mw = 190
        val ih = 34
        val totalH = 30 + names.size * ih + 8
        val mx = 6
        val my = screen.h - Theme.TASKBAR_H - totalH
        screen.fillRoundRect(mx + 3, my + 4, mw, totalH, 8, Theme.WIN_SHADOW)
        screen.fillRoundRect(mx, my, mw, totalH, 8, 0xF01E2228.toInt())
        screen.strokeRect(mx, my, mw, totalH, 0xFF4A90D9.toInt())
        screen.text("Applications", mx + 12, my + 10, Theme.ACCENT, 1)
        screen.fillRect(mx + 6, my + 24, mw - 12, 1, 0x30FFFFFF)
        for ((i, n) in names.withIndex()) {
            val r = intArrayOf(mx + 4, my + 30 + i * ih, mw - 8, ih - 2)
            screen.fillRoundRect(r[0], r[1], r[2], r[3], 4, 0x18FFFFFF)
            screen.text(n, r[0] + 12, r[1] + 10, Theme.TEXT, 1)
        }
    }

    private fun drawPointer() {
        if (!pointerVisible) return
        // 触摸点画一个小圆环，证明输入通了
        for (dy in -10..10) for (dx in -10..10) {
            val d2 = dx * dx + dy * dy
            if (d2 in 64..100) screen.blend(pointerX + dx, pointerY + dy, 0x80FFFFFF.toInt())
        }
    }

    private fun lerp(a: Int, b: Int, t: Float): Int = (a + (b - a) * t).toInt()

    /** 供应用查询屏幕信息 */
    fun screenW() = screen.w
    fun screenH() = screen.h
    fun uptimeSeconds() = uptimeMs / 1000
}

package nova.test

import java.io.File
import nova.abi.NovaEvent
import nova.abi.NovaHost
import nova.kernel.Kernel

/**
 * 内核验证台 —— 在 PC 上直接跑 NovaDesk 内核，不经过安卓。
 *
 * 这正是 ABI 架构的价值：内核零安卓依赖，所以可以脱离手机测试。
 * 本程序模拟一次完整的开机 + 交互，并把每一帧导出成 PNG 供肉眼检查。
 */

/** 一个纯内存宿主，实现那 8 个原语 */
class HeadlessHost(w: Int, h: Int) : NovaHost {
    override val width = w
    override val height = h
    private val queue = ArrayDeque<NovaEvent>()
    private var t = 0L
    val saved = ArrayList<String>()

    fun pushEvent(e: NovaEvent) { queue.addLast(e) }

    override fun present(argb: IntArray) {
        // 把这一帧存下来，用于对比和导出
        lastFrame = argb.copyOf()
        frameCount++
    }

    var lastFrame: IntArray? = null
    var frameCount = 0

    override fun clockMs(): Long { t += 16; return t }
    override fun pollEvent(): NovaEvent? = queue.removeFirstOrNull()
    override fun open(path: String, create: Boolean) = 1
    override fun read(fd: Int, maxLen: Int): ByteArray? = null
    override fun write(fd: Int, data: ByteArray): Int { saved.add(String(data)); return data.size }
    override fun close(fd: Int) {}
    override fun list(dir: String): List<String> = emptyList()
    override fun exit(code: Int) {}
}

object Main {

    @JvmStatic
    fun main(args: Array<String>) {
        val w = 1280
        val h = 720
        val host = HeadlessHost(w, h)
        val kernel = Kernel(host)

        val outDir = File(args.getOrElse(0) { "D:\\DSH工作\\NovaDesk\\_verify" })
        outDir.mkdirs()

        println("NovaDesk kernel smoke test")
        println("framebuffer: ${w}x$h")
        println("windows at boot: ${kernel.windows.size}")
        for (win in kernel.windows) {
            println("  - ${win.title} at (${win.x},${win.y}) ${win.w}x${win.h}")
        }

        // 帧 1：开机画面
        kernel.tick()
        dump(host, outDir, "01-boot")
        println("frame 1 drawn, windows=${kernel.windows.size}")
        val bootFrame = host.lastFrame!!.copyOf()

        // 在终端里敲命令
        val cmd = "help"
        for (c in cmd) {
            host.pushEvent(NovaEvent.Text(c))
            kernel.tick()
        }
        dump(host, outDir, "02a-typed")
        host.pushEvent(NovaEvent.Text('\n'))
        kernel.tick()
        dump(host, outDir, "02-terminal-help")
        println("typed 'help' -> terminal should now show command output")

        // 关键验证：终端真的有输出吗？画面必须和刚开机时不同
        val afterHelp = host.lastFrame!!
        val changed = bootFrame.indices.count { bootFrame[it] != afterHelp[it] }
        println("pixels changed by typing 'help': $changed")

        // 敲 ls
        for (c in "ls") { host.pushEvent(NovaEvent.Text(c)); kernel.tick() }
        host.pushEvent(NovaEvent.Text('\n'))
        kernel.tick()
        dump(host, outDir, "03-terminal-ls")

        // 拖动顶层窗口
        val top = kernel.windows.last()
        val tx = top.x + 100
        val ty = top.y + 12
        host.pushEvent(NovaEvent.Down(tx, ty, 0))
        kernel.tick()
        for (i in 1..40) {
            host.pushEvent(NovaEvent.Move(tx - i * 3, ty + i * 2, 0))
            kernel.tick()
        }
        host.pushEvent(NovaEvent.Up(tx - 120, ty + 80, 0))
        kernel.tick()
        dump(host, outDir, "04-dragged")
        println("dragged window to (${top.x},${top.y})")

        // 打开开始菜单
        host.pushEvent(NovaEvent.Down(26, h - 20, 0))
        kernel.tick()
        host.pushEvent(NovaEvent.Up(26, h - 20, 0))
        kernel.tick()
        dump(host, outDir, "05-startmenu")
        println("start menu open = ${kernel.startMenuOpen}")

        // 从开始菜单启动"clock"（第 4 项，索引 3）
        // 菜单几何：mw=190, ih=34, 起始 my = h - TASKBAR_H - (30 + n*34 + 8)
        val n = kernel.appNames().size
        val my = h - 40 - (30 + n * 34 + 8)
        val idx = kernel.appNames().indexOf("clock")
        val cy = my + 30 + idx * 34 + 14
        val cx = 60
        host.pushEvent(NovaEvent.Down(cx, cy, 0))
        kernel.tick()
        host.pushEvent(NovaEvent.Up(cx, cy, 0))
        kernel.tick()
        dump(host, outDir, "06-launched")
        println("launched via start menu -> ${kernel.windows.map { it.app.name }}")

        // 时钟应用跑几帧看指针动没动
        repeat(5) { kernel.tick() }
        dump(host, outDir, "07-clock")

        // 关闭一个窗口（点关闭按钮）
        val victim = kernel.windows.last()
        host.pushEvent(NovaEvent.Down(victim.closeBtnCx(), victim.closeBtnCy(), 0))
        kernel.tick()
        host.pushEvent(NovaEvent.Up(victim.closeBtnCx(), victim.closeBtnCy(), 0))
        kernel.tick()
        dump(host, outDir, "08-closed")
        println("after close: ${kernel.windows.map { it.app.name }}")

        // ---- 断言 ----
        var failures = 0
        fun check(name: String, cond: Boolean) {
            println((if (cond) "  PASS  " else "  FAIL  ") + name)
            if (!cond) failures++
        }
        println()
        println("checks:")
        check("kernel produced frames", host.frameCount > 20)
        check("frames are non-empty (not all black)", host.lastFrame!!.any { it != 0 })
        check("still has windows after close", kernel.windows.isNotEmpty())
        check("terminal printed output (pixels changed)", changed > 500)
        check("terminal is the focused window at boot",
            kernel.windows.last().app.name == "terminal")

        println()
        println("frames rendered: ${host.frameCount}")
        println("PNGs written to: ${outDir.absolutePath}")
        if (failures > 0) {
            println("RESULT: $failures failure(s)")
        } else {
            println("RESULT: all checks passed")
        }
    }

    /** 把帧导出成 PNG */
    private fun dump(host: HeadlessHost, dir: File, name: String) {
        val f = host.lastFrame ?: return
        val img = java.awt.image.BufferedImage(host.width, host.height, java.awt.image.BufferedImage.TYPE_INT_ARGB)
        img.setRGB(0, 0, host.width, host.height, f, 0, host.width)
        javax.imageio.ImageIO.write(img, "png", File(dir, "$name.png"))
    }
}

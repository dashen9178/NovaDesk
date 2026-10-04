package nova.apps

import nova.kernel.App
import nova.kernel.Canvas
import nova.kernel.Theme

/** 关于本机 —— 说明 NovaDesk 是什么 */
class AboutApp : App("about") {

    override fun icon() = "(i)"
    override fun defaultSize() = intArrayOf(430, 250)

    private var scroll = 0

    override fun draw(c: Canvas) {
        c.clear(0xFF14181F.toInt())
        c.fillRect(0, 0, c.w, 34, 0xFF1B2A3A.toInt())
        c.text("NovaDesk", 12, 11, 0xFF7EA6E0.toInt(), 2)

        val body = """
            A small operating system that runs
            inside an Android sandbox.

            Android provides ONLY:
              - a framebuffer (pixels)
              - touch events
              - a clock
              - a private file directory

            Everything else is ours:
              - the compositor and window manager
              - the font and rasterizer
              - the file system
              - the applications
              - the input dispatch

            No Android View is used to draw any
            of this. Android does not know what
            a "window" is here.

            Try: drag a title bar, resize by the
            bottom-right corner, open Start menu.
        """.trimIndent()

        val lines = body.split("\n")
        val lineH = nova.kernel.Font.CELL_H + 4
        val maxLines = (c.h - 44) / lineH
        val start = scroll.coerceIn(0, maxOf(0, lines.size - maxLines))
        var y = 44
        for (i in start until minOf(lines.size, start + maxLines)) {
            val color = if (lines[i].startsWith("  -")) Theme.ACCENT else Theme.TEXT
            c.text(lines[i], 12, y, color, 1)
            y += lineH
        }
        c.fillRect(0, c.h - 16, c.w, 16, 0xFF1B2A3A.toInt())
        c.text("swipe up/down inside to scroll", 8, c.h - 12, Theme.TEXT_DIM, 1)
    }

    override fun onTouch(x: Int, y: Int, phase: Int): Boolean {
        if (phase == PHASE_MOVE) {
            scroll = (scroll - 1).coerceAtLeast(0)
            return true
        }
        return false
    }
}

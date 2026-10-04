package nova.apps

import nova.kernel.App
import nova.kernel.Canvas
import nova.kernel.Theme

/** 画板 —— 直接用触摸在 NovaDesk 的画布上画像素 */
class PaintApp : App("paint") {

    override fun icon() = "/\\"
    override fun defaultSize() = intArrayOf(460, 320)

    private var surface = Canvas(1, 1)
    private var lastX = -1
    private var lastY = -1
    private var colorIdx = 0
    private var brush = 3

    private val palette = intArrayOf(
        0xFFE6EDF3.toInt(), 0xFFE05252.toInt(), 0xFF52C452.toInt(),
        0xFF4A90D9.toInt(), 0xFFE0A852.toInt(), 0xFFB05FD9.toInt()
    )

    override fun onResize(w: Int, h: Int) {
        surface = Canvas(maxOf(1, w), maxOf(1, h - 24))
    }

    override fun draw(c: Canvas) {
        if (surface.w != c.w || surface.h != c.h - 24) {
            val ns = Canvas(maxOf(1, c.w), maxOf(1, c.h - 24))
            ns.clear(0xFF0C1016.toInt())
            ns.blit(surface, 0, 0, surface.w, surface.h, 0, 0)
            surface = ns
        }
        c.clear(0xFF0C1016.toInt())
        c.blit(surface, 0, 0, surface.w, surface.h, 0, 0)

        // 调色板工具条
        c.fillRect(0, c.h - 24, c.w, 24, 0xFF1E242C.toInt())
        var x = 6
        for ((i, col) in palette.withIndex()) {
            c.fillRoundRect(x, c.h - 20, 16, 16, 3, col)
            if (i == colorIdx) c.strokeRect(x - 1, c.h - 21, 18, 18, 0xFFFFFFFF.toInt())
            x += 22
        }
        c.text("CLEAR", c.w - 50, c.h - 17, 0xFFE05252.toInt(), 1)
    }

    override fun onTouch(x: Int, y: Int, phase: Int): Boolean {
        if (y >= surface.h) {
            if (phase == PHASE_DOWN) {
                if (x > surface.w - 60) {
                    surface.clear(0xFF0C1016.toInt())
                } else {
                    val i = (x - 6) / 22
                    if (i in palette.indices) colorIdx = i
                }
            }
            return true
        }
        when (phase) {
            PHASE_DOWN -> { lastX = x; lastY = y; stamp(x, y) }
            PHASE_MOVE -> {
                if (lastX >= 0) {
                    // 在两点之间插值，避免快速滑动断线
                    val steps = maxOf(kotlin.math.abs(x - lastX), kotlin.math.abs(y - lastY))
                    for (s in 1..maxOf(1, steps)) {
                        val t = s.toFloat() / maxOf(1, steps)
                        stamp((lastX + (x - lastX) * t).toInt(), (lastY + (y - lastY) * t).toInt())
                    }
                }
                lastX = x; lastY = y
            }
            PHASE_UP -> { lastX = -1; lastY = -1 }
        }
        return true
    }

    private fun stamp(cx: Int, cy: Int) {
        val col = palette[colorIdx]
        for (dy in -brush..brush) for (dx in -brush..brush) {
            if (dx * dx + dy * dy <= brush * brush) surface.blend(cx + dx, cy + dy, col)
        }
    }
}

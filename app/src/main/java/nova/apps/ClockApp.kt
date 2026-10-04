package nova.apps

import nova.kernel.App
import nova.kernel.Canvas
import nova.kernel.Theme

/** 时钟 —— 用系统单调时钟驱动的模拟表盘 */
class ClockApp : App("clock") {

    override fun icon() = "(o)"
    override fun defaultSize() = intArrayOf(300, 300)

    private var secAngle = 0f
    private var minAngle = 0f
    private var hourAngle = 0f
    private var lastSec = -1L

    override fun draw(c: Canvas) {
        c.clear(0xFF0C1016.toInt())
        val cx = c.w / 2
        val cy = c.h / 2
        val r = minOf(cx, cy) - 16
        if (r < 20) return

        // 表盘
        for (dy in -r..r) for (dx in -r..r) {
            val d2 = dx * dx + dy * dy
            if (d2 <= r * r) c.blend(cx + dx, cy + dy, 0xFF161C24.toInt())
            else if (d2 <= (r + 2) * (r + 2)) c.blend(cx + dx, cy + dy, 0xFF3A4756.toInt())
        }

        // 刻度
        for (i in 0 until 12) {
            val a = Math.toRadians(i * 30.0 - 90.0)
            val x0 = cx + (Math.cos(a) * (r - 12)).toInt()
            val y0 = cy + (Math.sin(a) * (r - 12)).toInt()
            val x1 = cx + (Math.cos(a) * (r - 4)).toInt()
            val y1 = cy + (Math.sin(a) * (r - 4)).toInt()
            c.line(x0, y0, x1, y1, if (i % 3 == 0) Theme.ACCENT else 0xFF556070.toInt())
        }

        // 指针
        hand(c, cx, cy, hourAngle, (r * 0.5f).toInt(), 4, 0xFFE6EDF3.toInt())
        hand(c, cx, cy, minAngle, (r * 0.72f).toInt(), 3, 0xFF9FB4C7.toInt())
        hand(c, cx, cy, secAngle, (r * 0.85f).toInt(), 1, 0xFFE05252.toInt())

        // 中心轴
        for (dy in -3..3) for (dx in -3..3) {
            if (dx * dx + dy * dy <= 9) c.blend(cx + dx, cy + dy, Theme.ACCENT)
        }

        val s = sys.uptimeSeconds()
        val label = "${pad(s / 3600)}:${pad((s / 60) % 60)}:${pad(s % 60)}"
        c.textCentered(label, cx, c.h - 22, Theme.TEXT, 1)
        c.textCentered("uptime since boot", cx, c.h - 12, Theme.TEXT_DIM, 1)
    }

    private fun hand(c: Canvas, cx: Int, cy: Int, angle: Float, len: Int, thick: Int, color: Int) {
        val a = Math.toRadians(angle.toDouble() - 90.0)
        val ex = cx + (Math.cos(a) * len).toInt()
        val ey = cy + (Math.sin(a) * len).toInt()
        for (o in -(thick / 2)..(thick / 2)) {
            c.line(cx + o, cy, ex + o, ey, color)
            c.line(cx, cy + o, ex, ey + o, color)
        }
    }

    override fun update(dtMs: Long): Boolean {
        // 从系统时钟推导角度，而不是累加
        val total = sys.uptimeSeconds()
        val s = total % 60
        secAngle = (s * 6f)
        minAngle = ((total / 60) % 60) * 6f + s * 0.1f
        hourAngle = ((total / 3600) % 12) * 30f + ((total / 60) % 60) * 0.5f
        return true
    }

    private fun pad(v: Long) = if (v < 10) "0$v" else "$v"
}

package nova.kernel

/**
 * 窗口 —— NovaDesk 的核心抽象。
 *
 * 注意：窗口不是安卓的 View，不带任何系统控件。
 * 它只是一块内存画布 + 一组几何信息，由内核自己合成到显存。
 */
class Window(
    var title: String,
    var x: Int,
    var y: Int,
    var w: Int,
    var h: Int,
    val app: App
) {
    /** 窗口内容画布。app 往这里画。 */
    var surface: Canvas = Canvas(maxOf(1, w - 2 * Theme.BORDER), maxOf(1, h - Theme.TITLE_H - Theme.BORDER))

    var visible = true
    var maximized = false
    /** 最大化之前的位置，用于还原 */
    var restoreX = x
    var restoreY = y
    var restoreW = w
    var restoreH = h

    fun resizeSurface() {
        val nw = maxOf(1, w - 2 * Theme.BORDER)
        val nh = maxOf(1, h - Theme.TITLE_H - Theme.BORDER)
        if (surface.w != nw || surface.h != nh) {
            surface = Canvas(nw, nh)
            app.onResize(nw, nh)
        }
    }

    /** 标题栏矩形 */
    fun titleRect() = intArrayOf(x, y, w, Theme.TITLE_H)

    /** 关闭按钮中心相对窗口的位置 */
    fun closeBtnCx() = x + w - 14
    fun closeBtnCy() = y + Theme.TITLE_H / 2
    fun minBtnCx() = x + w - 34
    fun minBtnCy() = y + Theme.TITLE_H / 2
    fun maxBtnCx() = x + w - 54
    fun maxBtnCy() = y + Theme.TITLE_H / 2

    fun hitTitle(px: Int, py: Int) = px >= x && px < x + w && py >= y && py < y + Theme.TITLE_H

    fun hitClose(px: Int, py: Int): Boolean {
        val dx = px - closeBtnCx(); val dy = py - closeBtnCy()
        return dx * dx + dy * dy <= 49
    }

    fun hitMin(px: Int, py: Int): Boolean {
        val dx = px - minBtnCx(); val dy = py - closeBtnCy()
        return dx * dx + dy * dy <= 49
    }

    fun hitMax(px: Int, py: Int): Boolean {
        val dx = px - maxBtnCx(); val dy = py - closeBtnCy()
        return dx * dx + dy * dy <= 49
    }

    /** 内容区原点（屏幕坐标） */
    fun contentX() = x + Theme.BORDER
    fun contentY() = y + Theme.TITLE_H

    /** 把屏幕坐标转成内容区的本地坐标；不在内容区则返回 null */
    fun toLocal(px: Int, py: Int): IntArray? {
        val lx = px - contentX()
        val ly = py - contentY()
        if (lx < 0 || ly < 0 || lx >= surface.w || ly >= surface.h) return null
        return intArrayOf(lx, ly)
    }

    fun contains(px: Int, py: Int) = px >= x && px < x + w && py >= y && py < y + h
}

package nova.kernel

/**
 * 应用基类 —— NovaDesk 的"可执行格式"。
 *
 * 每个应用往自己的 surface 上画界面，通过 onTouch 收本地坐标事件。
 * 应用完全不知道安卓存在，也不会自己去画窗口标题栏——那是内核的职责。
 */
abstract class App(val name: String) {

    /** 所属窗口，由内核在创建时注入 */
    lateinit var win: Window

    /** 内核提供的系统服务 */
    lateinit var sys: Kernel

    /** 应用图标字符（点阵字库能画出来的符号） */
    open fun icon(): String = "?"

    /** 窗口初始尺寸 */
    open fun defaultSize(): IntArray = intArrayOf(460, 300)

    /** 每帧绘制。内核会在需要时调用。 */
    abstract fun draw(c: Canvas)

    /** 每帧更新逻辑。返回 true 表示需要重绘。 */
    open fun update(dtMs: Long): Boolean = false

    /** 触摸事件，坐标是 surface 本地坐标。返回 true 表示消费了事件。 */
    open fun onTouch(x: Int, y: Int, phase: Int): Boolean = false

    /** 键盘输入（字符，0 表示功能键）。 */
    open fun onChar(ch: Char): Boolean = false

    open fun onKey(code: Int): Boolean = false

    /** 窗口尺寸变化 */
    open fun onResize(w: Int, h: Int) {}

    /** 窗口关闭时的清理 */
    open fun onClose() {}

    companion object {
        const val PHASE_DOWN = 0
        const val PHASE_MOVE = 1
        const val PHASE_UP = 2
    }
}

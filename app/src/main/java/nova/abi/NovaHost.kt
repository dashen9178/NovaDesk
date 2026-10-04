package nova.abi

/**
 * NovaDesk 系统调用 ABI —— 内核与宿主之间唯一的契约。
 *
 * 设计宪法：
 *   宿主（安卓）只实现这些原语，永远不增长。
 *   宿主永远不知道"窗口""按钮""终端"是什么——那些是 NovaDesk 的概念。
 *
 * 因此这个文件里不允许出现任何 android.* 的引用。
 * kernel / apps 包只允许依赖本文件。
 */
interface NovaHost {

    // ---------- 1. 显存 ----------

    /** 宽度（像素，已按屏幕方向旋转到横屏） */
    val width: Int

    /** 高度（像素） */
    val height: Int

    /**
     * 提交一帧。
     * @param argb 长度必须 >= width*height 的 ARGB_8888 像素数组，行优先。
     *             内核自己在这块内存里画完所有东西，宿主只负责贴到屏幕上。
     */
    fun present(argb: IntArray)

    // ---------- 2. 时间 ----------

    /** 单调时钟，毫秒。仅用于动画计时，不保证与墙上时间一致。 */
    fun clockMs(): Long

    // ---------- 3. 输入 ----------

    /**
     * 取出一个待处理输入事件，没有则返回 null。
     * 内核每帧轮询直到返回 null。
     */
    fun pollEvent(): NovaEvent?

    // ---------- 4. 存储 ----------

    /** 在沙箱私有目录内打开文件。[path] 是 NovaDesk 内部路径，如 "/home/note.txt"。 */
    fun open(path: String, create: Boolean): Int

    fun read(fd: Int, maxLen: Int): ByteArray?

    fun write(fd: Int, data: ByteArray): Int

    fun close(fd: Int)

    fun list(dir: String): List<String>

    // ---------- 5. 退出 ----------

    fun exit(code: Int)
}

/** 输入事件。坐标已转换为 NovaDesk 屏幕坐标系（左上角原点，横屏）。 */
sealed class NovaEvent {
    /** 触摸按下 */
    data class Down(val x: Int, val y: Int, val t: Long) : NovaEvent()

    /** 触摸移动 */
    data class Move(val x: Int, val y: Int, val t: Long) : NovaEvent()

    /** 触摸抬起 */
    data class Up(val x: Int, val y: Int, val t: Long) : NovaEvent()

    /** 物理按键。code 见 [NovaKey]。 */
    data class Key(val code: Int, val down: Boolean, val t: Long) : NovaEvent()

    /** 文本输入（软键盘、回车、退格）。内核把它转给焦点窗口。 */
    data class Text(val ch: kotlin.Char) : NovaEvent()

    /** 屏幕尺寸变化（旋转/分屏）。 */
    data class Resize(val w: Int, val h: Int) : NovaEvent()
}

object NovaKey {
    const val BACK = 1
    const val ENTER = 2
    const val ESC = 3
    const val UP = 4
    const val DOWN = 5
    const val LEFT = 6
    const val RIGHT = 7
    const val TAB = 8
}

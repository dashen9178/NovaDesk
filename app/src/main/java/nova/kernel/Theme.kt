package nova.kernel

/**
 * NovaDesk 视觉规范 —— 所有颜色、尺寸集中在这里。
 * 想换皮肤只改这个文件。
 */
object Theme {
    // 桌面背景：深空渐变的两端色
    const val DESK_TOP = 0xFF1B2838.toInt()
    const val DESK_BOTTOM = 0xFF0D1620.toInt()

    // 窗口
    const val WIN_BG = 0xF01E2228.toInt()
    const val WIN_TITLE_ACTIVE = 0xFF2D3748.toInt()
    const val WIN_TITLE_INACTIVE = 0xFF252A31.toInt()
    const val WIN_BORDER_ACTIVE = 0xFF4A90D9.toInt()
    const val WIN_BORDER_INACTIVE = 0xFF3A4048.toInt()
    const val WIN_SHADOW = 0x50000000

    // 文字
    const val TEXT = 0xFFE6EDF3.toInt()
    const val TEXT_DIM = 0xFF8B949E.toInt()
    const val TEXT_TITLE = 0xFFFFFFFF.toInt()

    // 控件
    const val BTN_CLOSE = 0xFFE05252.toInt()
    const val BTN_MIN = 0xFFE0A852.toInt()
    const val BTN_MAX = 0xFF52C452.toInt()
    const val ACCENT = 0xFF4A90D9.toInt()

    // 任务栏
    const val TASKBAR_BG = 0xF01A1F26.toInt()
    const val TASKBAR_H = 40
    const val TASKBAR_ITEM = 0xFF2A313A.toInt()
    const val TASKBAR_ITEM_ACTIVE = 0xFF3A4756.toInt()

    // 窗口度量
    const val TITLE_H = 26
    const val BORDER = 1
    const val MIN_W = 160
    const val MIN_H = 100
}

package nova.apps

import nova.kernel.App
import nova.kernel.Canvas
import nova.kernel.Font
import nova.kernel.Theme

/** 记事本 —— 演示应用读写 VFS，并可通过 save 持久化到安卓沙箱 */
class NotesApp : App("notes") {

    private val buf = StringBuilder()
    private var path = "/home/note.txt"
    private var saved = true
    private var status = ""
    private var statusUntil = 0L
    private var blink = 0L
    private var showCursor = true
    private var scrollLine = 0

    override fun icon() = "~~"
    override fun defaultSize() = intArrayOf(430, 290)

    init {
        // 载入已有内容（如果有）
        // sys 还未注入，延迟到首次 draw
    }

    override fun draw(c: Canvas) {
        if (sys.vfs.exists(path) && buf.isEmpty() && saved) {
            buf.append(sys.vfs.read(path) ?: "")
        }

        c.clear(0xFF14181F.toInt())

        // 工具栏
        c.fillRect(0, 0, c.w, 22, 0xFF1E242C.toInt())
        c.text(if (saved) "saved" else "modified", 8, 8, if (saved) Theme.TEXT_DIM else 0xFFFFA657.toInt(), 1)
        c.text("| " + path, 60, 8, Theme.TEXT_DIM, 1)
        c.text("SAVE", c.w - 46, 8, Theme.ACCENT, 1)
        c.fillRect(0, 22, c.w, 1, 0x30FFFFFF)

        // 正文
        val lineH = Font.CELL_H + 3
        val lines = buf.toString().split("\n")
        val maxLines = (c.h - 30) / lineH
        val start = scrollLine.coerceIn(0, maxOf(0, lines.size - maxLines))
        var y = 28
        for (i in start until minOf(lines.size, start + maxLines)) {
            c.text(lines[i], 8, y, Theme.TEXT, 1)
            y += lineH
        }

        // 光标画在最后一行末尾
        if (showCursor) {
            val lastIdx = lines.size - 1
            if (lastIdx in start until start + maxLines) {
                val ly = 28 + (lastIdx - start) * lineH
                val lx = 8 + c.textWidth(lines[lastIdx], 1)
                c.fillRect(lx + 1, ly, 5, Font.CELL_H, Theme.ACCENT)
            }
        }

        // 状态栏
        if (status.isNotEmpty() && sys.uptimeSeconds() * 1000 < statusUntil) {
            c.fillRoundRect(8, c.h - 26, c.textWidth(status, 1) + 16, 18, 4, 0xE02D3748.toInt())
            c.text(status, 16, c.h - 21, 0xFF7EE787.toInt(), 1)
        }
    }

    override fun update(dtMs: Long): Boolean {
        blink += dtMs
        if (blink > 500) { blink = 0; showCursor = !showCursor; return true }
        return false
    }

    override fun onChar(ch: Char): Boolean {
        when (ch) {
            '\n', '\r' -> buf.append('\n')
            '\b' -> if (buf.isNotEmpty()) buf.deleteCharAt(buf.length - 1)
            else -> if (ch.code >= 32) buf.append(ch)
        }
        saved = false
        return true
    }

    override fun onTouch(x: Int, y: Int, phase: Int): Boolean {
        if (phase != PHASE_DOWN) return false
        if (y < 22 && x > win.surface.w - 60) {
            sys.vfs.write(path, buf.toString())
            saved = true
            val ok = sys.vfs.persist(path)
            status = if (ok) "saved to Android sandbox" else "saved in memory only"
            statusUntil = sys.uptimeSeconds() * 1000 + 2000
            return true
        }
        return false
    }

    override fun onKey(code: Int): Boolean = false
}

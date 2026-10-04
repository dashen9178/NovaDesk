package nova.apps

import nova.kernel.App
import nova.kernel.Canvas
import nova.kernel.Font
import nova.kernel.Theme

/** 文件管理器 —— 浏览 NovaDesk 自己的 VFS */
class FilesApp : App("files") {

    private var dir = "/"
    private var items: List<String> = emptyList()
    private var selected = 0
    private var showContent: String? = null
    private var contentScroll = 0
    private var lastW = 0

    override fun icon() = "[]"
    override fun defaultSize() = intArrayOf(440, 300)

    override fun onResize(w: Int, h: Int) { lastW = w }
    override fun onClose() {}

    private fun refresh() {
        items = sys.vfs.list(dir)
        selected = selected.coerceIn(0, maxOf(0, items.size - 1))
    }

    override fun draw(c: Canvas) {
        c.clear(0xFF14181F.toInt())
        if (items.isEmpty() && lastW == 0) { refresh(); lastW = c.w }

        // 路径栏
        c.fillRect(0, 0, c.w, 22, 0xFF1E242C.toInt())
        c.text(dir, 8, 8, Theme.ACCENT, 1)
        c.fillRect(0, 22, c.w, 1, 0x30FFFFFF)

        if (showContent != null) {
            drawContent(c)
            return
        }

        val rowH = 20
        val listW = c.w / 2
        var y = 28
        for ((i, name) in items.withIndex()) {
            if (y > c.h - rowH) break
            val isDir = name.endsWith("/")
            val sel = i == selected
            if (sel) c.fillRect(2, y - 2, listW - 4, rowH, 0x304A90D9)
            val label = if (isDir) name.dropLast(1) else name
            val color = if (isDir) 0xFF7EA6E0.toInt() else Theme.TEXT
            c.text((if (isDir) "[+] " else "    ") + label, 8, y + 3, color, 1)
            y += rowH
        }
        if (items.isEmpty()) c.text("(empty)", 10, 30, Theme.TEXT_DIM, 1)

        // 右侧预览
        c.fillRect(listW, 23, 1, c.h - 23, 0x30FFFFFF)
        val selName = items.getOrNull(selected)
        if (selName != null && !selName.endsWith("/")) {
            val path = if (dir == "/") "/$selName" else "$dir/$selName"
            val txt = sys.vfs.read(path) ?: ""
            var py = 28
            val maxLines = (c.h - 34) / (Font.CELL_H + 3)
            for (l in txt.split("\n").take(maxLines)) {
                c.text(l, listW + 8, py, Theme.TEXT_DIM, 1)
                py += Font.CELL_H + 3
            }
        } else if (selName != null) {
            c.text("directory", listW + 8, 28, Theme.TEXT_DIM, 1)
        }

        // 底部提示
        c.fillRect(0, c.h - 18, c.w, 18, 0xFF1E242C.toInt())
        c.text("click to open  |  top row: .. to go up", 8, c.h - 13, Theme.TEXT_DIM, 1)
    }

    private fun drawContent(c: Canvas) {
        val txt = showContent ?: return
        c.text("VIEW: $txt", 8, 4, 0xFF7EE787.toInt(), 1).let { }
        val body = sys.vfs.read(txt) ?: "(cannot read)"
        val lines = body.split("\n")
        val lineH = Font.CELL_H + 3
        val maxLines = (c.h - 30) / lineH
        val start = contentScroll.coerceIn(0, maxOf(0, lines.size - maxLines))
        var y = 26
        for (i in start until minOf(lines.size, start + maxLines)) {
            c.text(lines[i], 8, y, Theme.TEXT, 1)
            y += lineH
        }
        c.fillRect(0, c.h - 18, c.w, 18, 0xFF1E242C.toInt())
        c.text("click anywhere to go back", 8, c.h - 13, Theme.TEXT_DIM, 1)
    }

    override fun onTouch(x: Int, y: Int, phase: Int): Boolean {
        if (phase != PHASE_DOWN) return false
        if (showContent != null) { showContent = null; contentScroll = 0; return true }
        if (y < 24) return false

        if (x < win.surface.w / 2) {
            val idx = (y - 28) / 20
            if (idx in items.indices) {
                val name = items[idx]
                selected = idx
                val path = if (dir == "/") "/$name" else "$dir/$name"
                if (name.endsWith("/")) {
                    if (name == "../") {
                        val parent = dir.substringBeforeLast('/', "")
                        dir = if (parent.isEmpty()) "/" else parent
                    } else {
                        dir = path.trimEnd('/')
                    }
                    selected = 0
                    refresh()
                } else {
                    showContent = path
                    contentScroll = 0
                }
            }
        } else {
            showContent = null
        }
        return true
    }
}

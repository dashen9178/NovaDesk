package nova.apps

import nova.kernel.App
import nova.kernel.Canvas
import nova.kernel.Font
import nova.kernel.Theme

/**
 * 终端 —— NovaDesk 的 shell。
 * 这是证明系统"活着"的应用：能打字、能执行命令、能读写自己的文件系统。
 */
class TerminalApp : App("terminal") {

    private val lines = ArrayList<String>()
    private val input = StringBuilder()
    private var scroll = 0
    private var cwd = "/home"
    private var cursorBlink = 0L
    private var showCursor = true

    /** 历史记录 */
    private val history = ArrayList<String>()
    private var histIndex = -1

    override fun icon() = ">_"

    override fun defaultSize() = intArrayOf(520, 320)

    init {
        lines.add("NovaDesk Terminal v1.0")
        lines.add("Type 'help' for commands.")
        lines.add("")
    }

    override fun draw(c: Canvas) {
        c.clear(0xFF0C1016.toInt())

        val lineH = Font.CELL_H + 3
        val maxLines = (c.h - 8) / lineH
        val start = (lines.size - maxLines + 1 + scroll).coerceAtLeast(0)

        var y = 4
        var i = start
        while (i < lines.size && y < c.h - lineH) {
            val ln = lines[i]
            val color = when {
                ln.startsWith("$ ") -> 0xFF7EE787.toInt()
                ln.startsWith("!") -> 0xFFFF7B72.toInt()
                ln.startsWith("#") -> Theme.TEXT_DIM
                else -> Theme.TEXT
            }
            c.text(ln, 5, y, color, 1)
            y += lineH
            i++
        }

        // 当前输入行
        val prompt = "$input"
        c.text(prompt, 5, y, Theme.TEXT, 1)
        if (showCursor) {
            val cx = 5 + c.textWidth(prompt, 1)
            c.fillRect(cx, y, 5, Font.CELL_H, 0xFF7EE787.toInt())
        }
    }

    override fun update(dtMs: Long): Boolean {
        cursorBlink += dtMs
        if (cursorBlink > 500) {
            cursorBlink = 0
            showCursor = !showCursor
            return true
        }
        return false
    }

    override fun onChar(ch: Char): Boolean {
        when (ch) {
            '\n', '\r' -> execute()
            '\b' -> if (input.isNotEmpty()) input.deleteCharAt(input.length - 1)
            else -> if (ch.code >= 32 && ch.code < 127) input.append(ch)
        }
        scroll = 0
        return true
    }

    override fun onKey(code: Int): Boolean {
        return when (code) {
            nova.abi.NovaKey.UP -> { recallHistory(-1); true }
            nova.abi.NovaKey.DOWN -> { recallHistory(1); true }
            else -> false
        }
    }

    private fun recallHistory(dir: Int) {
        if (history.isEmpty()) return
        histIndex = (histIndex + dir).coerceIn(-1, history.size - 1)
        input.setLength(0)
        if (histIndex in history.indices) input.append(history[histIndex])
    }

    private fun execute() {
        val cmd = input.toString().trim()
        input.setLength(0)
        lines.add("$ $cmd")
        if (cmd.isNotEmpty()) {
            history.add(cmd)
            histIndex = history.size
        }
        runCommand(cmd)
        lines.add("")
        // 限制滚动缓冲
        while (lines.size > 400) lines.removeAt(0)
    }

    private fun runCommand(cmd: String) {
        if (cmd.isEmpty()) return
        val parts = cmd.split(Regex("\\s+"))
        val op = parts[0].lowercase()
        val args = parts.drop(1)

        when (op) {
            "help" -> {
                lines.add("Commands:")
                lines.add("  help           this message")
                lines.add("  ls [dir]       list directory")
                lines.add("  cat <file>     print a file")
                lines.add("  cd <dir>       change directory")
                lines.add("  pwd            print working directory")
                lines.add("  echo <text>    print text")
                lines.add("  write <f> <t>  write text to file")
                lines.add("  rm <file>      delete file")
                lines.add("  mkdir <dir>    create directory")
                lines.add("  save <file>    persist file to sandbox")
                lines.add("  date           system uptime")
                lines.add("  mem            framebuffer info")
                lines.add("  clear          clear screen")
                lines.add("  apps           list installed apps")
            }
            "ls" -> {
                val d = if (args.isEmpty()) cwd else resolve(args[0])
                if (!sys.vfs.isDir(d) && !sys.vfs.exists(d)) {
                    lines.add("!no such directory: $d"); return
                }
                val items = sys.vfs.list(d)
                if (items.isEmpty()) lines.add("(empty)")
                else {
                    // 三列排布
                    var row = StringBuilder()
                    for ((i, n) in items.withIndex()) {
                        row.append(n.padEnd(16))
                        if ((i + 1) % 3 == 0) { lines.add(row.toString()); row = StringBuilder() }
                    }
                    if (row.isNotEmpty()) lines.add(row.toString())
                }
            }
            "cat" -> {
                if (args.isEmpty()) { lines.add("!usage: cat <file>"); return }
                val f = resolve(args[0])
                val content = sys.vfs.read(f)
                if (content == null) lines.add("!no such file: $f")
                else for (l in content.split("\n")) lines.add(l)
            }
            "cd" -> {
                val d = if (args.isEmpty()) "/home" else resolve(args[0])
                if (sys.vfs.isDir(d)) cwd = d else lines.add("!no such directory: $d")
            }
            "pwd" -> lines.add(cwd)
            "echo" -> lines.add(args.joinToString(" "))
            "write" -> {
                if (args.size < 2) { lines.add("!usage: write <file> <text>"); return }
                val f = resolve(args[0])
                val text = args.drop(1).joinToString(" ")
                sys.vfs.write(f, text)
                lines.add("#wrote ${text.length} bytes to $f")
            }
            "append" -> {
                if (args.size < 2) { lines.add("!usage: append <file> <text>"); return }
                val f = resolve(args[0])
                sys.vfs.append(f, args.drop(1).joinToString(" ") + "\n")
                lines.add("#appended to $f")
            }
            "rm" -> {
                if (args.isEmpty()) { lines.add("!usage: rm <file>"); return }
                val f = resolve(args[0])
                if (sys.vfs.delete(f)) lines.add("#deleted $f") else lines.add("!no such file: $f")
            }
            "mkdir" -> {
                if (args.isEmpty()) { lines.add("!usage: mkdir <dir>"); return }
                sys.vfs.mkdir(resolve(args[0]))
                lines.add("#created ${resolve(args[0])}")
            }
            "save" -> {
                if (args.isEmpty()) { lines.add("!usage: save <file>"); return }
                val f = resolve(args[0])
                if (sys.vfs.persist(f)) lines.add("#persisted $f to Android sandbox")
                else lines.add("!failed to persist $f")
            }
            "date" -> {
                val s = sys.uptimeSeconds()
                lines.add("uptime: ${s / 60}m ${s % 60}s  (monotonic clock via sys_clock_ms)")
            }
            "mem" -> {
                lines.add("framebuffer: ${sys.screenW()}x${sys.screenH()} ARGB_8888")
                lines.add("bytes: ${sys.screenW() * sys.screenH() * 4}")
                lines.add("renderer: software rasterizer (our own)")
            }
            "apps" -> for (n in sys.appNames()) lines.add("  $n")
            "clear" -> { lines.clear(); return }
            "uname" -> {
                lines.add("NovaDesk 1.0 (sandboxed)")
                lines.add("host: Android HAL + framebuffer ABI")
                lines.add("arch: kotlin-vm")
            }
            else -> lines.add("!unknown command: $op  (try 'help')")
        }
    }

    /** 相对路径 → 绝对路径 */
    private fun resolve(p: String): String {
        if (p.startsWith("/")) return p
        if (p == "..") {
            val parent = cwd.substringBeforeLast('/', "")
            return if (parent.isEmpty()) "/" else parent
        }
        return if (cwd == "/") "/$p" else "$cwd/$p"
    }
}

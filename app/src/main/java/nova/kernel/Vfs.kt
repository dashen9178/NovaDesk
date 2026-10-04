package nova.kernel

import nova.abi.NovaHost

/**
 * NovaDesk 虚拟文件系统 —— 内存实现。
 *
 * 所有路径都是 NovaDesk 内部路径（如 /home/note.txt），
 * 通过 ABI 的 open/read/write 与宿主沙箱目录交换数据。
 * 重启后内存部分丢失，但标记为 persistent 的文件会写回宿主。
 */
class Vfs(private val host: NovaHost) {

    private val files = HashMap<String, StringBuilder>()
    private val dirs = HashSet<String>()

    init {
        dirs.add("/")
        dirs.add("/home")
        dirs.add("/apps")
        dirs.add("/tmp")

        files["/readme.txt"] = StringBuilder(
            "Welcome to NovaDesk.\n" +
            "This is not an Android app UI.\n" +
            "Every pixel here is drawn by our own kernel.\n" +
            "\n" +
            "Try the Terminal and type: help\n"
        )
        files["/home/welcome.txt"] = StringBuilder(
            "NovaDesk is a small operating system\n" +
            "running inside an Android sandbox.\n" +
            "Android only provides: a framebuffer,\n" +
            "touch events, and a clock.\n" +
            "Everything else you see is ours.\n"
        )
    }

    fun exists(path: String) = files.containsKey(norm(path)) || dirs.contains(norm(path))

    fun isDir(path: String) = dirs.contains(norm(path))

    fun read(path: String): String? = files[norm(path)]?.toString()

    fun write(path: String, content: String) {
        val p = norm(path)
        files[p] = StringBuilder(content)
        // 确保父目录存在
        val parent = p.substringBeforeLast('/', "/")
        if (parent.isNotEmpty()) dirs.add(parent)
    }

    fun append(path: String, content: String) {
        val p = norm(path)
        files.getOrPut(p) { StringBuilder() }.append(content)
    }

    fun delete(path: String): Boolean = files.remove(norm(path)) != null

    fun mkdir(path: String) { dirs.add(norm(path)) }

    /** 列出目录下的条目名（不含路径） */
    fun list(dir: String): List<String> {
        val d = norm(dir)
        val prefix = if (d == "/") "/" else "$d/"
        val out = ArrayList<String>()
        for (f in files.keys) {
            if (f.startsWith(prefix) && f != d) {
                val rest = f.substring(prefix.length)
                if (rest.isNotEmpty() && !rest.contains('/')) out.add(rest)
            }
        }
        for (sub in dirs) {
            if (sub.startsWith(prefix) && sub != d) {
                val rest = sub.substring(prefix.length)
                if (rest.isNotEmpty() && !rest.contains('/')) out.add("$rest/")
            }
        }
        return out.sorted()
    }

    /** 把内存文件持久化到宿主沙箱 */
    fun persist(path: String): Boolean {
        val content = read(path) ?: return false
        val fd = host.open("/novadesk/" + norm(path).trimStart('/'), true)
        if (fd < 0) return false
        host.write(fd, content.toByteArray(Charsets.UTF_8))
        host.close(fd)
        return true
    }

    private fun norm(p: String): String {
        var s = p.trim()
        if (!s.startsWith("/")) s = "/$s"
        while (s.length > 1 && s.endsWith("/")) s = s.dropLast(1)
        return s
    }
}

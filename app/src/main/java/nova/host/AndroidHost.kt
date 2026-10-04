package nova.host

import android.content.Context
import java.io.File
import java.io.RandomAccessFile
import nova.abi.NovaEvent
import nova.abi.NovaHost

/**
 * 宿主层 —— NovaDesk 里唯一允许出现 android.* 的地方。
 *
 * 它只实现 NovaHost 定义的原语，绝不包含任何 UI 逻辑。
 * 安卓在这里被降级为：文件目录 + 时钟 + 事件队列 + 一块屏幕。
 *
 * 这个类永远不应该超过几百行。如果它开始变长，说明架构跑偏了。
 */
class AndroidHost(private val ctx: Context) : NovaHost {

    override var width: Int = 1
        private set
    override var height: Int = 1
        private set

    /**
     * 双缓冲：内核画到 back，UI 线程从 front 读。
     * 避免渲染线程和 onDraw 争抢同一块数组造成撕裂。
     */
    @Volatile
    private var front: IntArray = IntArray(1)
    private var back: IntArray = IntArray(1)

    /** UI 线程调用，拿到当前可用于绘制的缓冲。 */
    fun snapshot(): IntArray = front

    fun dims(): IntArray = intArrayOf(width, height)

    private val events = ArrayDeque<NovaEvent>()
    private val eventLock = Object()

    private val files = HashMap<Int, RandomAccessFile>()
    private var nextFd = 1
    private val baseDir: File by lazy {
        File(ctx.filesDir, "novadesk").apply { if (!exists()) mkdirs() }
    }

    fun resize(w: Int, h: Int) {
        width = w
        height = h
        back = IntArray(w * h)
        front = IntArray(w * h)
        push(NovaEvent.Resize(w, h))
    }

    fun push(e: NovaEvent) {
        synchronized(eventLock) {
            // 事件队列限长，防止卡顿时无限堆积
            if (events.size < 512) events.addLast(e)
        }
    }

    // ---------------- ABI 实现 ----------------

    override fun present(argb: IntArray) {
        // 宿主不遍历、不解析、不修改像素——只交换缓冲。
        // 内核在这块内存里已经画好了窗口、文字、任务栏，
        // 安卓完全不知道那些是什么。
        val b = back
        if (argb !== b) argb.copyInto(b, 0, 0, minOf(argb.size, b.size))
        front = b
    }

    override fun clockMs(): Long = System.nanoTime() / 1_000_000L

    override fun pollEvent(): NovaEvent? {
        synchronized(eventLock) { return events.removeFirstOrNull() }
    }

    override fun open(path: String, create: Boolean): Int {
        val f = File(baseDir, path.trimStart('/'))
        val parent = f.parentFile
        if (parent != null && !parent.exists()) parent.mkdirs()
        if (!f.exists()) {
            if (!create) return -1
            f.createNewFile()
        }
        @Suppress("DEPRECATION")
        val raf = RandomAccessFile(f, "rw")
        val fd = nextFd++
        files[fd] = raf
        return fd
    }

    override fun read(fd: Int, maxLen: Int): ByteArray? {
        val raf = files[fd] ?: return null
        val len = minOf(maxLen.toLong(), raf.length()).toInt()
        if (len <= 0) return ByteArray(0)
        val buf = ByteArray(len)
        raf.seek(0)
        raf.readFully(buf)
        return buf
    }

    override fun write(fd: Int, data: ByteArray): Int {
        val raf = files[fd] ?: return -1
        // 覆盖语义：persist() 每次写出完整内容
        raf.seek(0)
        raf.setLength(0)
        raf.write(data)
        return data.size
    }

    override fun close(fd: Int) {
        files.remove(fd)?.close()
    }

    override fun list(dir: String): List<String> {
        val d = File(baseDir, dir.trimStart('/'))
        return d.listFiles()?.map { it.name } ?: emptyList()
    }

    override fun exit(code: Int) {
        android.os.Process.killProcess(android.os.Process.myPid())
    }
}

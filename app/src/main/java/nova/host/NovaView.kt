package nova.host

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.MotionEvent
import android.view.SurfaceHolder
import android.view.SurfaceView
import nova.abi.NovaEvent
import nova.abi.NovaKey

/**
 * 屏幕 —— 把内核的像素缓冲贴到安卓的 Surface 上。
 *
 * 关键点：这里没有任何布局、控件、文字或窗口逻辑。
 * 它只做一件事：Bitmap ← IntArray ← Surface。
 *
 * 所有"界面"都发生在内核里。安卓看到的只是一张图。
 */
@SuppressLint("ViewConstructor")
class NovaView(
    context: Context,
    private val host: AndroidHost
) : SurfaceView(context), SurfaceHolder.Callback {

    private var bmp: Bitmap? = null
    private var bmpW = 0
    private var bmpH = 0

    /** 触摸拖动方向：横屏下手指的 dx/dy 直接对应屏幕坐标 */
    private var lastX = 0f
    private var lastY = 0f

    init {
        holder.addCallback(this)
        isFocusable = true
        isFocusableInTouchMode = true
        keepScreenOn = true
    }

    override fun surfaceCreated(h: SurfaceHolder) {}

    override fun surfaceChanged(h: SurfaceHolder, format: Int, w: Int, ht: Int) {
        host.resize(w, ht)
    }

    override fun surfaceDestroyed(h: SurfaceHolder) {}

    /** 渲染线程调用 */
    fun renderFrame() {
        val h = holder
        if (!h.surface.isValid) return
        val dims = host.dims()
        if (dims[0] <= 1 || dims[1] <= 1) return

        if (bmp == null || bmpW != dims[0] || bmpH != dims[1]) {
            bmp?.recycle()
            bmp = Bitmap.createBitmap(dims[0], dims[1], Bitmap.Config.ARGB_8888)
            bmpW = dims[0]
            bmpH = dims[1]
        }
        val b = bmp ?: return
        // 内核刚画好的一整屏，原样搬进位图
        b.setPixels(host.snapshot(), 0, dims[0], 0, 0, dims[0], dims[1])

        var canvas: Canvas? = null
        try {
            canvas = h.lockCanvas()
            if (canvas != null) canvas.drawBitmap(b, 0f, 0f, null)
        } finally {
            if (canvas != null) {
                try { h.unlockCanvasAndPost(canvas) } catch (_: Exception) {}
            }
        }
    }

    // ---------------- 输入 ----------------

    override fun onTouchEvent(event: MotionEvent): Boolean {
        val x = event.x.toInt()
        val y = event.y.toInt()
        val t = host.clockMs()
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                lastX = event.x; lastY = event.y
                host.push(NovaEvent.Down(x, y, t))
            }
            MotionEvent.ACTION_MOVE -> {
                lastX = event.x; lastY = event.y
                host.push(NovaEvent.Move(x, y, t))
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                host.push(NovaEvent.Up(x, y, t))
            }
        }
        return true
    }

    /** 物理返回键交给 NovaDesk 内核处理（关闭窗口 / 关开始菜单） */
    fun sendBack() = host.push(NovaEvent.Key(NovaKey.BACK, true, host.clockMs()))

    /** 软键盘上来的字符直接喂给焦点窗口 */
    fun sendChar(ch: Char) = host.push(NovaEvent.Text(ch))

    fun sendEnter() = host.push(NovaEvent.Text('\n'))
    fun sendBackspace() = host.push(NovaEvent.Text('\b'))
}

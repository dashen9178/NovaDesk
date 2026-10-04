package nova.host

import android.os.Bundle
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import nova.kernel.Kernel

/**
 * 引导器 —— 安卓这一侧的全部入口。
 *
 * 它的工作只有三件事：
 *   1. 把 NovaView 铺满屏幕
 *   2. 开一个线程跑内核主循环
 *   3. 提供一个隐藏的 EditText，把软键盘的输入灌进内核
 *
 * 除此之外不做任何界面。注意这里没有任何按钮、标题栏、
 * 布局美学——因为那些都属于 NovaDesk 自己。
 */
class NovaActivity : AppCompatActivity() {

    private lateinit var host: AndroidHost
    private lateinit var view: NovaView
    private lateinit var kernel: Kernel
    private var loop: Thread? = null

    @Volatile
    private var running = false

    /** 隐藏输入法桥：安卓软键盘 → 内核 */
    private lateinit var ime: EditText
    private var lastImeLen = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 全屏沉浸，横屏
        window.setFlags(
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        )
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemBars()

        host = AndroidHost(this)
        view = NovaView(this, host)

        // 一个 1x1 透明的 EditText，专门用来唤起软键盘。
        // 这是我们唯一"借用"的安卓控件，且用户永远看不见它。
        ime = EditText(this).apply {
            setBackgroundColor(0x00000000)
            setTextColor(0x00000000)
            isCursorVisible = false
            layoutParams = LinearLayout.LayoutParams(1, 1)
            addTextChangedListener(object : android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun afterTextChanged(s: android.text.Editable?) {
                    handleImeText(s?.toString() ?: "")
                }
            })
            setOnKeyListener { _, code, ev ->
                if (ev.action == KeyEvent.ACTION_DOWN) {
                    when (code) {
                        KeyEvent.KEYCODE_DEL -> { view.sendBackspace(); true }
                        KeyEvent.KEYCODE_ENTER -> { view.sendEnter(); true }
                        else -> false
                    }
                } else false
            }
        }

        val root = FrameLayout(this)
        root.addView(view, FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.addView(ime, FrameLayout.LayoutParams(1, 1).apply {
            gravity = Gravity.BOTTOM or Gravity.START
        })
        setContentView(root)

        kernel = Kernel(host)

        view.requestFocus()
    }

    /** 首次触摸屏幕后唤起软键盘，让用户可以往终端里打字 */
    private fun ensureIme() {
        if (ime.hasFocus()) return
        ime.requestFocus()
        val imm = getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager
        imm.showSoftInput(ime, android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT)
    }

    /**
     * 内核主循环 —— 这是 NovaDesk 的调度器。
     * 跑在独立线程上，与安卓 UI 线程解耦。
     */
    private fun startKernelLoop() {
        if (running) return
        running = true
        loop = Thread({
            while (running) {
                val t0 = System.nanoTime()
                try {
                    kernel.tick()
                    view.renderFrame()
                } catch (t: Throwable) {
                    android.util.Log.e("NovaDesk", "kernel loop error", t)
                }
                // 目标 ~60fps；余量交给系统
                val elapsed = (System.nanoTime() - t0) / 1_000_000L
                val sleep = 16L - elapsed
                if (sleep > 0) {
                    try { Thread.sleep(sleep) } catch (_: InterruptedException) {}
                }
            }
        }, "novadesk-kernel").apply {
            priority = Thread.NORM_PRIORITY + 1
            start()
        }
    }

    private fun handleImeText(s: String) {
        // EditText 每次都是全量文本，我们只取出新增部分
        if (s.length > lastImeLen) {
            for (i in lastImeLen until s.length) {
                val c = s[i]
                if (c != '\n') view.sendChar(c)
            }
        } else if (s.length < lastImeLen) {
            // 退格
            repeat(lastImeLen - s.length) { view.sendBackspace() }
        }
        lastImeLen = s.length
        // 定期清空，避免无限增长
        if (s.length > 64) {
            ime.setText("")
            lastImeLen = 0
        }
    }

    private fun hideSystemBars() {
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            )
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    override fun onResume() {
        super.onResume()
        startKernelLoop()
        // 进系统后自动把键盘叫出来，NovaDesk 的终端是主界面
        view.postDelayed({ ensureIme() }, 600)
    }

    override fun onPause() {
        super.onPause()
        running = false
        loop = null
    }

    override fun onBackPressed() {
        // 返回键不是退出 App，而是交给 NovaDesk 内核当"关闭窗口"用
        view.sendBack()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        when (keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> { view.sendChar('+'); return true }
            KeyEvent.KEYCODE_VOLUME_DOWN -> { view.sendChar('-'); return true }
        }
        return super.onKeyDown(keyCode, event)
    }
}

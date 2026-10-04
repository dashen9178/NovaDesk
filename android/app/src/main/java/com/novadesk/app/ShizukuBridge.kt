package com.novadesk.app

import android.content.pm.PackageManager
import android.os.Build
import android.os.ParcelFileDescriptor
import android.webkit.JavascriptInterface
import android.webkit.WebView
import moe.shizuku.server.IRemoteProcess
import moe.shizuku.server.IShizukuService
import org.json.JSONObject
import rikka.shizuku.Shizuku

/**
 * ADB 权限桥（走 Shizuku）。
 *
 * ## 为什么需要它
 *
 * 安卓**不允许应用自己给自己开 ADB 权限** —— 必须有东西从外面把 shell 身份递进来。
 * Shizuku 就干这个：它自己通过「无线调试」或数据线拿到 shell（uid 2000）身份，
 * 再通过 binder 把这份权限转给别的应用。所以这条路必须用户先装 Shizuku、
 * 再手动授权一次，重启手机后要重新激活。
 *
 * ## 拿到之后能干什么
 *
 * 本质上就是**以 shell 身份执行命令**，比"文件读写"大得多：
 *   · 装/卸载应用、给别的应用授权或撤销、强制停止、冻结
 *   · 截屏、模拟点击/滑动/输入
 *   · 读写系统设置（亮度、音量、自动旋转…）
 *   · 列出已安装应用、看谁在后台跑
 *
 * ## 闸门
 *
 * 这个能力很重，所以：
 *   1. **必须用户明确授权**（Shizuku 自己弹的那个框，不是我们说了算）
 *   2. **每次调用都重新查一遍授权状态** —— 用户随时可能在 Shizuku 里撤销
 *   3. 输出限长，避免一条命令把内存吃光
 *   4. 有超时，避免 `logcat` 这类不会结束的命令把线程挂死
 */
class ShizukuBridge(
    private val web: WebView,
    private val activity: MainActivity
) {

    companion object {
        const val REQ_SHIZUKU = 0x9B01

        /** 单次最多回传多少字符 */
        private const val MAX_OUT = 200_000

        /** 命令最长跑多久 */
        private const val TIMEOUT_MS = 20_000L
    }

    /* ============================================================ *
     * 状态
     * ============================================================ */

    private fun available(): Boolean = try {
        Shizuku.pingBinder()
    } catch (e: Throwable) {
        false
    }

    private fun granted(): Boolean {
        return try {
            if (!available()) return false
            // Shizuku 11 之前用的是另一套权限模型
            if (Shizuku.isPreV11()) {
                activity.checkSelfPermission("moe.shizuku.manager.permission.API_V23") ==
                    PackageManager.PERMISSION_GRANTED
            } else {
                Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
            }
        } catch (e: Throwable) {
            false
        }
    }

    @JavascriptInterface
    fun info(): String = JSONObject().apply {
        put("available", available())
        put("granted", granted())
        put("version", try { if (available()) Shizuku.getVersion() else 0 } catch (e: Throwable) { 0 })
        put("sdk", Build.VERSION.SDK_INT)
    }.toString()

    private fun pushState() {
        val payload = info()
        web.post {
            try {
                web.evaluateJavascript(
                    "window.__novaShizukuChanged && window.__novaShizukuChanged(" +
                        payload + ");", null
                )
            } catch (_: Throwable) { }
        }
    }

    private fun pushError(msg: String) {
        web.post {
            try {
                web.evaluateJavascript(
                    "window.__novaShizukuChanged && window.__novaShizukuChanged(null, " +
                        JSONObject.quote(msg) + ");", null
                )
            } catch (_: Throwable) { }
        }
    }

    /* ============================================================ *
     * 申请权限
     * ============================================================ */

    @JavascriptInterface
    fun request() {
        activity.runOnUiThread {
            try {
                if (!available()) {
                    pushError(
                        "没检测到 Shizuku。要先装 Shizuku，再用「无线调试」启动它一次" +
                            "（手机自己就能做，不用连电脑）。"
                    )
                    return@runOnUiThread
                }
                if (granted()) {
                    pushState()
                    return@runOnUiThread
                }
                Shizuku.requestPermission(REQ_SHIZUKU)
            } catch (e: Throwable) {
                pushError("申请 ADB 权限失败：" + (e.message ?: e.javaClass.simpleName))
            }
        }
    }

    /** MainActivity 收到 Shizuku 的授权结果后调这里 */
    fun onPermissionResult() {
        pushState()
    }

    /* ============================================================ *
     * 执行命令
     * ============================================================ */

    /**
     * 以 shell 身份跑一条命令。
     *
     * ★ 必须**异步**：@JavascriptInterface 的方法虽然跑在 JavaBridge 线程上，
     *   但 JS 调用它是**同步等待**的 —— 一条跑 20 秒的命令会把整个页面冻住。
     *   所以这里立刻返回，干完再通过 window.__novaShizukuDone 推回去。
     *
     * 实现走的是 Shizuku 的底层进程接口：
     *   Shizuku.getBinder() -> IShizukuService.newProcess() -> IRemoteProcess
     * （Shizuku 13 里 `Shizuku.newProcess` 是私有的，只能这么来。）
     */
    @JavascriptInterface
    fun shell(callId: String, cmd: String) {
        Thread {
            val payload = exec(cmd).toString()
            web.post {
                try {
                    web.evaluateJavascript(
                        "window.__novaShizukuDone && window.__novaShizukuDone(" +
                            JSONObject.quote(callId) + "," + payload + ");", null
                    )
                } catch (_: Throwable) { }
            }
        }.start()
    }

    private fun exec(cmd: String): JSONObject {
        val res = JSONObject()
        if (!granted()) {
            return res.put("ok", false)
                .put("error", "还没有 ADB 权限。要用户在界面上点一下授权。")
        }
        if (cmd.isBlank()) {
            return res.put("ok", false).put("error", "命令是空的")
        }

        var proc: IRemoteProcess? = null
        try {
            val svc = IShizukuService.Stub.asInterface(Shizuku.getBinder())
                ?: return res.put("ok", false).put("error", "连不上 Shizuku 服务")
            proc = svc.newProcess(arrayOf("sh", "-c", cmd), null, null)
                ?: return res.put("ok", false).put("error", "Shizuku 没能起进程")

            val outBuf = StringBuilder()
            val errBuf = StringBuilder()

            // stdout/stderr 必须**同时**读：只读一路的话，另一路的管道缓冲区
            // 一满，对面就卡在 write 上不走了，等待永远不会结束。
            val p = proc
            val t1 = Thread {
                try { drain(ParcelFileDescriptor.AutoCloseInputStream(p.getInputStream()), outBuf) }
                catch (_: Throwable) { }
            }
            val t2 = Thread {
                try { drain(ParcelFileDescriptor.AutoCloseInputStream(p.getErrorStream()), errBuf) }
                catch (_: Throwable) { }
            }
            t1.isDaemon = true
            t2.isDaemon = true
            t1.start()
            t2.start()

            val finished = try {
                p.waitForTimeout(TIMEOUT_MS, "MILLISECONDS")
            } catch (e: Throwable) {
                try { p.waitFor(); true } catch (e2: Throwable) { false }
            }
            if (!finished) {
                // 超时（比如让它跑 logcat）：干掉它，别把线程挂死
                try { p.destroy() } catch (_: Throwable) { }
            }
            t1.join(1500)
            t2.join(1500)

            res.put("ok", true)
            res.put("code", if (finished) (try { p.exitValue() } catch (e: Throwable) { -1 }) else -1)
            res.put("out", outBuf.toString())
            res.put("err", errBuf.toString())
            res.put("timeout", !finished)
        } catch (e: Throwable) {
            res.put("ok", false)
            res.put("error", e.javaClass.simpleName + ": " + (e.message ?: ""))
        } finally {
            try { proc?.destroy() } catch (_: Throwable) { }
        }
        return res
    }

    /** 读一路输出，读满上限就停 */
    private fun drain(input: java.io.InputStream, sb: StringBuilder) {
        try {
            java.io.BufferedReader(java.io.InputStreamReader(input, Charsets.UTF_8)).use { br ->
                val buf = CharArray(4096)
                while (sb.length < MAX_OUT) {
                    val n = br.read(buf)
                    if (n <= 0) break
                    val room = MAX_OUT - sb.length
                    sb.append(buf, 0, if (n > room) room else n)
                }
            }
        } catch (_: Throwable) { }
    }
}

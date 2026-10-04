package com.novadesk.app

import android.os.Build
import android.webkit.JavascriptInterface
import android.webkit.WebView
import org.json.JSONObject

/**
 * 导出桥：把 AI 做出来的东西打成 APK。
 *
 * ★ 这里所有"慢"方法都是**异步**的（立刻返回、干完活再推结果回页面）。
 *   原因是 @JavascriptInterface 的方法由 JavaBridge 线程执行，而 JS 调用它是**同步等待**的 ——
 *   打包要 RSA 生成密钥、压缩、签名，好几秒；同步做的话整个界面会卡死，
 *   用户会以为应用挂了。
 */
class ApkBridge(
    private val web: WebView,
    private val activity: MainActivity
) {

    private fun push(payload: String) {
        web.post {
            try {
                web.evaluateJavascript(
                    "window.__novaApkDone && window.__novaApkDone(" +
                        JSONObject.quote(payload) + ");", null
                )
            } catch (_: Throwable) { }
        }
    }

    /** 页面开机时问一次：导出功能能不能用、签名身份有没有 */
    @JavascriptInterface
    fun info(): String = JSONObject().apply {
        put("available", true)
        put("hasKey", DeviceKey.hasKey(activity))
        put("sdk", Build.VERSION.SDK_INT)
    }.toString()

    /**
     * 导出。appName = 应用名，html = 完整的单页 HTML，version = 版本号。
     * 干完会调 window.__novaApkDone({...})。
     */
    @JavascriptInterface
    fun export(appName: String, html: String, version: String) {
        Thread {
            val r = ApkExporter.export(activity, appName, html, version)
            val payload = JSONObject().apply {
                put("ok", r.ok)
                put("path", r.path ?: "")
                put("size", r.size)
                put("published", r.published ?: "")
                put("error", r.error ?: "")
            }.toString()
            push(payload)
        }.start()
    }

    /** 把生成好的包装上（调系统安装器）。结果同样走 __novaApkDone */
    @JavascriptInterface
    fun install(path: String) {
        Thread {
            val err = ApkExporter.install(activity, path)
            val payload = JSONObject().apply {
                put("ok", err == null)
                put("action", "install")
                put("error", err ?: "")
            }.toString()
            push(payload)
        }.start()
    }

    /**
     * 重新生成设备签名身份。
     *
     * ★ 这个方法**不暴露给页面**（原来是个 @JavascriptInterface，已删）。
     *   它没有调用方 —— grep 全工程只有"处理返回值"的一句，没有任何地方真的调它。
     *   而它是**不可逆**的：删掉签名密钥之后，此前导出的所有作品都无法再覆盖更新，
     *   用户只能先卸载旧版再装新的（数据全丢）。
     *   一个既有破坏性、又没人用的接口，留着只会被 AI 写的面板脚本顺手调到。
     *   真要重置，让用户在系统里清应用数据即可。
     */
    @Suppress("unused")
    private fun resetDeviceKey() {
        DeviceKey.reset(activity)
    }
}

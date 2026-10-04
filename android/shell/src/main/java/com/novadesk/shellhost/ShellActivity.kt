package com.novadesk.shellhost

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout

/**
 * 导出壳的主界面。
 *
 * 它只干一件事：把 assets/app.html 铺满屏幕。
 * 用户导出的东西就是这个 app.html。
 *
 * ★ 这个类**必须**留在 com.novadesk.shellhost 这个包里。
 *   导出流程会把清单里的 applicationId 改写成 com.novadesk.rt.<哈希>，
 *   而清单里 activity 的 android:name 写的是本类的完整名字，
 *   两者字符串不同 —— 所以改写不会波及它，改完还能找到这个类。
 *   详细原因见 shell/build.gradle.kts 里的注释。
 */
class ShellActivity : Activity() {

    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = FrameLayout(this)
        root.setBackgroundColor(0xFFFEF7FF.toInt())

        web = WebView(this)
        root.addView(
            web,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        )
        setContentView(root)

        val s = web.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true          // AI 做的东西要能存数据
        s.databaseEnabled = true
        s.useWideViewPort = true
        s.loadWithOverviewMode = false
        s.mediaPlaybackRequiresUserGesture = false
        s.setSupportZoom(false)
        s.builtInZoomControls = false
        // 页面是给手指用的，别让系统字体缩放把排好的版撑乱
        s.textZoom = 100

        // ★ 刻意**不**开 allowFileAccessFromFileURLs / allowUniversalAccessFromFileURLs。
        //
        //   导出的应用里根本没有文件桥（页面收到的 Nova 替身，七个 file 方法
        //   一律返回 {ok:false}），所以它不需要任何 file:// 跨源能力。
        //   而 targetSdk 34 只在 Android 10+ 忽略这两个开关 ——
        //   在 Android 8/9/10 上它们**真实生效**，页面里的脚本就能
        //   fetch('file:///…') 直读任意本地文件，和"这个导出的应用读不了
        //   手机文件"的承诺正相反。这个包是要发给别人装的，更不能留。
        //
        //   （宿主 NovaDesk 那边保留了 allowUniversalAccessFromFileURLs ——
        //     对话直连 DeepSeek 靠它，那是另一回事。）

        // 不外跳：AI 做的东西里的链接也在自己这个 WebView 里开
        web.webViewClient = WebViewClient()

        web.loadUrl("file:///android_asset/app.html")
    }

    /** 返回键：能后退就后退，退到头了才退出应用 */
    @Deprecated("Deprecated in API 33, still the simplest hook for WebView back")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else finish()
    }
}

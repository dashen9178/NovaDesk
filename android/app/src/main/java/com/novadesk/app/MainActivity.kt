package com.novadesk.app

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.content.pm.ActivityInfo
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.ConsoleMessage
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import org.json.JSONObject
import rikka.shizuku.Shizuku
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL
import java.util.zip.GZIPInputStream

/**
 * 原生桥。
 *
 * 两件事：
 *   1. 联网（浏览器跨域做不了，原生没这个限制）
 *   2. **真正改设备状态** —— 屏幕方向、常亮、全屏。
 *      这一块很重要：AI 光"说"要改是没用的，得有真的入口。
 *
 * 约定：JS 调 NovaNative.xxx(...)，需要回结果的走 window.__novaNetDone。
 */
class NetBridge(
    private val web: WebView,
    private val activity: MainActivity
) {

    /** 屏幕方向：portrait / landscape / auto */
    @JavascriptInterface
    fun setOrientation(mode: String) {
        val o = when (mode.lowercase()) {
            "landscape" -> ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            "portrait" -> ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
            else -> ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
        activity.runOnUiThread { activity.requestedOrientation = o }
    }

    /** 屏幕常亮：on / off */
    @JavascriptInterface
    fun setKeepAwake(on: String) {
        activity.runOnUiThread {
            if (on == "on") {
                activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            } else {
                activity.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
        }
    }

    /** 全屏沉浸：on / off */
    @JavascriptInterface
    fun setFullscreen(on: String) {
        activity.runOnUiThread {
            if (on == "off") activity.exitImmersive() else activity.goImmersive()
        }
    }

    /** 读一下当前真实的设备/系统信息，别让 AI 瞎猜 */
    @JavascriptInterface
    fun info(): String {
        // 版本号从系统读，别写死 —— 写死的话 gradle 一改版本，
        // AI 报给用户的版本号就是错的
        val vName = try {
            val pi = activity.packageManager.getPackageInfo(activity.packageName, 0)
            pi.versionName ?: ""
        } catch (e: Exception) { "" }

        return JSONObject().apply {
            put("app", "NovaDesk")
            put("versionName", vName)
            put("androidRelease", Build.VERSION.RELEASE)
            put("androidSdk", Build.VERSION.SDK_INT)
            put("model", Build.MODEL)
            put("manufacturer", Build.MANUFACTURER)
            put("orientation", activity.resources.configuration.orientation)
            put("immersive", activity.immersiveWanted())
        }.toString()
    }

    /**
     * 用系统的方式打开一个链接（交给浏览器 / 下载器）。
     *
     * 为什么不能直接让 WebView 去下：WebView 里点 APK 链接**什么都不会发生**
     * （它不会下载，也没有安装入口）。检查更新给的下载按钮要是走 WebView，
     * 用户点了就像按了个坏按钮 —— 这正是这个项目反复强调不许出现的东西。
     *
     * 只放行 http(s)：Intent 能被任意 scheme 唤起，model 写的东西不该有这个机会。
     */
    @JavascriptInterface
    fun openUrl(url: String) {
        val u = url.trim()
        if (!u.startsWith("http://") && !u.startsWith("https://")) return
        activity.runOnUiThread {
            try {
                activity.startActivity(
                    Intent(Intent.ACTION_VIEW, Uri.parse(u))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            } catch (e: Exception) {
                Toast.makeText(activity, "这台设备打不开这个链接", Toast.LENGTH_LONG).show()
            }
        }
    }

    @JavascriptInterface
    fun get(callId: String, url: String) {
        doGet(callId, url, emptyMap())
    }

    /**
     * 带请求头的 GET。
     *
     * 存在的理由：SeaTable 的行读取接口（它的 API 网关）响应里
     * Access-Control-Allow-Origin **发了两遍**，WebView 按规范判成非法，
     * 页面直接 "Failed to fetch" —— 而同一地址用命令行是 200。
     * 页面无解，只能借原生绕开 CORS。
     *
     * 头用白名单卡死：这个口子是给"读云端更新表"用的，不是通用转发器。
     * 放任页面自己塞头的话，它能伪造 User-Agent / Cookie 之类，
     * 那就等于把整个 WebView 的会话借出去了。
     */
    @JavascriptInterface
    fun getH(callId: String, url: String, headerLines: String) {
        val allowed = setOf("authorization", "accept", "content-type", "token", "devicetype")
        val extra = HashMap<String, String>()
        // 一行一个 "Name: Value"（桌面那边直接喂 WinHTTP，所以格式统一成这个）
        for (line in headerLines.split('\n')) {
            val i = line.indexOf(':')
            if (i <= 0) continue
            val name = line.substring(0, i).trim()
            val value = line.substring(i + 1).trim()
            if (name.isEmpty() || value.isEmpty()) continue
            if (allowed.contains(name.lowercase())) extra[name] = value
        }
        doGet(callId, url, extra)
    }

    private fun doGet(callId: String, url: String, extra: Map<String, String>) {
        Thread {
            var ok = false
            var status = 0
            var body = ""
            var error = ""
            var conn: HttpURLConnection? = null
            try {
                // 只允许 http/https。
                // 不校验的话，页面能通过这个桥请求 file:// 之类的协议去读本地文件 ——
                // 那是实打实的越权，而联网本身完全不需要这些协议。
                val scheme = try { URL(url).protocol.lowercase() } catch (e: Exception) { "" }
                if (scheme != "http" && scheme != "https") {
                    error = "只允许 http/https，已拒绝：$scheme"
                } else if (!isPublicHost(url)) {
                    // 挡内网/本机：这个桥能把**响应正文**原样回给页面，
                    // 放任的话 AI 写的面板脚本就能拿它去扫内网
                    //（路由器后台、NAS、169.254.169.254 这类元数据地址）。
                    error = "只允许公网地址：内网/本机地址已被拒绝"
                } else {
                    // 自己跟跳转，每一跳都重做一次公网检查 ——
                    // 交给 HttpURLConnection 自动跳的话，
                    // 一个 302 到 http://192.168.1.1/ 就绕过了上面的检查。
                    var current = url
                    var hops = 0
                    while (true) {
                        if (!isPublicHost(current)) {
                            error = "跳转目标不是公网地址，已停止：$current"
                            break
                        }
                        val u = URL(current)
                        conn = (u.openConnection() as HttpURLConnection).apply {
                            connectTimeout = 15000
                            readTimeout = 25000
                            instanceFollowRedirects = false
                            requestMethod = "GET"
                            setRequestProperty(
                                "User-Agent",
                                "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 " +
                                    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
                            )
                            setRequestProperty(
                                "Accept",
                                "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"
                            )
                            setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
                            setRequestProperty("Accept-Encoding", "gzip")
                            // 调用方指定的头（白名单在 getH 里卡过）。
                            //
                            // ★ 只在**第一跳**带：带了 Authorization 的请求一旦被
                            //   302 到别的域名，跟着跳就等于把 token 送给第三方。
                            //   而且明文 http 一律不带 —— 那是在网上裸奔。
                            if (hops == 0 && u.protocol == "https") {
                                for ((k, v) in extra) {
                                    try { setRequestProperty(k, v) } catch (_: Exception) {}
                                }
                            }
                        }
                        status = conn.responseCode

                        if (status in 300..399 && hops < 3) {
                            val loc = conn.getHeaderField("Location")
                            if (!loc.isNullOrEmpty()) {
                                current = URL(u, loc).toString()
                                hops++
                                try { conn.disconnect() } catch (_: Exception) {}
                                conn = null
                                continue
                            }
                        }

                        ok = status in 200..299
                        val raw = if (ok) conn.inputStream else conn.errorStream
                        if (raw != null) {
                            val enc = conn.contentEncoding
                            // ★ 边读边数，到上限就停。
                            //   不能先 readText() 整份读进来再截断 —— 几百 MB 的
                            //   响应直接 OutOfMemoryError，而它是 Error 不是
                            //   Exception，外层 catch 抓不住，App 当场崩。
                            body = readCapped(
                                if (enc != null && enc.contains("gzip", true)) {
                                    GZIPInputStream(raw)
                                } else {
                                    raw
                                },
                                MAX_BODY_CHARS
                            )
                        }
                        if (!ok) error = "HTTP $status"
                        break
                    }
                }
            } catch (e: Exception) {
                error = e.javaClass.simpleName + ": " + (e.message ?: "")
            } finally {
                try { conn?.disconnect() } catch (_: Exception) {}
            }

            // 太大就别整段回传，防止把 WebView 撑爆（readCapped 已经限过了，
            // 这里只是双保险）
            if (body.length > 600_000) body = body.substring(0, 600_000)

            val payload = JSONObject().apply {
                put("ok", ok)
                put("status", status)
                put("body", body)
                put("error", error)
            }
            val js = "window.__novaNetDone(" + JSONObject.quote(callId) + "," + payload + ");"
            web.post { try { web.evaluateJavascript(js, null) } catch (_: Exception) {} }
        }.start()
    }

    /**
     * 这个网址指向的是不是公网。
     *
     * 必须挡：这个桥会把**响应正文**原样回给页面，放任的话 AI 写的面板脚本
     * 能拿它去扫内网（路由器后台、NAS、以及云上那个著名的
     * 169.254.169.254 元数据地址）。
     *
     * 解析出来的**所有**地址都得是公网才算通过 —— 只验第一个的话，
     * 一条 A 记录指公网、一条指内网的域名就能绕过去。
     */
    private fun isPublicHost(url: String): Boolean {
        return try {
            val host = URL(url).host
            if (host.isNullOrEmpty()) {
                false
            } else {
                val h = host.lowercase()
                if (h == "localhost" || h.endsWith(".localhost") || h.endsWith(".local") ||
                    h.endsWith(".internal") || h.endsWith(".home.arpa")
                ) {
                    false
                } else {
                    val addrs = InetAddress.getAllByName(host)
                    addrs.isNotEmpty() && addrs.all { a ->
                        !(a.isLoopbackAddress || a.isAnyLocalAddress || a.isLinkLocalAddress ||
                            a.isSiteLocalAddress || a.isMulticastAddress ||
                            isCarrierNat(a) || isUniqueLocalV6(a))
                    }
                }
            }
        } catch (e: Exception) {
            false
        }
    }

    /** 100.64.0.0/10 —— 运营商级 NAT，既不是公网也不是标准私网 */
    private fun isCarrierNat(a: InetAddress): Boolean {
        val b = a.address
        return b.size == 4 && (b[0].toInt() and 0xFF) == 100 &&
            (b[1].toInt() and 0xFF) in 64..127
    }

    /** fc00::/7 —— IPv6 唯一本地地址 */
    private fun isUniqueLocalV6(a: InetAddress): Boolean {
        val b = a.address
        return b.size == 16 && (b[0].toInt() and 0xFE) == 0xFC
    }

    private companion object {
        /** 单次最多回传多少字符 */
        const val MAX_BODY_CHARS = 600_000
    }
}

/**
 * 从流里读最多 max 个字符就停。
 *
 * 为什么不能直接 `readText()` 再截断：那要求先把整份读进内存。
 * 遇到一个几百 MB 的响应（或者一个慢速无限流）就是 OutOfMemoryError，
 * 而它继承自 Error 不是 Exception，`catch (e: Exception)` 根本抓不住。
 */
private fun readCapped(input: java.io.InputStream, max: Int): String {
    val sb = StringBuilder()
    val buf = CharArray(8192)
    java.io.BufferedReader(java.io.InputStreamReader(input, Charsets.UTF_8)).use { br ->
        while (true) {
            val n = br.read(buf)
            if (n <= 0) break
            if (sb.length + n >= max) {
                sb.append(buf, 0, max - sb.length)
                break
            }
            sb.append(buf, 0, n)
        }
    }
    return sb.toString()
}

/**
 * NovaDesk 的安卓壳。
 *
 * 它只做四件事：
 *   1. 横屏 + 全屏沉浸（不显示状态栏和导航栏）
 *   2. 一个 WebView 铺满，加载 assets 里的 NovaDesk.html
 *   3. 把原生联网能力暴露给页面（绕开跨域）
 *   4. 处理刘海/挖孔和软键盘，别让输入框被盖住
 *
 * 界面本身完全在 H5 里，这里不做任何 UI。
 *
 * 注意：这里继承的是**平台 Activity**，不是 AppCompatActivity。
 * AppCompatActivity 会强制校验主题必须是 Theme.AppCompat.*，
 * 用平台主题启动时直接抛 IllegalStateException（一点就闪退）。
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private lateinit var root: FrameLayout

    /** 页面底色（M3 surface #FEF7FF）。窗口底、WebView 底都用它，避免露白 */
    private val surfaceColor = 0xFFFEF7FF.toInt()

    /**
     * 现在是不是沉浸（系统栏藏起来）状态。
     *
     * 这个标志决定"要不要给系统栏让位"，是顶部那条白带的根治点：
     * 沉浸时系统栏已经藏了，但不少 ROM 仍然把它的高度报进 insets，
     * 照单全收就会在顶部垫出一条 —— 看上去就是"通知栏位置一片白"。
     */
    private var immersive = true

    /**
     * 用户（或 AI）**想要**的效果。
     *
     * 必须和"当前状态"分开记：焦点变化、onResume 都会重新铺一次沉浸，
     * 只看当前状态的话，AI 调 device("fullscreen","off") 之后会被立刻
     * 打回沉浸态 —— 表现就是"它说已经显示状态栏了，可根本没变"。
     */
    /**
     * 要不要沉浸（藏掉状态栏）。
     * **默认 false** —— 用户要看得见通知栏。prefs 里读，见 onCreate。
     */
    private var wantImmersive = false

    /** 沉浸偏好要落盘，重启之后不丢 */
    private val prefs by lazy { getSharedPreferences("novadesk.ui", MODE_PRIVATE) }

    /** NetBridge.info() 要把它报给页面，所以不能是 private */
    fun immersiveWanted(): Boolean = wantImmersive

    /** 文件桥。选文件夹 / 请权限都要回到 Activity 拿结果，所以得留个引用 */
    private lateinit var fileBridge: FileBridge

    /** 页面里 <input type=file> 弹出的选择器，选完要回填给 WebView */
    private var pendingFileCallback: ValueCallback<Array<Uri>>? = null

    /** ADB 权限桥（Shizuku） */
    private lateinit var shizukuBridge: ShizukuBridge

    /** Shizuku 授权结果的监听器，退出时要摘掉，不然泄漏 Activity */
    private var shizukuListener: Shizuku.OnRequestPermissionResultListener? = null

    private companion object {
        const val REQ_FILE_CHOOSER = 0x9A10
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 刘海屏也铺满（内容稍后按 insets 让开）
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes.layoutInDisplayCutoutMode =
                android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }

        root = FrameLayout(this)
        root.setBackgroundColor(surfaceColor)   // 用页面底色，露出来也看不出来

        web = WebView(this)
        root.addView(
            web,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
        )
        setContentView(root)

        // 系统栏万一被临时唤出（边缘上滑），底色也得是页面色，不能是白的
        @Suppress("DEPRECATION")
        run {
            window.statusBarColor = surfaceColor
            window.navigationBarColor = surfaceColor
        }
        // WebView 自己默认是白底，加载完第一帧之前会闪一下白
        web.setBackgroundColor(surfaceColor)

        val s = web.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true               // 存档要用 localStorage
        s.databaseEnabled = true
        s.cacheMode = WebSettings.LOAD_DEFAULT
        s.useWideViewPort = true
        s.loadWithOverviewMode = false
        s.mediaPlaybackRequiresUserGesture = false
        s.setSupportZoom(false)
        s.builtInZoomControls = false
        // 页面是给手指用的，别让系统字体缩放把它撑乱
        s.textZoom = 100

        // file:// 页面里的 fetch https://api.deepseek.com 会被同源策略拦掉。
        //
        // ★ 只开 allowUniversalAccessFromFileURLs，**不开**
        //   allowFileAccessFromFileURLs。
        //   前者是"让 file:// 页面能跨源发请求"—— 对话走 DeepSeek 靠它；
        //   后者是"让 file:// 页面能读别的 file:// 文件"，联网完全不需要，
        //   纯粹是给 AI 写的面板脚本开一条绕过"文件权限三档"去直读本地文件的路。
        //   桌面版那边我实测过：去掉 file 访问开关之后 API 照常连通。
        @Suppress("DEPRECATION")
        run {
            s.allowUniversalAccessFromFileURLs = true
            s.allowFileAccessFromFileURLs = false
        }

        // 原生联网桥：AI 的联网搜索 / 打开网页走这里，绕开浏览器跨域限制
        web.addJavascriptInterface(NetBridge(web, this), "NovaNative")

        // 文件桥：让 AI 真的能读写手机文件（权限档位由用户在界面上选）
        fileBridge = FileBridge(web, this)
        web.addJavascriptInterface(fileBridge, "NovaFiles")

        // 导出桥：把 AI 做出来的东西打成能装的 APK
        web.addJavascriptInterface(ApkBridge(web, this), "NovaApk")

        // Shizuku 桥：拿"ADB 权限"（shell 身份），之后的装应用/授权/截屏/
        // 模拟点击/改系统设置全走它
        shizukuBridge = ShizukuBridge(web, this)
        web.addJavascriptInterface(shizukuBridge, "NovaShizuku")

        // Shizuku 授权结果回来要通知页面（用户在 Shizuku 自己那个框里点的）
        try {
            val l = Shizuku.OnRequestPermissionResultListener { requestCode, grantResult ->
                if (requestCode == ShizukuBridge.REQ_SHIZUKU) {
                    if (grantResult != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                        android.util.Log.w("NovaDesk", "用户拒绝了 ADB 权限")
                    }
                    if (::shizukuBridge.isInitialized) shizukuBridge.onPermissionResult()
                }
            }
            shizukuListener = l
            Shizuku.addRequestPermissionResultListener(l)
        } catch (e: Throwable) {
            android.util.Log.w("NovaDesk", "注册 Shizuku 回调失败（大概没装 Shizuku）")
        }

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView, request: WebResourceRequest
            ): Boolean {
                // 只放行 http/https，其余（file://、content://、自定义 scheme）**一律拦掉**。
                //
                // ★ 之前写的是 return false —— 那等于让 WebView **自己加载**任意
                //   file:// 或 content:// 页面。而这个 WebView 上挂着三个原生桥
                //   （文件 / 联网 / 打包安装），去加载一个 AI 能写出来的本地 HTML
                //   等于把桥拱手送人；file:// 还是同源，连 localStorage 里的
                //   API key 都能一起读走。
                val u = request.url
                val scheme = (u.scheme ?: "").lowercase()
                if (scheme == "http" || scheme == "https") {
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, u))
                    } catch (_: Exception) { }
                    return true
                }
                // 唯一例外：我们自己打包在 assets 里的资源
                if (scheme == "file" && u.toString().startsWith("file:///android_asset/")) {
                    return false
                }
                return true
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(m: ConsoleMessage): Boolean {
                android.util.Log.d("NovaDesk", "[web] ${m.message()} @${m.lineNumber()}")
                return true
            }

            /**
             * 页面里的 <input type="file"> 点下去会走到这里。
             *
             * 安卓 WebView **默认什么都不做** —— 不实现这个方法的话，
             * 用户点了回形针就像按了个坏按钮，一点反应都没有。
             * 浏览器和桌面版（WebView2）都是自动弹系统文件框的，
             * 只有这里得自己接。
             */
            override fun onShowFileChooser(
                view: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                // 上一次的选择还没回结果就又点了：先把上一个回调作废。
                // 不然 JS 那边会永远挂着，页面上的选择框从此再也不响应。
                pendingFileCallback?.onReceiveValue(null)
                pendingFileCallback = filePathCallback

                return try {
                    val intent = fileChooserParams?.createIntent()
                        ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                            type = "*/*"
                            addCategory(Intent.CATEGORY_OPENABLE)
                        }
                    startActivityForResult(intent, REQ_FILE_CHOOSER)
                    true
                } catch (e: Exception) {
                    pendingFileCallback = null
                    false
                }
            }
        }

        // 保持全屏：从后台回来、或者软键盘收起后，系统栏可能又冒出来
        web.setOnFocusChangeListener { _, _ -> if (wantImmersive) goImmersive() }

        // 沉浸偏好是上次留下的：关过全屏的话，这次开机就别自动藏系统栏
        // ★ 默认**显示**手机的通知栏（状态栏），不再一上来就全屏沉浸。
        //   用户的原话：「把手机通知栏区域显示」—— 他要看得见时间/电量。
        //   想全屏仍然可以，跟 AI 说"全屏显示"就行（device 工具，会调 goImmersive）。
        //
        //   为什么换了个 key（showStatusBar 而不是继续用 immersive）：
        //   老版本默认写进去的是 immersive=true，那批用户升级后
        //   永远拿不到新默认值 —— 换 key 等于让新默认对所有人生效一次。
        wantImmersive = !prefs.getBoolean("showStatusBar", true)
        immersive = wantImmersive

        applyInsets()
        if (wantImmersive) goImmersive() else exitImmersive()

        // 调试口只在 debuggable 构建里开。
        // 常开的话，任何能连到这台设备的东西（包括本机其它应用）都能用 CDP
        // 接管页面 —— 读走 localStorage 里的 API key、直接调文件桥。
        val debuggable = (applicationInfo.flags and
            android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
        if (debuggable) {
            WebView.setWebContentsDebuggingEnabled(true)
        }

        web.loadUrl("file:///android_asset/NovaDesk.html")
    }

    /** 全屏沉浸：隐藏状态栏和导航栏，允许边缘上滑临时唤出 */
    fun goImmersive() {
        wantImmersive = true
        immersive = true
        prefs.edit().putBoolean("showStatusBar", false).apply()
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val c = WindowInsetsControllerCompat(window, window.decorView)
        c.hide(WindowInsetsCompat.Type.systemBars())
        c.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            )
        // 系统栏藏起来之后要重新算一次内边距 —— 否则会留着"给栏让位"的旧 padding
        root.requestApplyInsets()
    }

    /** 退出沉浸，把状态栏和导航栏放回来（AI 可以要求） */
    fun exitImmersive() {
        wantImmersive = false
        immersive = false
        prefs.edit().putBoolean("showStatusBar", true).apply()
        WindowCompat.setDecorFitsSystemWindows(window, true)
        val c = WindowInsetsControllerCompat(window, window.decorView)
        c.show(WindowInsetsCompat.Type.systemBars())
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_VISIBLE
        root.requestApplyInsets()
    }

    /**
     * 让开刘海和系统栏。
     *
     * 用户的原话：「我让你解决的是顶部那个白色区域，你直接关掉就行了啊」——
     * 那条白色的东西就是这里给刘海让位垫出来的 `surfaceColor`。
     * 沉浸模式下系统栏本来就是藏起来的，页面从 y=0 开始画，再垫一条就是纯多余。
     *
     * 所以规则简化成一条：
     *   · **沉浸模式：上下都不垫**（真·满屏，页面自己用 safe-area 处理内容位置）
     *   · 非沉浸模式：系统栏露着，那就得让位，不然内容被时间/电量压住
     *
     * 只保留左右两边的挖孔让位：横屏时摄像头在侧边，那是真的会挡住字。
     */
    private fun applyInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val cut = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            var l = cut.left
            var t = 0
            var r = cut.right
            var b = 0
            if (!immersive) {
                val sys = insets.getInsets(WindowInsetsCompat.Type.systemBars())
                l = maxOf(l, sys.left)
                t = sys.top
                r = maxOf(r, sys.right)
                b = sys.bottom
            }
            v.setPadding(l, t, r, b)
            insets
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // 只在"用户本来就想要沉浸"时才自动补回来 —— 否则会覆盖掉他刚选的退出全屏
        if (hasFocus && wantImmersive) goImmersive()
    }

    override fun onResume() {
        super.onResume()
        if (wantImmersive) goImmersive()
        // 用户可能刚从系统设置页回来（开"所有文件访问"），回来就对一次状态
        if (::fileBridge.isInitialized) fileBridge.notifyState()
        // 也可能刚去 Shizuku 里点了授权
        if (::shizukuBridge.isInitialized) {
            try { shizukuBridge.onPermissionResult() } catch (_: Throwable) { }
        }
    }

    override fun onDestroy() {
        // 摘掉 Shizuku 回调：它持有 Activity 引用，不摘会泄漏
        try {
            shizukuListener?.let { Shizuku.removeRequestPermissionResultListener(it) }
        } catch (_: Throwable) { }
        shizukuListener = null
        super.onDestroy()
    }

    /** SAF 选文件夹 / 「所有文件访问」设置页 / 页面选文件的结果都从这里回来 */
    @Deprecated("startActivityForResult 是这里最省事也最稳的接法")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)

        // 网页里选文件的结果：必须回填，否则页面上的选择框会一直挂着
        if (requestCode == REQ_FILE_CHOOSER) {
            val cb = pendingFileCallback
            pendingFileCallback = null
            cb?.onReceiveValue(
                if (resultCode == Activity.RESULT_OK && data != null) {
                    WebChromeClient.FileChooserParams.parseResult(resultCode, data)
                } else {
                    null
                }
            )
            return
        }

        if (::fileBridge.isInitialized) fileBridge.onActivityResult(requestCode, resultCode, data)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (::fileBridge.isInitialized) fileBridge.onPermissionsResult()
    }

    /**
     * 返回键：优先让页面自己处理（关掉弹窗 / 结束询问），否则才退出 App。
     * 直接查 DOM 的 hidden 状态最可靠 —— 比去够脚本里的词法变量稳。
     */
    @Deprecated("Deprecated in API 33, still the simplest hook for WebView back")
    override fun onBackPressed() {
        web.evaluateJavascript(
            "(function(){" +
                "var a=document.getElementById('ask');" +
                "var s=document.getElementById('setup');" +
                "var g=document.getElementById('settings');" +
                "var sh=document.getElementById('sheet');" +
                "var pm=document.getElementById('perm');" +
                "var dr=document.getElementById('drawer');" +
                // 长按弹出的底部菜单在最上层，先关它
                "if(sh && !sh.hidden){" +
                "  if(typeof closeSheet==='function') closeSheet();" +
                "  return 'handled';" +
                "}" +
                "if(pm && !pm.hidden){ pm.hidden=true; return 'handled'; }" +
                // 对话列表抽屉其次
                "if(dr && !dr.hidden){" +
                "  if(typeof closeDrawer==='function') closeDrawer();" +
                "  return 'handled';" +
                "}" +
                "if(a && !a.hidden){" +
                "  if(typeof finishAsk==='function') finishAsk('（用户跳过了这个问题，请自行决定）');" +
                "  return 'handled';" +
                "}" +
                "if((s && !s.hidden) || (g && !g.hidden)){" +
                "  if(s) s.hidden=true; if(g) g.hidden=true; return 'handled';" +
                "}" +
                "return 'exit';" +
                "})()"
        ) { result ->
            if (result?.trim('"') != "handled") finish()
        }
    }
}

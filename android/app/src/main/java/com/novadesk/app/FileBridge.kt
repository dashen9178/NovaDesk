package com.novadesk.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.documentfile.provider.DocumentFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 文件桥：让 AI 真的能读写手机上的文件。
 *
 * 三档权限，由用户在输入框上方的「权限」按钮里选：
 *
 *   off    关着。任何文件操作都返回"没开权限"，AI 只能如实转告。
 *   folder 只在一个用户亲手选的文件夹里活动（SAF，不弹任何敏感权限）。
 *          路径是相对这个文件夹的，比如 "笔记/a.txt"。
 *   all    完全权限，整个手机存储都能读写。Android 11+ 要用户去系统设置页
 *          打开「所有文件访问」，低版本走老的读写存储权限。
 *
 * 为什么非要给"完全权限"这一档：这是用户明确要的 —— 他说"最高完全权限"。
 * 代价是拿到 MANAGE_EXTERNAL_STORAGE 之后应用能碰整块存储，
 * 所以默认值是 off，必须用户自己点开，且随时能关回去。
 */
class FileBridge(
    private val web: WebView,
    private val activity: MainActivity
) {

    companion object {
        const val MODE_OFF = "off"
        const val MODE_FOLDER = "folder"
        const val MODE_ALL = "all"

        const val REQ_TREE = 0x9A01
        const val REQ_ALL = 0x9A02

        private const val PREFS = "novadesk.files"
        private const val K_MODE = "mode"
        private const val K_TREE = "treeUri"

        /** 单次最多回传多少字符，防止把 WebView 撑爆 */
        private const val MAX_READ = 400_000

        /**
         * 超过这个大小就**不整份读进内存**。
         *
         * 为什么必须拦：原来的写法是先 readBytes()/readText() 整份读进来、
         * 之后再截断，几百 MB 的文件直接 OutOfMemoryError —— 而它是 Error
         * 不是 Exception，外层 catch 抓不住，App 当场崩。
         */
        private const val MAX_READ_BYTES = 2L * 1024 * 1024
    }

    private val prefs = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Volatile
    var mode: String = MODE_OFF
        private set

    @Volatile
    var treeUri: Uri? = null
        private set

    init {
        mode = prefs.getString(K_MODE, MODE_OFF) ?: MODE_OFF
        val t = prefs.getString(K_TREE, null)
        if (!t.isNullOrEmpty()) {
            try { treeUri = Uri.parse(t) } catch (_: Exception) { treeUri = null }
        }
    }

    /* ============================================================ *
     * 权限状态
     * ============================================================ */

    /** "所有文件访问"到底给没给。这是系统的真实状态，不是我们记的。 */
    fun allGranted(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Environment.isExternalStorageManager()
        } else {
            activity.checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE) ==
                PackageManager.PERMISSION_GRANTED
        }

    private fun folderName(): String {
        val u = treeUri ?: return ""
        return try {
            DocumentFile.fromTreeUri(activity, u)?.name ?: u.lastPathSegment ?: ""
        } catch (_: Exception) {
            u.lastPathSegment ?: ""
        }
    }

    /** 给页面看的完整状态 */
    @JavascriptInterface
    fun info(): String = JSONObject().apply {
        put("mode", mode)
        put("allGranted", allGranted())
        put("folderName", folderName())
        put("hasFolder", treeUri != null)
        put("sdk", Build.VERSION.SDK_INT)
        put("root", Environment.getExternalStorageDirectory().absolutePath)
    }.toString()

    private fun pushState() {
        val payload = info()
        web.post {
            try {
                web.evaluateJavascript(
                    "window.__novaFileChanged && window.__novaFileChanged(" +
                        JSONObject.quote(payload) + ");", null
                )
            } catch (_: Exception) { }
        }
    }

    private fun pushError(msg: String) {
        web.post {
            try {
                web.evaluateJavascript(
                    "window.__novaFileChanged && window.__novaFileChanged(null, " +
                        JSONObject.quote(msg) + ");", null
                )
            } catch (_: Exception) { }
        }
    }

    /* ============================================================ *
     * 切权限 / 选文件夹
     * ============================================================ */

    @JavascriptInterface
    fun setMode(m: String) {
        val v = when (m) {
            MODE_FOLDER -> MODE_FOLDER
            MODE_ALL -> MODE_ALL
            else -> MODE_OFF
        }

        // ★ 从"关闭"升到"完全权限"、而系统授权其实还开着 —— 这条路原本可以
        //   **无声**走通：AI 写的面板脚本一行 setMode('all') 就能把用户刚关掉的
        //   权限重新打开，他一次都不用点。网页那层拦不住（面板脚本和界面共用
        //   同一个 JS 环境），只能在原生这层问一句，把用户放回回路里。
        //
        //   另外两条升级路径不用问："指定文件夹"会弹系统的文件夹选择器，
        //   没授权时的"完全权限"会跳系统设置页 —— 那本来就是用户在操作。
        if (v == MODE_ALL && mode == MODE_OFF && allGranted()) {
            activity.runOnUiThread { confirmEscalateToAll() }
            return
        }
        applyMode(v)
    }

    private fun confirmEscalateToAll() {
        try {
            android.app.AlertDialog.Builder(activity)
                .setTitle("文件权限")
                .setMessage("NovaDesk 想打开「完全权限」：之后它能读写手机上的任意文件。\n\n允许吗？")
                .setNegativeButton("不允许") { _, _ -> pushState() }
                .setPositiveButton("允许") { _, _ -> applyMode(MODE_ALL) }
                .setCancelable(false)
                .show()
        } catch (e: Exception) {
            pushError("弹确认框失败，已保持关闭")
        }
    }

    private fun applyMode(v: String) {
        mode = v
        prefs.edit().putString(K_MODE, v).apply()

        // 选了"文件夹"却还没挑过目录，直接把他领到选择器，少一步
        if (v == MODE_FOLDER && treeUri == null) {
            activity.runOnUiThread { launchTreePicker() }
        }
        // 选了"完全权限"却没授权，直接带他去系统设置页
        if (v == MODE_ALL && !allGranted()) {
            activity.runOnUiThread { launchAllFilesRequest() }
        }
        pushState()
    }

    @JavascriptInterface
    fun pickFolder() {
        activity.runOnUiThread { launchTreePicker() }
    }

    @JavascriptInterface
    fun requestAll() {
        activity.runOnUiThread { launchAllFilesRequest() }
    }

    private fun launchTreePicker() {
        try {
            val i = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
                addFlags(
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION or
                        Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
                )
                putExtra("android.provider.extra.SHOW_ADVANCED", true)
            }
            activity.startActivityForResult(i, REQ_TREE)
        } catch (e: Exception) {
            pushError("打不开文件夹选择器：" + (e.message ?: ""))
        }
    }

    private fun launchAllFilesRequest() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val pkgUri = Uri.parse("package:" + activity.packageName)
            try {
                activity.startActivityForResult(
                    Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, pkgUri), REQ_ALL
                )
            } catch (_: Exception) {
                try {
                    activity.startActivityForResult(
                        Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION), REQ_ALL
                    )
                } catch (e2: Exception) {
                    pushError("这台机器打不开「所有文件访问」设置页")
                }
            }
        } else {
            activity.requestPermissions(
                arrayOf(android.Manifest.permission.WRITE_EXTERNAL_STORAGE), REQ_ALL
            )
        }
    }

    /** MainActivity 转发回来的结果 */
    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == REQ_TREE) {
            val u = data?.data
            if (resultCode == Activity.RESULT_OK && u != null) {
                try {
                    activity.contentResolver.takePersistableUriPermission(
                        u,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    )
                } catch (_: Exception) { }
                treeUri = u
                prefs.edit().putString(K_TREE, u.toString()).apply()
                if (mode == MODE_OFF) {
                    mode = MODE_FOLDER
                    prefs.edit().putString(K_MODE, MODE_FOLDER).apply()
                }
            }
            pushState()
        } else if (requestCode == REQ_ALL) {
            // 用户可能开了权限就切回来，也可能直接返回 —— 都以系统真实状态为准
            if (mode == MODE_ALL && allGranted()) {
                prefs.edit().putString(K_MODE, MODE_ALL).apply()
            }
            pushState()
        }
    }

    fun onPermissionsResult() {
        pushState()
    }

    /** 给 Activity 用：从系统设置页回来时刷新一次页面上的权限状态 */
    fun notifyState() {
        pushState()
    }

    /* ============================================================ *
     * 路径解析
     * ============================================================ */

    private fun fail(msg: String): String =
        JSONObject().put("ok", false).put("error", msg).toString()

    private fun fail(e: Exception): String =
        fail(e.javaClass.simpleName + ": " + (e.message ?: ""))

    private fun ok(o: JSONObject): String = o.put("ok", true).toString()

    private val offMsg = "文件权限没开。请用户点输入框上方的「权限」按钮开启。"

    /** all 模式：把各种写法归一到一个真实路径。返回 null 表示路径不合法。 */
    private fun resolveFile(path: String): File? {
        val s = path.trim().replace('\\', '/')
        // ★ all 档也必须拦 ".."。
        //   之前只拦了 folder 档，all 档直接交给 OS 解析 ——
        //   于是 "../../../data/data/<自己包名>/shared_prefs/x.xml"
        //   会被当成 /storage/emulated/0/../../../data/... 一路走出去，
        //   读到自己的 SharedPreferences（里面存着权限档位、授权树 URI），
        //   甚至写坏签名私钥文件。"完全权限"是指整个存储随便用，
        //   不是指可以跳出预期范围去碰应用私有目录。
        if (s.split('/').any { it == ".." }) return null

        val ext = Environment.getExternalStorageDirectory()
        return when {
            s.isEmpty() || s == "/" -> ext
            s.startsWith("/sdcard/") -> File(ext, s.removePrefix("/sdcard/"))
            s == "/sdcard" -> ext
            s.startsWith("/") -> File(s)
            else -> File(ext, s)
        }
    }

    private fun segments(path: String): List<String> =
        path.trim().replace('\\', '/').split('/').filter { it.isNotEmpty() && it != "." }

    private fun mimeOf(name: String): String {
        val n = name.lowercase()
        return when {
            n.endsWith(".txt") || n.endsWith(".md") || n.endsWith(".log") -> "text/plain"
            n.endsWith(".json") -> "application/json"
            n.endsWith(".html") || n.endsWith(".htm") -> "text/html"
            n.endsWith(".css") -> "text/css"
            n.endsWith(".js") -> "application/javascript"
            n.endsWith(".csv") -> "text/csv"
            n.endsWith(".xml") -> "text/xml"
            n.endsWith(".png") -> "image/png"
            n.endsWith(".jpg") || n.endsWith(".jpeg") -> "image/jpeg"
            n.endsWith(".gif") -> "image/gif"
            n.endsWith(".webp") -> "image/webp"
            n.endsWith(".pdf") -> "application/pdf"
            n.endsWith(".zip") -> "application/zip"
            n.endsWith(".mp3") -> "audio/mpeg"
            n.endsWith(".mp4") -> "video/mp4"
            n.endsWith(".apk") -> "application/vnd.android.package-archive"
            else -> "application/octet-stream"
        }
    }

    /**
     * folder 模式下按相对路径找节点。
     * 刻意**不允许 ".."** —— SAF 本身也跳不出去，这里直接拒绝，免得出现
     * "看起来能写、其实写到别处"这种更难查的问题。
     */
    private fun resolveDoc(path: String, create: Boolean): DocumentFile? {
        val root = treeUri?.let {
            try { DocumentFile.fromTreeUri(activity, it) } catch (_: Exception) { null }
        } ?: return null

        val parts = segments(path)
        if (parts.isEmpty()) return root

        var cur: DocumentFile = root
        for ((i, seg) in parts.withIndex()) {
            if (seg == "..") return null
            val isLast = i == parts.size - 1
            val found = cur.findFile(seg)
            if (found != null) { cur = found; continue }
            if (!create) return null
            cur = if (isLast) {
                cur.createFile(mimeOf(seg), seg) ?: return null
            } else {
                cur.createDirectory(seg) ?: return null
            }
        }
        return cur
    }

    /* ============================================================ *
     * 给 AI 用的文件操作
     * ============================================================ */

    @JavascriptInterface
    fun list(path: String): String {
        try {
            if (mode == MODE_OFF) return fail(offMsg)
            if (mode == MODE_FOLDER) {
                val d = resolveDoc(path, false) ?: return fail("找不到：" + path)
                if (!d.isDirectory) return fail("这不是目录：" + path)
                val arr = JSONArray()
                d.listFiles().sortedBy { (it.name ?: "").lowercase() }.forEach { f ->
                    arr.put(
                        JSONObject()
                            .put("name", f.name ?: "")
                            .put("dir", f.isDirectory)
                            .put("size", f.length())
                    )
                }
                return ok(JSONObject().put("path", path).put("items", arr))
            }
            val f = resolveFile(path) ?: return fail("路径不合法（不许用 .. 跳出范围）：" + path)
            if (!f.exists()) return fail("找不到：" + path)
            if (!f.isDirectory) return fail("这不是目录：" + path)
            val arr = JSONArray()
            (f.listFiles() ?: emptyArray()).sortedBy { it.name.lowercase() }.forEach { c ->
                arr.put(
                    JSONObject()
                        .put("name", c.name)
                        .put("dir", c.isDirectory)
                        .put("size", c.length())
                )
            }
            return ok(JSONObject().put("path", f.absolutePath).put("items", arr))
        } catch (e: Exception) {
            return fail(e)
        }
    }

    @JavascriptInterface
    fun read(path: String): String {
        try {
            if (mode == MODE_OFF) return fail(offMsg)
            val text: String
            if (mode == MODE_FOLDER) {
                val d = resolveDoc(path, false) ?: return fail("找不到：" + path)
                if (d.isDirectory) return fail("这是个目录：" + path)
                // 先看大小再读 —— 见 MAX_READ_BYTES 的说明
                if (d.length() > MAX_READ_BYTES) {
                    return fail("文件太大（" + d.length() + " 字节），超过 " +
                        (MAX_READ_BYTES / 1024) + " KB 就不整份读了")
                }
                val is2 = activity.contentResolver.openInputStream(d.uri)
                    ?: return fail("读不出来：" + path)
                text = is2.use { it.readBytes().toString(Charsets.UTF_8) }
            } else {
                val f = resolveFile(path) ?: return fail("路径不合法（不许用 .. 跳出范围）：" + path)
                if (!f.exists()) return fail("找不到：" + path)
                if (f.isDirectory) return fail("这是个目录：" + path)
                if (f.length() > MAX_READ_BYTES) {
                    return fail("文件太大（" + f.length() + " 字节），超过 " +
                        (MAX_READ_BYTES / 1024) + " KB 就不整份读了")
                }
                text = f.readText(Charsets.UTF_8)
            }
            val truncated = text.length > MAX_READ
            return ok(
                JSONObject()
                    .put("path", path)
                    .put("text", if (truncated) text.substring(0, MAX_READ) else text)
                    .put("chars", text.length)
                    .put("truncated", truncated)
            )
        } catch (e: Exception) {
            return fail(e)
        }
    }

    @JavascriptInterface
    fun write(path: String, content: String): String {
        try {
            if (mode == MODE_OFF) return fail(offMsg)
            val bytes = content.toByteArray(Charsets.UTF_8)
            if (mode == MODE_FOLDER) {
                val parts = segments(path)
                if (parts.isEmpty() || parts.contains("..")) return fail("路径不合法：" + path)
                val dirPath = parts.dropLast(1).joinToString("/")
                val name = parts.last()
                val dir = if (dirPath.isEmpty()) {
                    treeUri?.let {
                        try { DocumentFile.fromTreeUri(activity, it) } catch (_: Exception) { null }
                    }
                } else resolveDoc(dirPath, true)
                if (dir == null) return fail("建不出目录：" + dirPath)
                val target = dir.findFile(name) ?: dir.createFile(mimeOf(name), name)
                if (target == null) return fail("建不出文件：" + path)
                val os = activity.contentResolver.openOutputStream(target.uri, "wt")
                    ?: return fail("写不进去：" + path)
                os.use { it.write(bytes) }
            } else {
                val f = resolveFile(path) ?: return fail("路径不合法（不许用 .. 跳出范围）：" + path)
                f.parentFile?.mkdirs()
                f.writeBytes(bytes)
            }
            return ok(JSONObject().put("path", path).put("bytes", bytes.size))
        } catch (e: Exception) {
            return fail(e)
        }
    }

    @JavascriptInterface
    fun mkdir(path: String): String {
        try {
            if (mode == MODE_OFF) return fail(offMsg)
            if (mode == MODE_FOLDER) {
                val d = resolveDoc(path, true) ?: return fail("建不出目录：" + path)
                if (!d.isDirectory) return fail("同名的东西已经存在，而且不是目录")
            } else {
                val f = resolveFile(path) ?: return fail("路径不合法（不许用 .. 跳出范围）：" + path)
                if (!f.mkdirs() && !f.isDirectory) return fail("建不出目录：" + path)
            }
            return ok(JSONObject().put("path", path))
        } catch (e: Exception) {
            return fail(e)
        }
    }

    @JavascriptInterface
    fun delete(path: String): String {
        try {
            if (mode == MODE_OFF) return fail(offMsg)
            // 空路径绝对不能走到删除：SAF 那边空路径解析出来就是**授权树的根**，
            // 删它等于把用户选的整个文件夹递归清空
            if (path.trim().isEmpty()) {
                return fail("delete 必须给明确路径，空路径被拒绝（那等于删掉整个根目录）")
            }
            if (mode == MODE_FOLDER) {
                val d = resolveDoc(path, false) ?: return fail("找不到：" + path)
                if (isTreeRoot(d)) return fail("这是用户授权的根目录，不许整个删掉")
                if (!d.delete()) return fail("删不掉：" + path)
            } else {
                val f = resolveFile(path) ?: return fail("路径不合法（不许用 .. 跳出范围）：" + path)
                if (!f.exists()) return fail("找不到：" + path)
                // 存储根也不许整个删
                if (f.absolutePath == Environment.getExternalStorageDirectory().absolutePath) {
                    return fail("这是存储根目录，不许整个删掉")
                }
                if (!f.delete()) return fail("删不掉：" + path)
            }
            return ok(JSONObject().put("path", path))
        } catch (e: Exception) {
            return fail(e)
        }
    }

    /** 这个节点是不是用户当初授权的那棵树的根 */
    private fun isTreeRoot(d: DocumentFile): Boolean {
        val t = treeUri ?: return false
        return d.uri.toString().trimEnd('/') == t.toString().trimEnd('/')
    }

    /** 看看某个路径存不存在、是文件还是目录、多大 */
    @JavascriptInterface
    fun stat(path: String): String {
        try {
            if (mode == MODE_OFF) return fail(offMsg)
            if (mode == MODE_FOLDER) {
                val d = resolveDoc(path, false) ?: return ok(
                    JSONObject().put("path", path).put("exists", false)
                )
                return ok(
                    JSONObject()
                        .put("path", path)
                        .put("exists", true)
                        .put("dir", d.isDirectory)
                        .put("size", d.length())
                )
            }
            val f = resolveFile(path) ?: return fail("路径不合法（不许用 .. 跳出范围）：" + path)
            return ok(
                JSONObject()
                    .put("path", f.absolutePath)
                    .put("exists", f.exists())
                    .put("dir", f.isDirectory)
                    .put("size", if (f.isFile) f.length() else 0L)
            )
        } catch (e: Exception) {
            return fail(e)
        }
    }
}

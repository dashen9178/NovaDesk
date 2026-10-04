package com.novadesk.app

import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.core.content.FileProvider
import com.android.apksig.ApkSigner
import com.android.apksig.ApkVerifier
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.FilterOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.util.Locale
import java.util.zip.CRC32
import java.util.zip.Deflater
import java.util.zip.ZipEntry
import java.util.zip.ZipFile

/**
 * 把 AI 做出来的东西打成一个能装到任何手机上的 APK。
 *
 * 办法不是"在手机上跑一遍 Android 编译器"（那在手机上根本不现实），
 * 而是**备好一块模具**：
 *
 *   1. 取出随包发布的壳 APK（assets/shell.apk，一个极简 WebView 应用）
 *   2. 改写它二进制清单里的包名 / 应用名 / 版本号（AxmlRewriter，字符串池整体重建）
 *   3. 把用户的东西作为 assets/app.html 塞进去
 *   4. 用最标准的 ZIP 结构重打包（resources.arsc 不压缩且 4 字节对齐、
 *      所有条目预计算 CRC、不用数据描述符 —— 这是对各路 ROM 安装器最稳的写法）
 *   5. 用**本机自己生成的**签名身份做 v1+v2+v3 重签（DeviceKey + 官方 apksig）
 *   6. 签完当场自检，验不过就把包删掉、直接报错
 *
 * 第 6 步很关键：不验就交给安装器的话，用户只会看到"安装包已损坏"这种
 * 什么线索都没有的提示，根本没法排查。
 */
object ApkExporter {

    /** 壳的资产名，和 app/build.gradle.kts 里 copyShellApk 的目标一致 */
    private const val SHELL_ASSET = "shell.apk"

    /** 壳的原始包名。导出时整体替换成 com.novadesk.rt.<哈希> */
    private const val OLD_PACKAGE = "com.novadesk.rt.a00000"

    /** 壳清单里写死的应用名锚点 */
    private const val LABEL_ANCHOR = "XXXXXXXXXXXXXXXXXXXX"

    /** 壳清单里的 versionName 锚点 */
    private const val VERSION_ANCHOR = "0.00"

    /** 用户的东西塞进这个位置，壳的 ShellActivity 就是按这个路径加载的 */
    private const val PAGE_ENTRY = "assets/app.html"

    private const val MIN_SDK = 26

    /** 合法的固定 DOS 时间：1980-01-01 00:00（写全 0 是非法日期，部分 ROM 会拒包） */
    private const val DOS_TIME = 0
    private const val DOS_DATE = (0 shl 9) or (1 shl 5) or 1

    class Result(
        val ok: Boolean,
        val path: String?,
        val size: Long,
        val published: String?,
        val error: String?
    )

    /* ============================================================ *
     * 对外：导出
     * ============================================================ */

    fun export(ctx: Context, appNameRaw: String, html: String, versionRaw: String): Result {
        val r = exportCore(
            appNameRaw = appNameRaw,
            html = html,
            versionRaw = versionRaw,
            shell = { ctx.assets.open(SHELL_ASSET) },
            cacheDir = ctx.cacheDir,
            outDir = File(ctx.filesDir, "output"),
            keyDir = File(ctx.filesDir, "signing")
        )
        // 成了就再往公共「下载」放一份，用户可以直接装、也可以发给别人
        if (r.ok && r.path != null) {
            val f = File(r.path)
            return Result(true, r.path, r.size, publishToDownloads(ctx, f, f.name), null)
        }
        return r
    }

    /**
     * 导出主体。
     *
     * **刻意只依赖 File / InputStream，不碰 Context** —— 这样整条
     * "套壳 → 改清单 → 塞页面 → 重打包 → 重签名 → 自检" 的链路，
     * 能在 PC 的 JVM 单元测试里拿真壳 APK 从头跑一遍。
     * 手机上打出来的包和那里验过的是同一段代码，不是"照着写一遍"。
     */
    internal fun exportCore(
        appNameRaw: String,
        html: String,
        versionRaw: String,
        shell: () -> InputStream,
        cacheDir: File,
        outDir: File,
        keyDir: File
    ): Result {
        var work: File? = null
        var unsigned: File? = null
        var signed: File? = null
        try {
            var appName = appNameRaw.trim().ifEmpty { "我的应用" }
            if (appName.length > 20) appName = appName.substring(0, 20)

            var version = versionRaw.trim()
            if (!version.contains(Regex("\\d"))) version = "1.0"
            version = version.replace(Regex("[^0-9.]"), "").trim()
            if (version.isEmpty() || version.length > 12) version = "1.0"

            if (html.isBlank()) {
                return Result(false, null, 0, null, "没有可打包的内容")
            }

            val newPackage = "com.novadesk.rt." + stableSuffix(appName)
            val page = html.toByteArray(Charsets.UTF_8)

            // 1) 取壳
            // 临时文件名带上纳秒时间戳：两个人同时点导出（或者连点两下）
            // 用固定名字会互相踩，出来的包是坏的
            val tag = java.lang.Long.toHexString(System.nanoTime())
            work = File(cacheDir, "shell_in_$tag.apk")
            shell().use { input ->
                FileOutputStream(work).use { out -> input.copyTo(out) }
            }

            if (!outDir.exists() && !outDir.mkdirs()) {
                return Result(false, null, 0, null, "建不出输出目录")
            }
            unsigned = File(cacheDir, "unsigned_$tag.apk")
            signed = File(outDir, sanitizeName(appName) + ".apk")
            if (signed.exists()) signed.delete()

            // 2) 拆开壳、改清单、换页面
            val entries = readEntries(work)

            val manifest = entries["AndroidManifest.xml"]
                ?: return Result(false, null, 0, null, "壳里没有 AndroidManifest.xml")
            val repl = LinkedHashMap<String, String>()
            repl[OLD_PACKAGE] = newPackage
            repl[LABEL_ANCHOR] = appName
            repl[VERSION_ANCHOR] = version
            entries["AndroidManifest.xml"] =
                AxmlRewriter.replace(manifest, repl, OLD_PACKAGE, newPackage)

            // resources.arsc 里的资源包名也同步改掉，保持和清单一致
            entries["resources.arsc"]?.let { arsc ->
                entries["resources.arsc"] = rewriteArscPackage(arsc, newPackage)
            }

            // 3) 用户的页面
            entries[PAGE_ENTRY] = page

            // 4) 规范 ZIP
            packApk(entries, unsigned)

            // 5) 设备身份重签
            val key = DeviceKey.getFrom(keyDir)
            sign(key.privateKey, key.chain, unsigned, signed)

            // 6) 自检 —— 绝不把验不过的包交给安装器
            val verifyMsg = verifyApk(signed)
            if (verifyMsg != null) {
                signed.delete()
                return Result(false, null, 0, null, "生成后自检没过：" + verifyMsg)
            }
            if (!signed.exists() || signed.length() < 20_000) {
                signed.delete()
                return Result(false, null, 0, null, "生成出来的包体积不对，已中止")
            }

            return Result(true, signed.absolutePath, signed.length(), null, null)
        } catch (e: Throwable) {
            return Result(false, null, 0, null, (e.javaClass.simpleName + ": " + (e.message ?: "")))
        } finally {
            try { unsigned?.delete() } catch (_: Throwable) { }
            try { work?.delete() } catch (_: Throwable) { }
        }
    }

    /* ============================================================ *
     * 对外：把生成的包装上（调系统安装器）
     * ============================================================ */

    fun install(ctx: Context, path: String): String? {
        val f = File(path)
        if (!f.isFile) return "找不到这个 APK：" + path
        return try {
            val uri: Uri = FileProvider.getUriForFile(
                ctx, ctx.packageName + ".fileprovider", f
            )
            val i = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            ctx.startActivity(i)
            null
        } catch (e: Throwable) {
            "打不开安装器：" + (e.message ?: "")
        }
    }

    /* ============================================================ *
     * 往公共「下载」也放一份，方便用户安装/发给别人
     * ============================================================ */

    private fun publishToDownloads(ctx: Context, apk: File, name: String): String? {
        // Android 10+ 走 MediaStore，不需要任何权限
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            try {
                val resolver = ctx.contentResolver
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, name)
                    put(
                        MediaStore.Downloads.MIME_TYPE,
                        "application/vnd.android.package-archive"
                    )
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val uri = resolver.insert(
                    MediaStore.Downloads.EXTERNAL_CONTENT_URI, values
                ) ?: return null
                resolver.openOutputStream(uri)?.use { os ->
                    apk.inputStream().use { it.copyTo(os) }
                } ?: return null
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
                return "下载/" + name
            } catch (_: Throwable) {
                return null
            }
        }
        // Android 9 及以下：有存储权限就直接写进公共下载目录（没有就算了，不强求）
        return try {
            val dir = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                "NovaDesk"
            )
            if (!dir.exists() && !dir.mkdirs()) return null
            val dst = File(dir, name)
            apk.inputStream().use { input ->
                FileOutputStream(dst).use { out -> input.copyTo(out) }
            }
            "下载/NovaDesk/" + name
        } catch (_: Throwable) {
            null
        }
    }

    /* ============================================================ *
     * 打包（纯逻辑，不碰 Android API，便于日后在 PC 上等价验证）
     * ============================================================ */

    private class CentralEntry {
        lateinit var name: ByteArray
        var method = 0
        var flag = 0
        var crc = 0L
        var csize = 0L
        var usize = 0L
        var offset = 0L
    }

    /** resources.arsc 和已经被压缩过的二进制资源按 STORED 存（和官方构建一致），其余 DEFLATED */
    internal fun isStoredEntry(name: String): Boolean {
        if (name == "resources.arsc") return true
        val n = name.lowercase(Locale.ROOT)
        return n.endsWith(".png") || n.endsWith(".jpg") || n.endsWith(".jpeg") ||
            n.endsWith(".gif") || n.endsWith(".webp") || n.endsWith(".cur") ||
            n.endsWith(".mp3") || n.endsWith(".ogg") || n.endsWith(".wav") ||
            n.endsWith(".mp4") || n.endsWith(".ttf")
    }

    internal fun readEntries(zip: File): LinkedHashMap<String, ByteArray> {
        val entries = LinkedHashMap<String, ByteArray>()
        ZipFile(zip).use { zf ->
            val en = zf.entries()
            while (en.hasMoreElements()) {
                val ze: ZipEntry = en.nextElement()
                if (ze.name.startsWith("META-INF/")) continue   // 丢掉壳自带的签名
                entries[ze.name] = zf.getInputStream(ze).use { it.readBytes() }
            }
        }
        return entries
    }

    /**
     * 手写规范 ZIP。
     *
     * 三个细节都是为了兼容各种安装器，缺一个就会在部分 ROM 上装不上：
     *   · resources.arsc 与已压缩资源 STORED 存放，且数据起始偏移 4 字节对齐
     *   · 所有条目在本地头里就写全 CRC/大小（general purpose bit3 = 0，不用数据描述符）
     *   · 固定写一个合法的 DOS 时间（1980-01-01），不能留全 0
     */
    internal fun packApk(entries: Map<String, ByteArray>, out: File) {
        val def = Deflater(Deflater.BEST_COMPRESSION, true)
        try {
            val central = ArrayList<CentralEntry>()
            FileOutputStream(out).use { fos ->
                val co = CountedOut(fos)
                for ((name, raw) in entries) {
                    val stored = isStoredEntry(name)
                    val crc = CRC32().apply { update(raw) }
                    val nameB = name.toByteArray(Charsets.UTF_8)

                    val payload: ByteArray
                    val method: Int
                    if (stored) {
                        method = 0
                        payload = raw
                    } else {
                        method = 8
                        payload = deflate(def, raw)
                    }

                    var extraLen = 0
                    if (stored) {
                        // 让这条数据在文件里的起始偏移是 4 的倍数（zipalign 干的就是这件事）
                        val headerEnd = co.count + 30 + nameB.size
                        extraLen = ((4 - (headerEnd and 3L)) and 3L).toInt()
                    }
                    val offset = co.count

                    var nonAscii = false
                    for (b in nameB) if ((b.toInt() and 0x80) != 0) { nonAscii = true; break }
                    val gpFlag = if (nonAscii) 0x800 else 0

                    writeU32(co, 0x04034b50L)      // local file header
                    writeU16(co, 20)               // version needed
                    writeU16(co, gpFlag)
                    writeU16(co, method)
                    writeU16(co, DOS_TIME)
                    writeU16(co, DOS_DATE)
                    writeU32(co, crc.value)
                    writeU32(co, payload.size.toLong())
                    writeU32(co, raw.size.toLong())
                    writeU16(co, nameB.size)
                    writeU16(co, extraLen)
                    co.write(nameB)
                    if (extraLen > 0) co.write(ByteArray(extraLen))
                    co.write(payload)

                    val ce = CentralEntry()
                    ce.name = nameB
                    ce.method = method
                    ce.flag = gpFlag
                    ce.crc = crc.value
                    ce.csize = payload.size.toLong()
                    ce.usize = raw.size.toLong()
                    ce.offset = offset
                    central.add(ce)
                }

                val cdStart = co.count
                for (ce in central) {
                    writeU32(co, 0x02014b50L)
                    writeU16(co, 20)
                    writeU16(co, 20)
                    writeU16(co, ce.flag)
                    writeU16(co, ce.method)
                    writeU16(co, DOS_TIME)
                    writeU16(co, DOS_DATE)
                    writeU32(co, ce.crc)
                    writeU32(co, ce.csize)
                    writeU32(co, ce.usize)
                    writeU16(co, ce.name.size)
                    writeU16(co, 0)
                    writeU16(co, 0)
                    writeU16(co, 0)
                    writeU16(co, 0)
                    writeU32(co, 0L)
                    writeU32(co, ce.offset)
                    co.write(ce.name)
                }
                val cdSize = co.count - cdStart

                writeU32(co, 0x06054b50L)          // EOCD
                writeU16(co, 0)
                writeU16(co, 0)
                writeU16(co, central.size)
                writeU16(co, central.size)
                writeU32(co, cdSize)
                writeU32(co, cdStart)
                writeU16(co, 0)
            }
        } finally {
            def.end()
        }
    }

    private fun deflate(def: Deflater, raw: ByteArray): ByteArray {
        def.reset()
        def.setInput(raw)
        def.finish()
        val bo = ByteArrayOutputStream(raw.size / 2 + 32)
        val buf = ByteArray(8192)
        while (!def.finished()) {
            val n = def.deflate(buf)
            if (n > 0) bo.write(buf, 0, n)
        }
        return bo.toByteArray()
    }

    private class CountedOut(out: OutputStream) : FilterOutputStream(out) {
        var count: Long = 0
            private set

        override fun write(b: Int) {
            out.write(b)
            count++
        }

        override fun write(b: ByteArray, off: Int, len: Int) {
            out.write(b, off, len)
            count += len
        }
    }

    private fun writeU16(o: OutputStream, v: Int) {
        o.write(v and 0xff)
        o.write((v ushr 8) and 0xff)
    }

    private fun writeU32(o: OutputStream, v: Long) {
        o.write((v and 0xff).toInt())
        o.write(((v ushr 8) and 0xff).toInt())
        o.write(((v ushr 16) and 0xff).toInt())
        o.write(((v ushr 24) and 0xff).toInt())
    }

    /* ============================================================ *
     * resources.arsc 包名改写
     * ============================================================ */

    /**
     * RES_TABLE_PACKAGE_TYPE 里的包名是**定长 128 个 UTF-16 字符**（256 字节）的缓冲，
     * 所以可以原地改写、体积不变，不用重建整张资源表。
     */
    internal fun rewriteArscPackage(arsc: ByteArray, newPkg: String): ByteArray {
        if (arsc.size < 12) return arsc
        val tableHeader = (arsc[2].toInt() and 0xff) or ((arsc[3].toInt() and 0xff) shl 8)
        var off = tableHeader
        while (off + 8 <= arsc.size) {
            val type = (arsc[off].toInt() and 0xff) or ((arsc[off + 1].toInt() and 0xff) shl 8)
            val size = (arsc[off + 4].toInt() and 0xff) or
                ((arsc[off + 5].toInt() and 0xff) shl 8) or
                ((arsc[off + 6].toInt() and 0xff) shl 16) or
                ((arsc[off + 7].toInt() and 0xff) shl 24)
            if (size <= 0 || off + size <= off) break      // 防畸形的 size 把偏移推反
            if (type == 0x0200) {                      // RES_TABLE_PACKAGE_TYPE
                val nameOff = off + 12                 // id(4) 之后就是 256 字节包名
                // 写之前先确认这 256 字节真的在表里，别越界写坏别人的内存
                if (nameOff < 0 || nameOff + 256 > arsc.size) return arsc
                val out = arsc.clone()
                var chars = newPkg.toCharArray()
                if (chars.size > 127) chars = chars.copyOf(127)
                for (i in 0 until 128) {
                    val c = if (i < chars.size) chars[i].code else 0
                    out[nameOff + i * 2] = (c and 0xff).toByte()
                    out[nameOff + i * 2 + 1] = ((c ushr 8) and 0xff).toByte()
                }
                return out
            }
            off += size
        }
        return arsc
    }

    /* ============================================================ *
     * 签名 / 自检
     * ============================================================ */

    internal fun sign(
        pk: java.security.PrivateKey,
        chain: List<java.security.cert.X509Certificate>,
        input: File,
        output: File
    ) {
        require(chain.isNotEmpty()) { "签名证书为空" }
        val sc = ApkSigner.SignerConfig.Builder("CERT", pk, chain).build()
        ApkSigner.Builder(listOf(sc))
            .setInputApk(input)
            .setOutputApk(output)
            .setMinSdkVersion(MIN_SDK)
            .setV1SigningEnabled(true)
            .setV2SigningEnabled(true)
            .setV3SigningEnabled(true)
            .setOtherSignersSignaturesPreserved(false)
            .build()
            .sign()
    }

    /** 返回 null 表示通过，否则返回错误描述 */
    internal fun verifyApk(apk: File): String? {
        return try {
            val r = ApkVerifier.Builder(apk)
                .setMinCheckedPlatformVersion(MIN_SDK)
                .setMaxCheckedPlatformVersion(34)
                .build()
                .verify()
            if (!r.isVerified) {
                val sb = StringBuilder()
                for (i in r.errors) sb.append(i).append(';')
                if (sb.isEmpty()) "没通过签名校验" else sb.toString()
            } else if (r.isVerifiedUsingV1Scheme || r.isVerifiedUsingV2Scheme ||
                r.isVerifiedUsingV3Scheme
            ) {
                null
            } else {
                "没有任何可用的签名方案"
            }
        } catch (e: Throwable) {
            "校验时异常：" + (e.message ?: "")
        }
    }

    /* ============================================================ *
     * 小工具
     * ============================================================ */

    /** 由应用名推出一个稳定的包名后缀：同一个名字每次导出都是同一个包，才能覆盖安装 */
    private fun stableSuffix(name: String): String {
        var h = 1125899906842597L
        for (c in name) h = 31 * h + c.code
        // 用无符号字符串，不要 Math.abs —— Long.MIN_VALUE 取绝对值还是负数
        var s = java.lang.Long.toUnsignedString(h, 36)
        while (s.length < 6) s = "0" + s
        s = s.substring(s.length - 6)
        // 包名的一段不能以数字开头，垫个字母
        if (s[0].isDigit()) s = "a" + s.substring(1)
        return s
    }

    private fun sanitizeName(name: String): String {
        val s = name.replace(Regex("[\\\\/:*?\"<>|\\s]+"), "_")
        return if (s.isEmpty()) "app" else s
    }
}

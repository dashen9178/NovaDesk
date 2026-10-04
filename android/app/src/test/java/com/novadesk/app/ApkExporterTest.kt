package com.novadesk.app

import com.android.apksig.ApkVerifier
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.zip.ZipFile

/**
 * 导出链路的 PC 端实证。
 *
 * 手机上导出靠的是"备用壳 → 改清单 → 塞页面 → 重打包 → 重签名"这套动作，
 * 中间任何一步错了，用户拿到的都是一个装不上、或者装上一开就闪退的包 ——
 * 而这两种失败在手机上排查起来都很痛苦（安装器只会含糊地说"包已损坏"）。
 *
 * 所以这里在 JVM 上拿**真实的壳 APK** 把整条链路跑一遍，逐个断言：
 *   · 包名、应用名、版本号真的被改写了
 *   · 入口 Activity 的类名**原样保留**（这是最容易搞砸、也最致命的一点：
 *     如果它跟着包名一起变，清单就指向一个 dex 里不存在的类，装上必闪退）
 *   · 用户的页面确实被塞进了 assets/app.html
 *   · resources.arsc 是 STORED 且 4 字节对齐
 *   · 签名能被官方 ApkVerifier 验过
 *
 * 跑的是 ApkExporter.exportCore 本身，不是照着它另写一遍 —— 验的就是会上手机的那段代码。
 */
class ApkExporterTest {

    private fun shellFile(): File {
        // 单元测试的工作目录是模块目录（android/app），但手工用 java 跑时
        // 可能是 android/ 甚至仓库根 —— 所以一路往上找，别写死一个相对路径。
        val tried = ArrayList<String>()
        var dir: File? = File("").absoluteFile
        while (dir != null) {
            for (rel in listOf(
                "build/generated/shellAssets/shell.apk",
                "app/build/generated/shellAssets/shell.apk",
                "shell/build/outputs/apk/release/shell-release-unsigned.apk"
            )) {
                val c = File(dir, rel)
                tried.add(c.path)
                if (c.isFile) return c
            }
            dir = dir.parentFile
        }
        error(
            "找不到壳 APK。先跑 gradlew :shell:assembleRelease。找过这些地方：\n" +
                tried.joinToString("\n")
        )
    }

    private fun tempDir(name: String): File {
        val d = File(System.getProperty("java.io.tmpdir"), "nova-test-$name-${System.nanoTime()}")
        d.mkdirs()
        return d
    }

    private class LocalEntry(val name: String, val method: Int, val dataOffset: Long)

    private fun u16(b: ByteArray, p: Int): Int =
        (b[p].toInt() and 0xff) or ((b[p + 1].toInt() and 0xff) shl 8)

    private fun u32(b: ByteArray, p: Int): Long =
        (b[p].toLong() and 0xff) or
            ((b[p + 1].toLong() and 0xff) shl 8) or
            ((b[p + 2].toLong() and 0xff) shl 16) or
            ((b[p + 3].toLong() and 0xff) shl 24)

    /** 顺着本地文件头走一遍，拿到每个条目的压缩方式和数据起始偏移 */
    private fun walkLocalEntries(b: ByteArray): List<LocalEntry> {
        val out = ArrayList<LocalEntry>()
        var p = 0
        while (p + 30 <= b.size) {
            if (u32(b, p) != 0x04034b50L) break
            val method = u16(b, p + 8)
            val csize = u32(b, p + 18)
            val nameLen = u16(b, p + 26)
            val extraLen = u16(b, p + 28)
            val name = String(b, p + 30, nameLen, Charsets.UTF_8)
            val dataOff = (p + 30 + nameLen + extraLen).toLong()
            out.add(LocalEntry(name, method, dataOff))
            p = (dataOff + csize).toInt()
        }
        return out
    }

    /**
     * 在字节里找一段文本。
     *
     * **两种编码都要找**：AXML 的字符串池可能是 UTF-8 也可能是 UTF-16
     * （由池头 flags 的 0x100 位决定），这台机器上 aapt2 给壳打出来的是 UTF-16。
     * 只按 UTF-8 找的话，明明改写成功了也会断言失败 —— 反过来也一样，
     * 否定断言会变成"永远通过"的假绿。
     */
    private fun containsText(hay: ByteArray, needle: String): Boolean =
        containsBytes(hay, needle.toByteArray(Charsets.UTF_8)) ||
            containsBytes(hay, needle.toByteArray(Charsets.UTF_16LE))

    private fun containsBytes(hay: ByteArray, needle: ByteArray): Boolean {
        val n = needle
        if (n.isEmpty() || hay.size < n.size) return false
        outer@ for (i in 0..(hay.size - n.size)) {
            for (j in n.indices) if (hay[i + j] != n[j]) continue@outer
            return true
        }
        return false
    }

    /**
     * 读 zip 里某个条目的字节。
     * 必须在 use 里面读完再交出去 —— 先关 ZipFile、回头再读流，会得到
     * "Stream closed"，而且看上去像打包出了问题，很容易误判。
     */
    private fun readZipEntry(zip: File, name: String): ByteArray =
        ZipFile(zip).use { z ->
            val e = z.getEntry(name) ?: error("APK 里没有 $name")
            z.getInputStream(e).use { it.readBytes() }
        }

    // ================================================================

    @Test
    fun `导出的包结构正确且签名可验证`() {
        val shell = shellFile()
        val cache = tempDir("cache")
        val out = tempDir("out")
        val keys = tempDir("keys")

        val appName = "测试应用"
        val version = "2.5"
        val page = "<!doctype html><html><body><h1>你好 NovaDesk</h1>" +
            "<script>document.title='ok'</script></body></html>"

        val r = ApkExporter.exportCore(
            appNameRaw = appName,
            html = page,
            versionRaw = version,
            shell = { shell.inputStream() },
            cacheDir = cache,
            outDir = out,
            keyDir = keys
        )

        assertTrue("导出应该成功，实际错误：" + r.error, r.ok)
        assertNotNull(r.path)

        val apk = File(r.path!!)
        assertTrue("生成的 APK 应该存在", apk.isFile)
        assertTrue("生成的 APK 不该小于 20KB，实际 ${apk.length()}", apk.length() > 20_000)

        // ---- 签名：官方 ApkVerifier 必须验得过 ----
        val vr = ApkVerifier.Builder(apk)
            .setMinCheckedPlatformVersion(26)
            .setMaxCheckedPlatformVersion(34)
            .build()
            .verify()
        assertTrue("签名应该验得过，错误：${vr.errors}", vr.isVerified)
        assertTrue(
            "应该有 v2 或 v3 签名",
            vr.isVerifiedUsingV2Scheme || vr.isVerifiedUsingV3Scheme
        )

        // ---- 包名：由应用名稳定推出 ----
        val expectedPkg = "com.novadesk.rt." + stableSuffixForTest(appName)

        val raw = apk.readBytes()

        // ---- 清单：包名/应用名/版本号被改写，入口类名原样保留 ----
        val manifest = readZipEntry(apk, "AndroidManifest.xml")

        assertTrue("清单里应该出现新包名 $expectedPkg", containsText(manifest, expectedPkg))
        assertFalse(
            "壳的原始包名不该残留",
            containsText(manifest, "com.novadesk.rt.a00000")
        )
        assertTrue("清单里应该出现新的应用名", containsText(manifest, appName))
        assertFalse(
            "应用名锚点不该残留（说明没改写成功）",
            containsText(manifest, "XXXXXXXXXXXXXXXXXXXX")
        )
        assertTrue("清单里应该出现新的版本号", containsText(manifest, version))
        assertTrue(
            "★ 入口 Activity 的类名必须原样保留，否则装上一点就闪退",
            containsText(manifest, "com.novadesk.shellhost.ShellActivity")
        )

        // ---- 页面：用户的 HTML 真的进去了，而且一个字节不差 ----
        val inner = readZipEntry(apk, "assets/app.html").toString(Charsets.UTF_8)
        assertEquals("assets/app.html 内容应该和传进去的一模一样", page, inner)

        // ---- ZIP 结构：resources.arsc 不压缩、且 4 字节对齐 ----
        val locals = walkLocalEntries(raw)
        assertTrue("应该能解析出本地条目，实际 ${locals.size} 条", locals.size > 5)

        val arsc = locals.firstOrNull { it.name == "resources.arsc" }
        assertNotNull("应该有 resources.arsc", arsc)
        assertEquals("resources.arsc 必须以 STORED 存放", 0, arsc!!.method)
        assertEquals(
            "resources.arsc 的数据起始偏移必须 4 字节对齐",
            0L, arsc.dataOffset % 4
        )

        // 其他 STORED 条目（图标等）也得对齐
        locals.filter { it.method == 0 }.forEach { e ->
            assertEquals("STORED 条目 ${e.name} 未对齐", 0L, e.dataOffset % 4)
        }

        // 所有条目都必须在本地头写全 CRC（不能用数据描述符），否则部分 ROM 会拒装
        val gpFlags = ArrayList<Int>()
        run {
            var p = 0
            while (p + 30 <= raw.size && u32(raw, p) == 0x04034b50L) {
                gpFlags.add(u16(raw, p + 6))
                val csize = u32(raw, p + 18)
                val nameLen = u16(raw, p + 26)
                val extraLen = u16(raw, p + 28)
                p = (p + 30 + nameLen + extraLen + csize).toInt()
            }
        }
        gpFlags.forEachIndexed { i, f ->
            assertEquals("第 $i 个条目用了数据描述符（bit3），部分 ROM 会拒装", 0, f and 0x08)
        }

        // 壳必须是**未签名**的。壳里若带着别人的签名，导出的包就会把那份签名一起带上，
        // 而我们要的是用本机设备身份重签 —— 这条守住"壳不带签名"这个前提。
        val shellNames = ArrayList<String>()
        ZipFile(shell).use { z ->
            val en = z.entries()
            while (en.hasMoreElements()) shellNames.add(en.nextElement().name)
        }
        val shellSigned = shellNames.any {
            it.startsWith("META-INF/") &&
                (it.endsWith(".RSA") || it.endsWith(".SF") || it.endsWith(".DSA") || it.endsWith(".EC"))
        }
        assertFalse("壳 APK 本身不该带签名（导出时用设备身份重签）", shellSigned)

        // 新包必须有 v1 签名文件（v2/v3 在 APK Signing Block 里，不算普通条目）
        assertTrue(
            "新包应该有 v1 签名文件（META-INF/*.RSA），说明重签真的做了",
            locals.any { it.name.startsWith("META-INF/") && it.name.endsWith(".RSA") }
        )
    }

    @Test
    fun `同名应用每次导出得到同一个包名（能覆盖安装）`() {
        val a = ApkExporter.exportCore(
            appNameRaw = "记账本", html = "<html><body>x</body></html>", versionRaw = "1.0",
            shell = { shellFile().inputStream() },
            cacheDir = tempDir("c1"), outDir = tempDir("o1"), keyDir = tempDir("k1")
        )
        assertTrue(a.error ?: "", a.ok)
        val p1 = findPackage(readZipEntry(File(a.path!!), "AndroidManifest.xml"))

        val b = ApkExporter.exportCore(
            appNameRaw = "记账本", html = "<html><body>y</body></html>", versionRaw = "1.0",
            shell = { shellFile().inputStream() },
            cacheDir = tempDir("c2"), outDir = tempDir("o2"), keyDir = tempDir("k2")
        )
        assertTrue(b.error ?: "", b.ok)
        val p2 = findPackage(readZipEntry(File(b.path!!), "AndroidManifest.xml"))

        assertNotNull("应该能从清单里读出新包名", p1)
        assertEquals("同一个应用名必须推出同一个包名，否则装不上、也覆盖不了", p1, p2)
    }

    @Test
    fun `空内容会被挡下来`() {
        val r = ApkExporter.exportCore(
            appNameRaw = "空", html = "   ", versionRaw = "1.0",
            shell = { shellFile().inputStream() },
            cacheDir = tempDir("c"), outDir = tempDir("o"), keyDir = tempDir("k")
        )
        assertFalse("没有内容时不该生成包", r.ok)
        assertNotNull(r.error)
    }

    @Test
    fun `应用名会被截断到 20 个字`() {
        val long = "一二三四五六七八九十一二三四五六七八九十超出部分"
        val r = ApkExporter.exportCore(
            appNameRaw = long, html = "<html><body>x</body></html>", versionRaw = "1.0",
            shell = { shellFile().inputStream() },
            cacheDir = tempDir("c"), outDir = tempDir("o"), keyDir = tempDir("k")
        )
        assertTrue(r.error ?: "", r.ok)
        val m = readZipEntry(File(r.path!!), "AndroidManifest.xml")
        assertTrue("截断后的名字应该在清单里", containsText(m, long.substring(0, 20)))
        assertFalse("超出部分不该出现在清单里", containsText(m, long))
    }

    // ================================================================

    /** 和 ApkExporter 内部同一套算法；这里独立实现一遍，避免"自己验自己" */
    private fun stableSuffixForTest(name: String): String {
        var h = 1125899906842597L
        for (c in name) h = 31 * h + c.code
        h = Math.abs(h)
        var s = java.lang.Long.toString(h, 36)
        while (s.length < 6) s = "0" + s
        return s.substring(s.length - 6)
    }

    /** 从 AXML 字节里把 com.novadesk.rt.xxxxxx 抠出来（两种池编码都试） */
    private fun findPackage(axml: ByteArray): String? {
        val re = Regex("com\\.novadesk\\.rt\\.[0-9a-z]{6}")
        for (cs in listOf(Charsets.UTF_16LE, Charsets.UTF_8, Charsets.ISO_8859_1)) {
            re.find(String(axml, cs))?.let { return it.value }
        }
        return null
    }
}

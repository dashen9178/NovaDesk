package com.novadesk.app

import android.content.Context
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Calendar
import java.util.Date

/**
 * 设备签名身份：**每台手机自己生成**，第一次导出时现造，之后一直复用。
 *
 * 为什么不用一份写死在包里的密钥：APK 本质就是个 ZIP，任何人拿到都能把
 * 里面的私钥抠出来，然后以你的名义签出"官方更新"。所以私钥不随包分发 ——
 * 在设备上用 KeyPairGenerator 现生成一份，连同自签证书存进应用私有目录
 * （别的应用读不到，卸载即销毁）。
 *
 * 代价（必须让用户知道）：清数据 / 卸载重装 / 换手机之后这份身份就没了，
 * 此前导出的作品将**无法覆盖更新**，只能先卸载旧版再装新的。
 */
object DeviceKey {

    private const val DIR_NAME = "signing"
    private const val KEY_NAME = "device.pk8"
    private const val CERT_NAME = "device.crt"

    /** 证书主题里的 CN。用户基本看不到它，只在系统「应用签名」详情里露一眼 */
    private const val SUBJECT_CN = "NovaDesk Device Signing Key"

    /** Android 14 起要求新应用的签名证书有效期至少到 2033 年，30 年足够 */
    private const val KEY_BITS = 2048
    private const val VALID_YEARS = 30

    /** 一份可用的签名身份：私钥 + 证书链 */
    class Material(val privateKey: PrivateKey, val chain: List<X509Certificate>)

    private fun dir(ctx: Context) = File(ctx.filesDir, DIR_NAME)

    fun hasKey(ctx: Context): Boolean = hasKeyIn(dir(ctx))

    /** 和 hasKey 一样，只是收一个目录 —— 便于在 PC 单元测试里直接跑 */
    fun hasKeyIn(d: File): Boolean =
        File(d, KEY_NAME).isFile && File(d, CERT_NAME).isFile

    /**
     * 取出设备密钥；不存在或者损坏就当场生成一份并落盘。
     * RSA 2048 生成一次大约几百毫秒，所以只在第一次导出时发生。
     */
    @Synchronized
    fun get(ctx: Context): Material = getFrom(dir(ctx))

    /**
     * 核心实现：只依赖一个目录，不依赖 Context。
     * 这样打包+签名的整条链路能在 PC 的单元测试里用真壳 APK 跑一遍。
     */
    @Synchronized
    fun getFrom(d: File): Material {
        val keyFile = File(d, KEY_NAME)
        val certFile = File(d, CERT_NAME)

        if (keyFile.isFile && certFile.isFile) {
            try {
                return load(keyFile, certFile)
            } catch (_: Throwable) {
                // 半损坏（比如只写成功了一半）：整对丢掉重新生成。
                // 绝不静默复用其中一半 —— 那会做出"私钥和证书对不上"的怪包。
                keyFile.delete()
                certFile.delete()
            }
        }
        return generate(d, keyFile, certFile)
    }

    /** 仅在"重新生成签名身份"这类维护入口用。会让已导出的作品无法覆盖更新。 */
    @Synchronized
    fun reset(ctx: Context) {
        val d = dir(ctx)
        File(d, KEY_NAME).delete()
        File(d, CERT_NAME).delete()
    }

    private fun load(keyFile: File, certFile: File): Material {
        val keyDer = readAll(FileInputStream(keyFile))
        val pk = KeyFactory.getInstance("RSA")
            .generatePrivate(PKCS8EncodedKeySpec(keyDer))

        val certDer = readAll(FileInputStream(certFile))
        val cf = CertificateFactory.getInstance("X.509")
        val cert = cf.generateCertificate(ByteArrayInputStream(certDer)) as X509Certificate

        return Material(pk, listOf(cert))
    }

    private fun generate(d: File, keyFile: File, certFile: File): Material {
        val kpg = KeyPairGenerator.getInstance("RSA")
        kpg.initialize(KEY_BITS)
        val kp = kpg.generateKeyPair()

        val notBefore = shift(Date(), Calendar.DAY_OF_MONTH, -1)
        val notAfter = shift(Date(), Calendar.YEAR, VALID_YEARS)
        val certDer = DerX509.selfSigned(kp, SUBJECT_CN, notBefore, notAfter)

        if (!d.exists() && !d.mkdirs()) {
            throw IllegalStateException("建不出密钥目录：" + d.absolutePath)
        }
        FileOutputStreamSafe.write(keyFile, kp.private.encoded)
        FileOutputStreamSafe.write(certFile, certDer)

        val cf = CertificateFactory.getInstance("X.509")
        val cert = cf.generateCertificate(ByteArrayInputStream(certDer)) as X509Certificate
        return Material(kp.private, listOf(cert))
    }

    private fun shift(base: Date, field: Int, amount: Int): Date {
        val c = Calendar.getInstance()
        c.time = base
        c.add(field, amount)
        return c.time
    }

    private fun readAll(input: InputStream): ByteArray {
        val bo = java.io.ByteArrayOutputStream()
        val buf = ByteArray(8192)
        input.use { ins ->
            while (true) {
                val n = ins.read(buf)
                if (n <= 0) break
                bo.write(buf, 0, n)
            }
        }
        return bo.toByteArray()
    }

    /** 小工具：写文件并保证关掉 */
    private object FileOutputStreamSafe {
        fun write(f: File, data: ByteArray) {
            val os = java.io.FileOutputStream(f)
            try {
                os.write(data)
                os.flush()
            } finally {
                try { os.close() } catch (_: Throwable) { }
            }
        }
    }
}

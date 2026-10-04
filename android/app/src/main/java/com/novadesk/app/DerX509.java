package com.novadesk.app;

import java.io.ByteArrayOutputStream;
import java.math.BigInteger;
import java.security.KeyPair;
import java.security.SecureRandom;
import java.security.Signature;
import java.util.Calendar;
import java.util.Date;
import java.util.GregorianCalendar;
import java.util.Locale;
import java.util.SimpleTimeZone;

/**
 * 最小 X.509 自签证书生成器（纯 Java，不依赖 Android，也不依赖 BouncyCastle）。
 *
 * 为什么需要它：设备端要自己生成签名密钥，就必须自己造一张配套的自签证书。
 * Android 公开 API 没有"生成证书"这个能力（KeyStore 的 setCertificateSubject
 * 需要 API 34+，本工程 minSdk 24 用不了），而引入 BouncyCastle 的证书模块
 * 会给现在只有 1.5MB 的包体增加 4MB 以上。所以这里按 DER 手写 X.509 结构，
 * 只覆盖"自签叶子证书"需要的最小子集：
 *
 *   Certificate ::= SEQUENCE {
 *       tbsCertificate       TBSCertificate,
 *       signatureAlgorithm   AlgorithmIdentifier,   -- sha256WithRSA
 *       signatureValue       BIT STRING
 *   }
 *
 * 生成的证书：CN/OU 用传入的名字，O=NovaDesk，C=CN，含 basicConstraints(CA:FALSE)
 * 与 keyUsage(digitalSignature) 两个扩展，有效期由调用方给定（本工程用 30 年）。
 */
final class DerX509 {

    private DerX509() {}

    private static final String OID_SHA256_RSA = "1.2.840.113549.1.1.11"; // sha256WithRSAEncryption
    private static final String OID_CN         = "2.5.4.3";               // commonName
    private static final String OID_O          = "2.5.4.10";              // organizationName
    private static final String OID_C          = "2.5.4.6";               // countryName
    private static final String OID_BC         = "2.5.29.19";             // basicConstraints
    private static final String OID_KU         = "2.5.29.15";             // keyUsage

    /**
     * 生成一张自签证书，返回 DER 字节。
     *
     * @param kp   密钥对（证书里嵌入公钥，并用私钥对 tbs 部分签名）
     * @param commonName 证书主题的 CN，例如导出器用它标识"设备密钥"
     * @param notBefore  生效时间
     * @param notAfter   失效时间
     */
    static byte[] selfSigned(KeyPair kp, String commonName, Date notBefore, Date notAfter) throws Exception {
        byte[] tbs = tbsCertificate(kp, commonName, notBefore, notAfter);
        Signature sg = Signature.getInstance("SHA256withRSA");
        sg.initSign(kp.getPrivate());
        sg.update(tbs);
        byte[] signature = sg.sign();
        return seq(tbs, algorithmIdentifier(), bitString(signature));
    }

    /** TBSCertificate：证书里被签名的那部分 */
    private static byte[] tbsCertificate(KeyPair kp, String commonName, Date notBefore, Date notAfter) throws Exception {
        byte[] subject = name(commonName);
        return seq(
                explicit(0, integer(BigInteger.valueOf(2))),          // version v3（0 号显式标签）
                integer(randomSerial()),
                algorithmIdentifier(),                                // signature
                subject,                                              // issuer：自签，与 subject 相同
                seq(time(notBefore), time(notAfter)),                 // validity
                subject,                                              // subject
                kp.getPublic().getEncoded(),                          // SubjectPublicKeyInfo（JDK 直接产出该 DER）
                explicit(3, seq(basicConstraints(), keyUsage()))      // extensions（3 号显式标签）
        );
    }

    /** Name ::= SEQUENCE OF RDN；RDN ::= SET OF AttributeTypeAndValue */
    private static byte[] name(String commonName) {
        return seq(
                set(seq(oid(OID_C), printable("CN"))),
                set(seq(oid(OID_O), utf8("NovaDesk"))),
                set(seq(oid(OID_CN), utf8(commonName)))
        );
    }

    /** basicConstraints = CA:FALSE（临界扩展） */
    private static byte[] basicConstraints() {
        byte[] value = seq(booleanValue(false));   // BasicConstraints ::= SEQUENCE { cA BOOLEAN DEFAULT FALSE }
        return seq(oid(OID_BC), booleanValue(true), octetString(value));
    }

    /** keyUsage = digitalSignature（临界扩展）；BIT STRING 只有 bit0 置位，故 7 个填充位 */
    private static byte[] keyUsage() {
        byte[] bits = tlv(0x03, new byte[] { 0x07, (byte) 0x80 });
        return seq(oid(OID_KU), booleanValue(true), octetString(bits));
    }

    /** AlgorithmIdentifier ::= SEQUENCE { algorithm OID, parameters NULL } */
    private static byte[] algorithmIdentifier() {
        return seq(oid(OID_SHA256_RSA), tlv(0x05, new byte[0]));
    }

    /** Time ::= CHOICE { utcTime UTCTime, generalTime GeneralizedTime }，按年份选一种 */
    private static byte[] time(Date date) {
        Calendar c = new GregorianCalendar(new SimpleTimeZone(0, "UTC"));
        c.setTime(date);
        int year = c.get(Calendar.YEAR);
        String full = String.format(Locale.US, "%04d%02d%02d%02d%02d%02dZ",
                year, c.get(Calendar.MONTH) + 1, c.get(Calendar.DAY_OF_MONTH),
                c.get(Calendar.HOUR_OF_DAY), c.get(Calendar.MINUTE), c.get(Calendar.SECOND));
        if (year >= 1950 && year <= 2049) return tlv(0x17, ascii(full.substring(2))); // UTCTime: YYMMDDHHMMSSZ
        return tlv(0x18, ascii(full));                                                // GeneralizedTime: YYYY...
    }

    /** 随机正序列号（64 位，最高位置 0 保证为正） */
    private static BigInteger randomSerial() {
        byte[] b = new byte[8];
        new SecureRandom().nextBytes(b);
        b[0] &= 0x7f;
        BigInteger v = new BigInteger(1, b);
        return v.signum() == 0 ? BigInteger.ONE : v;
    }

    // ---------- DER 基本编码 ----------

    private static byte[] seq(byte[]... parts) { return tlv(0x30, cat(parts)); }   // SEQUENCE
    private static byte[] set(byte[]... parts) { return tlv(0x31, cat(parts)); }   // SET

    private static byte[] tlv(int tag, byte[] value) {
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        bo.write(tag);
        writeLength(bo, value.length);
        bo.write(value, 0, value.length);
        return bo.toByteArray();
    }

    private static void writeLength(ByteArrayOutputStream bo, int n) {
        if (n < 0x80) { bo.write(n); return; }
        int bytes = 1;
        while ((n >>> (bytes * 8)) != 0) bytes++;
        bo.write(0x80 | bytes);
        for (int i = bytes - 1; i >= 0; i--) bo.write((n >>> (i * 8)) & 0xff);
    }

    private static byte[] cat(byte[]... parts) {
        int len = 0;
        for (byte[] p : parts) len += p.length;
        byte[] out = new byte[len];
        int off = 0;
        for (byte[] p : parts) { System.arraycopy(p, 0, out, off, p.length); off += p.length; }
        return out;
    }

    private static byte[] integer(BigInteger v) { return tlv(0x02, v.toByteArray()); }

    private static byte[] oid(String dotted) {
        String[] p = dotted.split("\\.");
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        body.write(Integer.parseInt(p[0]) * 40 + Integer.parseInt(p[1]));
        for (int i = 2; i < p.length; i++) writeBase128(body, new BigInteger(p[i]));
        return tlv(0x06, body.toByteArray());
    }

    private static void writeBase128(ByteArrayOutputStream bo, BigInteger v) {
        int count = Math.max(1, (v.bitLength() + 6) / 7);
        for (int i = count - 1; i >= 0; i--) {
            int b = v.shiftRight(i * 7).intValue() & 0x7f;
            if (i != 0) b |= 0x80;
            bo.write(b);
        }
    }

    private static byte[] utf8(String s) {
        try { return tlv(0x0c, s.getBytes("UTF-8")); }
        catch (Exception e) { throw new RuntimeException(e); }
    }

    private static byte[] printable(String s) { return tlv(0x13, ascii(s)); }

    private static byte[] ascii(String s) {
        byte[] out = new byte[s.length()];
        for (int i = 0; i < s.length(); i++) out[i] = (byte) (s.charAt(i) & 0x7f);
        return out;
    }

    private static byte[] booleanValue(boolean b) { return tlv(0x01, new byte[] { b ? (byte) 0xff : 0x00 }); }

    private static byte[] octetString(byte[] d) { return tlv(0x04, d); }

    /** 签名是"整字节"位串，故填充位数为 0 */
    private static byte[] bitString(byte[] d) { return tlv(0x03, cat(new byte[] { 0 }, d)); }

    /** [n] EXPLICIT */
    private static byte[] explicit(int n, byte[] content) { return tlv(0xA0 | n, content); }
}

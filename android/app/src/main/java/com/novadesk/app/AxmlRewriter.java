package com.novadesk.app;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.HashMap;
import java.util.Map;

/**
 * 二进制 AndroidManifest.xml（AXML）字符串池重写器。
 * AXML = 外层 XML 块(8B) + 字符串池块(第一个) + 资源映射块 + 若干元素块。
 * 元素块只按“字符串索引”引用字符串，因此只要保持索引顺序不变、重建字符串池，
 * 就能把任意字符串替换成任意长度的新值（应用名、版本号、包名），无需等长锚点。
 */
public final class AxmlRewriter {

    private AxmlRewriter() {}

    /** replacements: 旧值 -> 新值（按字符串内容精确匹配，索引保持不变） */
    public static byte[] replace(byte[] axml, Map<String, String> replacements) {
        return replace(axml, replacements, null, null);
    }

    /**
     * 在精确替换的基础上，额外把"以 oldPkg + . 开头"的字符串统一换掉前缀。
     *
     * 为什么非要有这一条：AGP 打包时会自动往清单里塞一条
     *   &lt;包名&gt;.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION
     * 以及配套的 uses-permission。它是"包名 + 后缀"拼成的**一整个字符串**，
     * 精确匹配碰不到它 —— 改完包名之后它会留着旧包名。
     *
     * 功能上不算致命（声明和使用是同一个名字，仍然自洽），但有两个坏处：
     *   1. 看上去就像没改干净；
     *   2. 同一个壳导出的所有应用会共用同一个自定义权限名。
     * 所以补一条前缀规则把它一起带过去。
     *
     * 只认 "oldPkg + ." 这个边界，不做任意子串替换 —— 免得误伤别的字符串。
     */
    public static byte[] replace(byte[] axml, Map<String, String> replacements,
                                 String oldPkg, String newPkg) {
        boolean hasExact = replacements != null && !replacements.isEmpty();
        boolean hasPrefix = oldPkg != null && !oldPkg.isEmpty()
                && newPkg != null && !newPkg.isEmpty() && !oldPkg.equals(newPkg);
        if (!hasExact && !hasPrefix) return axml;
        ByteBuffer buf = ByteBuffer.wrap(axml).order(ByteOrder.LITTLE_ENDIAN);

        // 外层 XML 块：type=0x0003, headerSize=8
        int outerType = buf.getShort(0) & 0xffff;
        if (outerType != 0x0003) throw new IllegalArgumentException("not AXML");
        int poolOff = 8;
        int pType = buf.getShort(poolOff) & 0xffff;
        int pHeader = buf.getShort(poolOff + 2) & 0xffff;
        int pSize = buf.getInt(poolOff + 4);
        if (pType != 0x0001) throw new IllegalArgumentException("string pool not first");

        int stringCount = buf.getInt(poolOff + 8);
        int styleCount = buf.getInt(poolOff + 12);
        int flags = buf.getInt(poolOff + 16);
        int stringsStart = buf.getInt(poolOff + 20);
        if (styleCount != 0) throw new IllegalArgumentException("styled pool unsupported");
        boolean utf8 = (flags & 0x100) != 0;

        int[] offsets = new int[stringCount];
        String[] strings = new String[stringCount];
        int dataStart = poolOff + stringsStart;
        for (int i = 0; i < stringCount; i++) {
            offsets[i] = buf.getInt(poolOff + pHeader + 4 * i);
            int p = dataStart + offsets[i];
            strings[i] = utf8 ? decodeUtf8(buf, p) : decodeUtf16(buf, p);
        }

        // 替换
        boolean changed = false;
        for (int i = 0; i < stringCount; i++) {
            String cur = strings[i];

            if (hasExact) {
                String nv = replacements.get(cur);
                if (nv != null && !nv.equals(cur)) { strings[i] = nv; changed = true; continue; }
            }

            // "旧包名 + 后缀"这种派生字符串：只换前缀
            if (hasPrefix && cur.startsWith(oldPkg + ".")) {
                strings[i] = newPkg + cur.substring(oldPkg.length());
                changed = true;
            }
        }
        if (!changed) return axml;

        // 重建字符串数据区与偏移表
        ByteArrayOutputStream data = new ByteArrayOutputStream();
        int[] newOffsets = new int[stringCount];
        for (int i = 0; i < stringCount; i++) {
            newOffsets[i] = data.size();
            byte[] enc = encodeString(strings[i], utf8);
            data.write(enc, 0, enc.length);
        }
        byte[] dataBytes = data.toByteArray();
        int newStringsStart = pHeader + 4 * stringCount;
        int unpadded = newStringsStart + dataBytes.length;
        int newPoolSize = (unpadded + 3) & ~3;

        ByteArrayOutputStream pool = new ByteArrayOutputStream();
        writeU16(pool, 0x0001);
        writeU16(pool, pHeader);
        writeU32(pool, newPoolSize);
        writeU32(pool, stringCount);
        writeU32(pool, styleCount);
        writeU32(pool, flags);
        writeU32(pool, newStringsStart);
        writeU32(pool, 0); // stylesStart
        for (int i = 0; i < stringCount; i++) writeU32(pool, newOffsets[i]);
        pool.write(dataBytes, 0, dataBytes.length);
        while (pool.size() < newPoolSize) pool.write(0);
        byte[] poolBytes = pool.toByteArray();

        // 其余块原样拼接，更新外层块总长度
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        writeU16(out, outerType);
        writeU16(out, 8);
        int restLen = axml.length - (poolOff + pSize);
        writeU32(out, 8 + poolBytes.length + restLen);
        out.write(poolBytes, 0, poolBytes.length);
        out.write(axml, poolOff + pSize, restLen);
        return out.toByteArray();
    }

    private static String decodeUtf16(ByteBuffer b, int p) {
        int n = b.getShort(p) & 0xffff;
        StringBuilder sb = new StringBuilder(n);
        for (int i = 0; i < n; i++) sb.append((char) (b.getShort(p + 2 + i * 2) & 0xffff));
        return sb.toString();
    }

    private static String decodeUtf8(ByteBuffer b, int p) {
        int skip = b.get(p) & 0xff; // u16len（1~2 字节，简化）
        int q = p + ((skip & 0x80) != 0 ? 2 : 1);
        int n = b.get(q) & 0xff;
        if ((n & 0x80) != 0) n = ((n & 0x7f) << 8) | (b.get(q + 1) & 0xff);
        int s = q + (((b.get(q) & 0x80) != 0) ? 2 : 1);
        byte[] tmp = new byte[n];
        for (int i = 0; i < n; i++) tmp[i] = b.get(s + i);
        return new String(tmp, java.nio.charset.StandardCharsets.UTF_8);
    }

    private static byte[] encodeString(String s, boolean utf8) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        if (utf8) {
            byte[] u = s.getBytes(java.nio.charset.StandardCharsets.UTF_8);
            // AXML 的 UTF-8 串带**两个**长度字段（UTF-16 码元数、UTF-8 字节数），
            // 每个都是 1~2 字节：最高位为 1 表示占两个字节。
            // 原来一律按单字节写 —— 替换值只要到 128 个字符就会写出坏清单。
            // 现在所有替换值都很短，但没理由留这个雷。
            writeLen8(o, s.length());
            writeLen8(o, u.length);
            o.write(u, 0, u.length);
            writeU8(o, 0);
        } else {
            writeU16(o, s.length());
            for (int i = 0; i < s.length(); i++) writeU16(o, s.charAt(i) & 0xffff);
            writeU16(o, 0);
        }
        return o.toByteArray();
    }

    private static void writeU8(ByteArrayOutputStream o, int v) { o.write(v & 0xff); }

    /** AXML UTF-8 串的长度字段：<0x80 一个字节，否则两字节（高位在前，最高位置 1） */
    private static void writeLen8(ByteArrayOutputStream o, int n) {
        if (n < 0x80) {
            writeU8(o, n);
        } else {
            writeU8(o, ((n >> 8) & 0x7f) | 0x80);
            writeU8(o, n & 0xff);
        }
    }

    private static void writeU16(ByteArrayOutputStream o, int v) { o.write(v & 0xff); o.write((v >>> 8) & 0xff); }
    private static void writeU32(ByteArrayOutputStream o, long v) {
        o.write((int) (v & 0xff)); o.write((int) ((v >>> 8) & 0xff));
        o.write((int) ((v >>> 16) & 0xff)); o.write((int) ((v >>> 24) & 0xff));
    }
}

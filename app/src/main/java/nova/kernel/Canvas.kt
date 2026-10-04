package nova.kernel

/**
 * 软件光栅化器 —— NovaDesk 的"显卡"。
 * 完全在内存里对 IntArray 像素缓冲做运算，零安卓依赖。
 */
class Canvas(val w: Int, val h: Int) {

    val px = IntArray(w * h)

    // ---------- 基础 ----------

    fun clear(color: Int) {
        java.util.Arrays.fill(px, color)
    }

    fun clipX(x: Int) = x in 0 until w
    fun clipY(y: Int) = y in 0 until h

    fun set(x: Int, y: Int, color: Int) {
        if (x < 0 || y < 0 || x >= w || y >= h) return
        px[y * w + x] = color
    }

    fun get(x: Int, y: Int): Int {
        if (x < 0 || y < 0 || x >= w || y >= h) return 0
        return px[y * w + x]
    }

    /** 把 src 按 alpha 混合到 (x,y) */
    fun blend(x: Int, y: Int, color: Int) {
        if (x < 0 || y < 0 || x >= w || y >= h) return
        val a = (color ushr 24) and 0xFF
        if (a == 0) return
        if (a == 255) { px[y * w + x] = color; return }
        val i = y * w + x
        val dst = px[i]
        val sr = (color shr 16) and 0xFF
        val sg = (color shr 8) and 0xFF
        val sb = color and 0xFF
        val dr = (dst shr 16) and 0xFF
        val dg = (dst shr 8) and 0xFF
        val db = dst and 0xFF
        val ia = 255 - a
        val r = (sr * a + dr * ia) / 255
        val g = (sg * a + dg * ia) / 255
        val b = (sb * a + db * ia) / 255
        px[i] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
    }

    // ---------- 矩形 ----------

    fun fillRect(x: Int, y: Int, rw: Int, rh: Int, color: Int) {
        val a = (color ushr 24) and 0xFF
        if (a == 0) return
        var x0 = x; var y0 = y; var x1 = x + rw; var y1 = y + rh
        if (x0 < 0) x0 = 0
        if (y0 < 0) y0 = 0
        if (x1 > w) x1 = w
        if (y1 > h) y1 = h
        if (x0 >= x1 || y0 >= y1) return
        if (a == 255) {
            for (yy in y0 until y1) {
                val base = yy * w
                java.util.Arrays.fill(px, base + x0, base + x1, color)
            }
        } else {
            for (yy in y0 until y1) for (xx in x0 until x1) blend(xx, yy, color)
        }
    }

    /** 圆角矩形 */
    fun fillRoundRect(x: Int, y: Int, rw: Int, rh: Int, radius: Int, color: Int) {
        val r = radius.coerceAtMost(minOf(rw, rh) / 2)
        if (r <= 0) { fillRect(x, y, rw, rh, color); return }
        fillRect(x + r, y, rw - 2 * r, rh, color)
        fillRect(x, y + r, r, rh - 2 * r, color)
        fillRect(x + rw - r, y + r, r, rh - 2 * r, color)
        val rr = r * r
        for (dy in 0 until r) {
            for (dx in 0 until r) {
                val ddx = r - dx
                val ddy = r - dy
                if (ddx * ddx + ddy * ddy <= rr) {
                    set(x + dx, y + dy, color)
                    set(x + rw - 1 - dx, y + dy, color)
                    set(x + dx, y + rh - 1 - dy, color)
                    set(x + rw - 1 - dx, y + rh - 1 - dy, color)
                }
            }
        }
    }

    /** 只画 1px 边框的矩形 */
    fun strokeRect(x: Int, y: Int, rw: Int, rh: Int, color: Int) {
        fillRect(x, y, rw, 1, color)
        fillRect(x, y + rh - 1, rw, 1, color)
        fillRect(x, y, 1, rh, color)
        fillRect(x + rw - 1, y, 1, rh, color)
    }

    // ---------- 线 ----------

    fun line(x0: Int, y0: Int, x1: Int, y1: Int, color: Int) {
        var x = x0; var y = y0
        val dx = kotlin.math.abs(x1 - x0)
        val dy = -kotlin.math.abs(y1 - y0)
        val sx = if (x0 < x1) 1 else -1
        val sy = if (y0 < y1) 1 else -1
        var err = dx + dy
        while (true) {
            blend(x, y, color)
            if (x == x1 && y == y1) break
            val e2 = 2 * err
            if (e2 >= dy) { err += dy; x += sx }
            if (e2 <= dx) { err += dx; y += sy }
        }
    }

    // ---------- 文字 ----------

    fun text(s: String, x: Int, y: Int, color: Int, scale: Int = 1) {
        var cx = x
        for (ch in s) {
            Font.draw(this, ch, cx, y, color, scale)
            cx += Font.advance(ch, scale)
        }
    }

    fun textWidth(s: String, scale: Int = 1): Int {
        var t = 0
        for (ch in s) t += Font.advance(ch, scale)
        return t
    }

    /** 在给定宽度内居中绘制 */
    fun textCentered(s: String, cx: Int, y: Int, color: Int, scale: Int = 1) {
        text(s, cx - textWidth(s, scale) / 2, y, color, scale)
    }

    /**
     * 自动换行的段落绘制。返回绘制结束的 y 坐标。
     */
    fun paragraph(s: String, x: Int, y: Int, maxW: Int, color: Int, scale: Int = 1): Int {
        var cy = y
        val lineH = Font.CELL_H * scale + 2 * scale
        var cur = StringBuilder()
        for (ch in s) {
            if (ch == '\n') {
                text(cur.toString(), x, cy, color, scale)
                cur = StringBuilder()
                cy += lineH
                continue
            }
            cur.append(ch)
            if (textWidth(cur.toString(), scale) > maxW) {
                cur.deleteCharAt(cur.length - 1)
                text(cur.toString(), x, cy, color, scale)
                cur = StringBuilder().append(ch)
                cy += lineH
            }
        }
        if (cur.isNotEmpty()) {
            text(cur.toString(), x, cy, color, scale)
            cy += lineH
        }
        return cy
    }

    // ---------- 位块传输 ----------

    /** 把另一个 Canvas 的矩形区域贴到本 Canvas 上（带 alpha） */
    fun blit(src: Canvas, sx: Int, sy: Int, sw: Int, sh: Int, dx: Int, dy: Int) {
        for (yy in 0 until sh) {
            val syy = sy + yy
            if (syy < 0 || syy >= src.h) continue
            for (xx in 0 until sw) {
                val sxx = sx + xx
                if (sxx < 0 || sxx >= src.w) continue
                blend(dx + xx, dy + yy, src.px[syy * src.w + sxx])
            }
        }
    }

    /** 整块替换（不含混合），用于把内核画布推给宿主 */
    fun copyTo(dst: IntArray) {
        System.arraycopy(px, 0, dst, 0, minOf(px.size, dst.size))
    }
}

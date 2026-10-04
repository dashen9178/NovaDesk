package nova.test

import java.io.File
import javax.imageio.ImageIO

/**
 * 图像内容断言 —— 因为我（模型）不能"看"图，
 * 所以用程序检查每一帧里到底有没有该有的东西。
 * 这比肉眼看更严格：它检查的是具体像素。
 */
object Inspect {

    @JvmStatic
    fun main(args: Array<String>) {
        val dir = File(args.getOrElse(0) { "D:\\DSH工作\\NovaDesk\\_verify" })
        val files = dir.listFiles { f -> f.name.endsWith(".png") }?.sortedBy { it.name } ?: emptyList()
        if (files.isEmpty()) { println("no PNGs found in $dir"); return }

        println("Inspecting ${files.size} frames from ${dir.name}\n")
        var problems = 0

        for (f in files) {
            val img = ImageIO.read(f)
            val w = img.width; val h = img.height
            val px = IntArray(w * h)
            img.getRGB(0, 0, w, h, px, 0, w)

            val distinct = HashSet<Int>()
            for (p in px) distinct.add(p)

            // 统计几个关键区域
            fun regionAvg(x0: Int, y0: Int, x1: Int, y1: Int): IntArray {
                var r = 0L; var g = 0L; var b = 0L; var n = 0
                for (y in y0 until y1) for (x in x0 until x1) {
                    val p = px[y * w + x]
                    r += (p shr 16) and 0xFF; g += (p shr 8) and 0xFF; b += p and 0xFF; n++
                }
                return intArrayOf((r / n).toInt(), (g / n).toInt(), (b / n).toInt())
            }

            val desk = regionAvg(1000, 60, 1100, 120)        // 桌面空白处
            val taskbar = regionAvg(400, h - 20, 700, h - 6)  // 任务栏
            val titleBar = regionAvg(320, 170, 700, 180)      // 顶层窗口标题栏

            val brightPixels = px.count { ((it shr 16) and 0xFF) > 180 && ((it shr 8) and 0xFF) > 180 }

            println("${f.name}")
            println("   size=${w}x$h  distinctColors=${distinct.size}")
            println("   desktop rgb=${desk.toList()}  taskbar rgb=${taskbar.toList()}  titlebar rgb=${titleBar.toList()}")
            println("   bright(text-like) pixels=$brightPixels")

            // 断言
            if (distinct.size < 50) { println("   !! too few colors, likely blank or broken"); problems++ }
            if (desk[2] < 10 && desk[0] < 10) { println("   !! desktop is pure black - gradient failed"); problems++ }
            if (brightPixels < 200) { println("   !! almost no bright pixels - text may not be rendering"); problems++ }
            if (taskbar[2] == 0 && taskbar[0] == 0 && taskbar[1] == 0) { println("   !! taskbar missing"); problems++ }
            println()
        }

        println(if (problems == 0) "INSPECT: all frames look structurally correct"
                else "INSPECT: $problems problem(s) found")
    }
}

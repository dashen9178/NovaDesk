/**
 * NovaDesk 内核核心（JavaScript 版）
 *
 * 对应 Kotlin 的 NovaHost.kt / Canvas.kt / Font.kt / Theme.kt / Window.kt / Vfs.kt / App.kt
 * 机械式逐行等价翻译，行为完全一致。
 *
 * 这个文件不引用任何浏览器或安卓 API —— 它是纯逻辑。
 *
 * ---------------------------------------------------------------------------
 * 翻译约定（Kotlin -> JS），务必对照理解：
 *
 *   Int / 颜色：Kotlin 的 Int 是 32 位有符号。0xFF1B2838.toInt() 在 Kotlin 里是负数，
 *               JS 里直接写 0xFF1B2838 就是正的 Number（超过 2^31）。本文件一律用
 *               无符号 32 位数值表示颜色。
 *               Kotlin `shr`  = 算术右移（符号扩展）-> JS `>>`
 *               Kotlin `ushr` = 逻辑右移            -> JS `>>>`
 *               Kotlin `and` / `or` 是按 32 位算的。JS 的 `&`/`|` 先把操作数转成
 *               32 位有符号，所以结果可能是负数 —— 写回 Int32Array 时数值位相同，
 *               但若要当"无符号颜色"用，需要 `>>> 0`。
 *
 *   IntArray(n)                -> Int32Array(n)
 *   Arrays.fill(a, v)          -> a.fill(v)
 *   Arrays.fill(a, f, t, v)    -> a.fill(v, f, t)
 *   System.arraycopy(s,0,d,0,n)-> d.set(s.subarray(0, n))
 *   `a until b`（不含 b）      -> for (let i = a; i < b; i++)
 *   coerceAtMost -> Math.min, coerceAtLeast -> Math.max, coerceIn -> clamp
 *   lateinit var -> 初始化为 null 的普通属性，之后由外部注入
 *   companion object { const val X } -> 类上的静态属性，如 App.PHASE_DOWN = 0
 *   StringBuilder.append -> 字符串拼接；deleteCharAt(len-1) -> slice(0, -1)
 *   Map/Set -> Map/Set；HashMap/HashSet 的迭代顺序是实现细节，本文件按插入顺序
 *
 *   Kotlin 的整数除法是"向零截断"，JS 的 `/` 是浮点除法。
 *   凡是 Kotlin 里两个 Int 相除的地方，本文件都显式用 `| 0` 或 Math.trunc 截断。
 * ---------------------------------------------------------------------------
 */

/* ================================================================== *
 * 1. nova.abi —— 系统调用 ABI
 * ================================================================== */

/**
 * 宿主接口说明（Kotlin 的 `interface NovaHost`）。
 *
 * 按题设要求，这里**不导出** NovaHost 的任何类/对象，只在此以 JSDoc 声明宿主
 * （浏览器端）必须实现的方法，供 host.js 参照实现：
 *
 *   // ---------- 1. 显存 ----------
 *   width                        : number  宽度（像素，已按屏幕方向旋转到横屏）
 *   height                       : number  高度（像素）
 *   present(argb)                : void    提交一帧。argb 长度必须 >= width*height 的
 *                                           ARGB_8888 像素数组（行优先）。内核自己在这块
 *                                           内存里画完所有东西，宿主只负责贴到屏幕上。
 *
 *   // ---------- 2. 时间 ----------
 *   clockMs()                    : number  单调时钟，毫秒。仅用于动画计时，
 *                                           不保证与墙上时间一致。
 *
 *   // ---------- 3. 输入 ----------
 *   pollEvent()                  : NovaEvent | null
 *                                           取出一个待处理输入事件，没有则返回 null。
 *                                           内核每帧轮询直到返回 null。
 *
 *   // ---------- 4. 存储 ----------
 *   open(path, create)           : number  在沙箱私有目录内打开文件。
 *                                           path 是 NovaDesk 内部路径，如 "/home/note.txt"。
 *                                           返回 fd，失败返回负数。
 *   read(fd, maxLen)             : Uint8Array | null
 *   write(fd, data)              : number  写入的字节数
 *   close(fd)                    : void
 *   list(dir)                    : string[]
 *
 *   // ---------- 5. 退出 ----------
 *   exit(code)                   : void
 */

/**
 * 输入事件（Kotlin 的 `sealed class NovaEvent` 及其 6 个 data class 子类）。
 * 坐标已转换为 NovaDesk 屏幕坐标系（左上角原点，横屏）。
 *
 * JS 里用"基类 + 挂在其上的子类构造器"来表达 Kotlin 的嵌套 sealed 子类，
 * 这样消费方既可以写 `new NovaEvent.Down(x, y, t)`（对应 Kotlin 的
 * `NovaEvent.Down(...)`），也可以写 `e instanceof NovaEvent.Down`。
 */
export class NovaEvent {}

/** 触摸按下 */
export class NovaDown extends NovaEvent {
  constructor(x, y, t) {
    super();
    this.x = x;
    this.y = y;
    this.t = t;
  }
}

/** 触摸移动 */
export class NovaMove extends NovaEvent {
  constructor(x, y, t) {
    super();
    this.x = x;
    this.y = y;
    this.t = t;
  }
}

/** 触摸抬起 */
export class NovaUp extends NovaEvent {
  constructor(x, y, t) {
    super();
    this.x = x;
    this.y = y;
    this.t = t;
  }
}

/** 物理按键。code 见 NovaKey。 */
export class NovaKeyEvent extends NovaEvent {
  constructor(code, down, t) {
    super();
    this.code = code;
    this.down = down;
    this.t = t;
  }
}

/** 文本输入（软键盘、回车、退格）。内核把它转给焦点窗口。 */
export class NovaText extends NovaEvent {
  constructor(ch) {
    super();
    this.ch = ch;
  }
}

/** 屏幕尺寸变化（旋转/分屏）。 */
export class NovaResize extends NovaEvent {
  constructor(w, h) {
    super();
    this.w = w;
    this.h = h;
  }
}

// Kotlin 里这些是 NovaEvent 的嵌套子类；这里挂成静态属性，
// 既保持 NovaEvent.Down / .Move / .Up / .Key / .Text / .Resize 的写法，
// 也让 `instanceof NovaEvent.Down` 正常工作。
NovaEvent.Down = NovaDown;
NovaEvent.Move = NovaMove;
NovaEvent.Up = NovaUp;
NovaEvent.Key = NovaKeyEvent;
NovaEvent.Text = NovaText;
NovaEvent.Resize = NovaResize;

/** 物理按键码（Kotlin 的 `object NovaKey`） */
export const NovaKey = {
  BACK: 1,
  ENTER: 2,
  ESC: 3,
  UP: 4,
  DOWN: 5,
  LEFT: 6,
  RIGHT: 7,
  TAB: 8,
};

/* ================================================================== *
 * 2. nova.kernel.Theme —— 视觉规范
 * ================================================================== */

/**
 * NovaDesk 视觉规范 —— 所有颜色、尺寸集中在这里。
 * 想换皮肤只改这个文件。
 *
 * 注意：Kotlin 里 `0xFF1B2838.toInt()` 是有符号负数，JS 里十六进制字面量
 * 本身就是对应的无符号正值，数值位完全一致。
 */
export const Theme = {
  // 桌面背景：深空渐变的两端色
  DESK_TOP: 0xFF1B2838,
  DESK_BOTTOM: 0xFF0D1620,

  // 窗口
  WIN_BG: 0xF01E2228,
  WIN_TITLE_ACTIVE: 0xFF2D3748,
  WIN_TITLE_INACTIVE: 0xFF252A31,
  WIN_BORDER_ACTIVE: 0xFF4A90D9,
  WIN_BORDER_INACTIVE: 0xFF3A4048,
  WIN_SHADOW: 0x50000000,

  // 文字
  TEXT: 0xFFE6EDF3,
  TEXT_DIM: 0xFF8B949E,
  TEXT_TITLE: 0xFFFFFFFF,

  // 控件
  BTN_CLOSE: 0xFFE05252,
  BTN_MIN: 0xFFE0A852,
  BTN_MAX: 0xFF52C452,
  ACCENT: 0xFF4A90D9,

  // 任务栏
  TASKBAR_BG: 0xF01A1F26,
  TASKBAR_H: 30,
  TASKBAR_ITEM: 0xFF2A313A,
  TASKBAR_ITEM_ACTIVE: 0xFF3A4756,

  // 窗口度量（虚拟分辨率是 800x450，比原来的 1280x720 小，
  // 这样中文字形相对屏幕更大，手机上才看得清）
  TITLE_H: 20,
  BORDER: 1,
  MIN_W: 120,
  MIN_H: 80,
};

/* ================================================================== *
 * 3. nova.kernel.Canvas —— 软件光栅化器
 * ================================================================== */

/**
 * 软件光栅化器 —— NovaDesk 的"显卡"。
 * 完全在内存里对 IntArray 像素缓冲做运算，零安卓依赖。
 */
export class Canvas {
  constructor(w, h) {
    this.w = w;
    this.h = h;
    /** Kotlin: val px = IntArray(w * h) */
    this.px = new Int32Array(w * h);
  }

  // ---------- 基础 ----------

  /** Kotlin: java.util.Arrays.fill(px, color) */
  clear(color) {
    this.px.fill(color);
  }

  /** Kotlin: `x in 0 until w`（until 不含右端） */
  clipX(x) { return x >= 0 && x < this.w; }

  /** Kotlin: `y in 0 until h` */
  clipY(y) { return y >= 0 && y < this.h; }

  set(x, y, color) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    this.px[y * this.w + x] = color;
  }

  get(x, y) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return 0;
    return this.px[y * this.w + x];
  }

  /** 把 src 按 alpha 混合到 (x,y) */
  blend(x, y, color) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    // Kotlin: val a = (color ushr 24) and 0xFF   —— ushr 是逻辑右移 -> JS >>>
    const a = (color >>> 24) & 0xFF;
    if (a === 0) return;
    if (a === 255) {
      this.px[y * this.w + x] = color;
      return;
    }
    const i = y * this.w + x;
    const dst = this.px[i];
    // Kotlin: (color shr 16) and 0xFF    —— shr 是算术右移 -> JS >>
    // 对 32 位值先 >> 再 & 0xFF，与 Kotlin 逐位一致。
    const sr = (color >> 16) & 0xFF;
    const sg = (color >> 8) & 0xFF;
    const sb = color & 0xFF;
    const dr = (dst >> 16) & 0xFF;
    const dg = (dst >> 8) & 0xFF;
    const db = dst & 0xFF;
    const ia = 255 - a;
    // Kotlin 的 Int/Int 是向零截断除法；`| 0` 完成同样的截断（结果远小于 2^31，安全）
    const r = (sr * a + dr * ia) / 255 | 0;
    const g = (sg * a + dg * ia) / 255 | 0;
    const b = (sb * a + db * ia) / 255 | 0;
    // Kotlin: (0xFF shl 24) or (r shl 16) or (g shl 8) or b
    // 0xFF << 24 在 JS 里是负数（0xFF000000 超出有符号 32 位范围），
    // 但 Int32Array 存的本来就是有符号 32 位，数值位与 Kotlin 完全一致。
    this.px[i] = (0xFF << 24) | (r << 16) | (g << 8) | b;
  }

  // ---------- 矩形 ----------

  fillRect(x, y, rw, rh, color) {
    const a = (color >>> 24) & 0xFF;
    if (a === 0) return;
    let x0 = x, y0 = y, x1 = x + rw, y1 = y + rh;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > this.w) x1 = this.w;
    if (y1 > this.h) y1 = this.h;
    if (x0 >= x1 || y0 >= y1) return;
    if (a === 255) {
      for (let yy = y0; yy < y1; yy++) {
        const base = yy * this.w;
        // Kotlin: java.util.Arrays.fill(px, base + x0, base + x1, color)
        this.px.fill(color, base + x0, base + x1);
      }
    } else {
      for (let yy = y0; yy < y1; yy++) {
        for (let xx = x0; xx < x1; xx++) this.blend(xx, yy, color);
      }
    }
  }

  /** 圆角矩形 */
  fillRoundRect(x, y, rw, rh, radius, color) {
    // Kotlin: radius.coerceAtMost(minOf(rw, rh) / 2)   —— 整数除法，向零截断
    const r = Math.min(radius, (Math.min(rw, rh) / 2 | 0));
    if (r <= 0) { this.fillRect(x, y, rw, rh, color); return; }
    this.fillRect(x + r, y, rw - 2 * r, rh, color);
    this.fillRect(x, y + r, r, rh - 2 * r, color);
    this.fillRect(x + rw - r, y + r, r, rh - 2 * r, color);
    const rr = r * r;
    for (let dy = 0; dy < r; dy++) {
      for (let dx = 0; dx < r; dx++) {
        const ddx = r - dx;
        const ddy = r - dy;
        if (ddx * ddx + ddy * ddy <= rr) {
          this.set(x + dx, y + dy, color);
          this.set(x + rw - 1 - dx, y + dy, color);
          this.set(x + dx, y + rh - 1 - dy, color);
          this.set(x + rw - 1 - dx, y + rh - 1 - dy, color);
        }
      }
    }
  }

  /** 只画 1px 边框的矩形 */
  strokeRect(x, y, rw, rh, color) {
    this.fillRect(x, y, rw, 1, color);
    this.fillRect(x, y + rh - 1, rw, 1, color);
    this.fillRect(x, y, 1, rh, color);
    this.fillRect(x + rw - 1, y, 1, rh, color);
  }

  // ---------- 线 ----------

  /** Bresenham 直线 */
  line(x0, y0, x1, y1, color) {
    let x = x0, y = y0;
    const dx = Math.abs(x1 - x0);
    const dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1;
    const sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      this.blend(x, y, color);
      if (x === x1 && y === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x += sx; }
      if (e2 <= dx) { err += dx; y += sy; }
    }
  }

  // ---------- 文字 ----------

  text(s, x, y, color, scale = 1) {
    let cx = x;
    // Kotlin `for (ch in s)` 遍历 UTF-16 码元；JS 的 for...of 遍历码位。
    // 本字库只收录 ASCII，两者一致。
    for (const ch of s) {
      Font.draw(this, ch, cx, y, color, scale);
      cx += Font.advance(ch, scale);
    }
  }

  textWidth(s, scale = 1) {
    let t = 0;
    for (const ch of s) t += Font.advance(ch, scale);
    return t;
  }

  /** 在给定宽度内居中绘制 */
  textCentered(s, cx, y, color, scale = 1) {
    // Kotlin: cx - textWidth(s, scale) / 2   —— 整数除法，向零截断
    this.text(s, cx - (this.textWidth(s, scale) / 2 | 0), y, color, scale);
  }

  /**
   * 自动换行的段落绘制。返回绘制结束的 y 坐标。
   *
   * Kotlin 用 StringBuilder 累积当前行；这里用普通字符串，
   * append -> `+=`，deleteCharAt(length-1) -> slice(0, -1)。
   */
  paragraph(s, x, y, maxW, color, scale = 1) {
    let cy = y;
    const lineH = Font.CELL_H * scale + 2 * scale;
    let cur = '';
    for (const ch of s) {
      if (ch === '\n') {
        this.text(cur, x, cy, color, scale);
        cur = '';
        cy += lineH;
        continue;
      }
      cur += ch;
      if (this.textWidth(cur, scale) > maxW) {
        // Kotlin: cur.deleteCharAt(cur.length - 1)
        cur = cur.slice(0, -1);
        this.text(cur, x, cy, color, scale);
        // Kotlin: cur = StringBuilder().append(ch)
        cur = ch;
        cy += lineH;
      }
    }
    // Kotlin: cur.isNotEmpty()
    if (cur.length > 0) {
      this.text(cur, x, cy, color, scale);
      cy += lineH;
    }
    return cy;
  }

  // ---------- 位块传输 ----------

  /** 把另一个 Canvas 的矩形区域贴到本 Canvas 上（带 alpha） */
  blit(src, sx, sy, sw, sh, dx, dy) {
    for (let yy = 0; yy < sh; yy++) {
      const syy = sy + yy;
      if (syy < 0 || syy >= src.h) continue;
      for (let xx = 0; xx < sw; xx++) {
        const sxx = sx + xx;
        if (sxx < 0 || sxx >= src.w) continue;
        this.blend(dx + xx, dy + yy, src.px[syy * src.w + sxx]);
      }
    }
  }

  /** 整块替换（不含混合），用于把内核画布推给宿主 */
  copyTo(dst) {
    // Kotlin: System.arraycopy(px, 0, dst, 0, minOf(px.size, dst.size))
    const n = Math.min(this.px.length, dst.length);
    dst.set(this.px.subarray(0, n));
  }

  // ---------- 艺术字 ----------

  /**
   * 把遮罩按行水平错位后染色画出来 —— 斜体的关键。
   * 越靠上的行偏移越大，视觉上就"往右倾"。
   */
  _blitShearTint(mask, dx, dy, color, slant) {
    const H = mask.h, W = mask.w;
    for (let y = 0; y < H; y++) {
      const shift = Math.round(slant * (H - 1 - y));
      const base = y * W;
      for (let x = 0; x < W; x++) {
        if ((mask.px[base + x] >>> 24) !== 0) {
          this.blend(dx + x + shift, dy + y, color);
        }
      }
    }
  }

  /**
   * 艺术字：粗体 + 斜体 + 描边。欢迎页那行大字用它。
   *
   * 做法：先把文字（加粗）画进一张临时遮罩，然后
   *   1) 用遮罩在周围一圈偏移位置涂描边色 → 轮廓
   *   2) 居中涂填充色，并按行剪切 → 斜体
   *
   * 之所以绕一层遮罩，是因为斜体需要按行错位，
   * 而 Font.draw 内部已经把每一行画完了，外面插不进去。
   *
   * 返回渲染好的画布，方便调用方缓存（这个函数不便宜）。
   */
  renderTextArt(s, fill, outline, scale, slant) {
    scale = scale || 1;
    slant = (slant === undefined) ? 0.32 : slant;

    const lh = Math.max(Font.CJK_H || 0, Font.CELL_H);
    const tw = this.textWidth(s, scale);
    const th = lh * scale;
    const pad = 4 + Math.ceil(slant * th) + 2;
    const W = tw + pad * 2;
    const H = th + pad * 2;

    // 1) 字形遮罩（加粗 = 同一点阵多次微偏移叠加）
    const mask0 = new Canvas(W, H);
    const boldOffs = [[0, 0], [1, 0], [2, 0], [1, 1], [2, 1]];
    for (const o of boldOffs) mask0.text(s, pad + o[0], pad + o[1], 0xFFFFFFFF, scale);

    // 2) 裁到真实墨迹范围。
    //    不裁的话，画布里绝大部分是行高留白（ASCII 只有 7 行高，
    //    而行高按中文算有 23 行），结果字看着比预期小，居中也会偏。
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (let y = 0; y < H; y++) {
      const base = y * W;
      for (let x = 0; x < W; x++) {
        if ((mask0.px[base + x] >>> 24) !== 0) {
          if (x < x0) x0 = x;
          if (x > x1) x1 = x;
          if (y < y0) y0 = y;
          if (y > y1) y1 = y;
        }
      }
    }
    if (x1 < 0) return new Canvas(1, 1);      // 空字符串

    const mw = x1 - x0 + 1;
    const mh = y1 - y0 + 1;
    const mask = new Canvas(mw, mh);
    for (let y = 0; y < mh; y++) {
      for (let x = 0; x < mw; x++) {
        mask.px[y * mw + x] = mask0.px[(y + y0) * W + (x + x0)];
      }
    }

    // 3) 描边 + 填充。斜体是"越靠上的行越往右偏"，
    //    所以左上角需要额外的水平空间，否则顶行会被切掉。
    const r = Math.max(2, Math.round(scale * 0.5));
    const maxShift = Math.ceil(slant * (mh - 1));
    const art = new Canvas(mw + maxShift + 2 * r + 2, mh + 2 * r + 2);
    const ox = r, oy = r;

    if (outline !== undefined && outline !== null) {
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          if (dx === 0 && dy === 0) continue;
          if (dx * dx + dy * dy > r * r + 1) continue;
          art._blitShearTint(mask, ox + dx, oy + dy, outline, slant);
        }
      }
    }
    art._blitShearTint(mask, ox, oy, fill, slant);
    return art;
  }

  /** 居中绘制已经渲染好的艺术字画布 */
  blitArtCentered(art, cx, cy) {
    this.blit(art, 0, 0, art.w, art.h, cx - (art.w >> 1), cy - (art.h >> 1));
  }
}

/* ================================================================== *
 * 4. nova.kernel.Font —— 5x7 点阵字库
 * ================================================================== */

/**
 * 点阵字库 —— NovaDesk 自己的字体系统。
 *
 * 每个 ASCII 字形用 5x7 点阵描述，写成字符串便于阅读和修改。
 * '#' 表示亮点。字模在需要时惰性光栅化成位图缓存。
 *
 * 这是 NovaDesk 的资产，不依赖任何系统字体。
 *
 * 说明：Kotlin 里这是 `object Font`。JS 里导出为一个普通对象；
 * 同时导出构造函数，使 `Font.GLYPH_W` / `Font.draw(...)` 的调用方式与 Kotlin 一致。
 */
export function Font() {}

/** Kotlin: const val GLYPH_W = 5 */
Font.GLYPH_W = 5;
/** Kotlin: const val GLYPH_H = 7 */
Font.GLYPH_H = 7;
/** ASCII 字符单元宽度（含字间距 1px） */
Font.CELL_W = 6;

/**
 * CJK 点阵字体的度量，由 kernel-cjk.js 在加载时填入。
 *
 * 中文字形是方形的，比 ASCII 的 5x7 大得多，所以行高必须以它为准，
 * 否则中文会上下叠在一起。
 */
Font.CJK_W = 0;
Font.CJK_H = 0;
/** 码位 -> 每行一个整数的点阵位图（最高位是最左边的像素） */
Font.cjk = null;
/** ASCII 字形在行内需要向下偏移多少，才能和中文的基线对齐 */
Font.ASCII_DY = 0;

/**
 * 字符单元高度。中文字比英文高，行距要按中文算，
 * 内核和各个应用都直接用这个值排版。
 */
Font.CELL_H = 7;

/** 这个字符是不是中日韩表意文字（需要用点阵中文库绘制） */
Font.isCJK = function (ch) {
  if (!ch) return false;
  const c = ch.codePointAt(0);
  return (c >= 0x2E80 && c <= 0x9FFF)     // 部首扩展 + 基本区
      || (c >= 0xF900 && c <= 0xFAFF)     // 兼容表意文字
      || (c >= 0xFF00 && c <= 0xFFEF)     // 全角标点
      || (c >= 0x3000 && c <= 0x303F);    // 中日韩标点
};

/**
 * Kotlin:
 *   private val raw: Map<Char, Array<String>> = mapOf(...)
 * JS 里用普通对象按字符作键，等价于 Map<Char, Array<String>>。
 */
Font.raw = {
  ' ': ['     ', '     ', '     ', '     ', '     ', '     ', '     '],
  '!': ['  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '     ', '  #  '],
  '"': [' # # ', ' # # ', '     ', '     ', '     ', '     ', '     '],
  '#': [' # # ', ' # # ', '#####', ' # # ', '#####', ' # # ', ' # # '],
  '$': ['  #  ', ' ####', '# #  ', ' ### ', '  # #', '#### ', '  #  '],
  '%': ['##  #', '##  #', '   # ', '  #  ', ' #   ', '#  ##', '#  ##'],
  '&': [' ##  ', '#  # ', '#  # ', ' ##  ', '#  # ', '#   #', ' ## #'],
  "'": ['  #  ', '  #  ', '     ', '     ', '     ', '     ', '     '],
  '(': ['   # ', '  #  ', ' #   ', ' #   ', ' #   ', '  #  ', '   # '],
  ')': [' #   ', '  #  ', '   # ', '   # ', '   # ', '  #  ', ' #   '],
  '*': ['     ', '#  # ', ' ##  ', '#### ', ' ##  ', '#  # ', '     '],
  '+': ['     ', '  #  ', '  #  ', '#####', '  #  ', '  #  ', '     '],
  ',': ['     ', '     ', '     ', '     ', '     ', '  ## ', ' ##  '],
  '-': ['     ', '     ', '     ', '#####', '     ', '     ', '     '],
  '.': ['     ', '     ', '     ', '     ', '     ', ' ##  ', ' ##  '],
  '/': ['    #', '   # ', '   # ', '  #  ', ' #   ', ' #   ', '#    '],
  '0': [' ### ', '#   #', '#  ##', '# # #', '##  #', '#   #', ' ### '],
  '1': ['  #  ', ' ##  ', '  #  ', '  #  ', '  #  ', '  #  ', ' ### '],
  '2': [' ### ', '#   #', '    #', '   # ', '  #  ', ' #   ', '#####'],
  '3': ['#####', '   # ', '  #  ', '   # ', '    #', '#   #', ' ### '],
  '4': ['   # ', '  ## ', ' # # ', '#  # ', '#####', '   # ', '   # '],
  '5': ['#####', '#    ', '#### ', '    #', '    #', '#   #', ' ### '],
  '6': ['  ## ', ' #   ', '#    ', '#### ', '#   #', '#   #', ' ### '],
  '7': ['#####', '    #', '   # ', '  #  ', ' #   ', ' #   ', ' #   '],
  '8': [' ### ', '#   #', '#   #', ' ### ', '#   #', '#   #', ' ### '],
  '9': [' ### ', '#   #', '#   #', ' ####', '    #', '   # ', ' ##  '],
  ':': ['     ', ' ##  ', ' ##  ', '     ', ' ##  ', ' ##  ', '     '],
  ';': ['     ', ' ##  ', ' ##  ', '     ', ' ##  ', ' ##  ', ' #   '],
  '<': ['   # ', '  #  ', ' #   ', '#    ', ' #   ', '  #  ', '   # '],
  '=': ['     ', '     ', '#####', '     ', '#####', '     ', '     '],
  '>': [' #   ', '  #  ', '   # ', '    #', '   # ', '  #  ', ' #   '],
  '?': [' ### ', '#   #', '    #', '   # ', '  #  ', '     ', '  #  '],
  '@': [' ### ', '#   #', '# ###', '# # #', '# ###', '#    ', ' ### '],
  'A': [' ### ', '#   #', '#   #', '#####', '#   #', '#   #', '#   #'],
  'B': ['#### ', '#   #', '#   #', '#### ', '#   #', '#   #', '#### '],
  'C': [' ### ', '#   #', '#    ', '#    ', '#    ', '#   #', ' ### '],
  'D': ['###  ', '#  # ', '#   #', '#   #', '#   #', '#  # ', '###  '],
  'E': ['#####', '#    ', '#    ', '#### ', '#    ', '#    ', '#####'],
  'F': ['#####', '#    ', '#    ', '#### ', '#    ', '#    ', '#    '],
  'G': [' ### ', '#   #', '#    ', '# ###', '#   #', '#   #', ' ####'],
  'H': ['#   #', '#   #', '#   #', '#####', '#   #', '#   #', '#   #'],
  'I': [' ### ', '  #  ', '  #  ', '  #  ', '  #  ', '  #  ', ' ### '],
  'J': ['    #', '    #', '    #', '    #', '#   #', '#   #', ' ### '],
  'K': ['#   #', '#  # ', '# #  ', '##   ', '# #  ', '#  # ', '#   #'],
  'L': ['#    ', '#    ', '#    ', '#    ', '#    ', '#    ', '#####'],
  'M': ['#   #', '## ##', '# # #', '# # #', '#   #', '#   #', '#   #'],
  'N': ['#   #', '##  #', '# # #', '# # #', '#  ##', '#   #', '#   #'],
  'O': [' ### ', '#   #', '#   #', '#   #', '#   #', '#   #', ' ### '],
  'P': ['#### ', '#   #', '#   #', '#### ', '#    ', '#    ', '#    '],
  'Q': [' ### ', '#   #', '#   #', '#   #', '# # #', '#  # ', ' ## #'],
  'R': ['#### ', '#   #', '#   #', '#### ', '# #  ', '#  # ', '#   #'],
  'S': [' ####', '#    ', '#    ', ' ### ', '    #', '    #', '#### '],
  'T': ['#####', '  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '  #  '],
  'U': ['#   #', '#   #', '#   #', '#   #', '#   #', '#   #', ' ### '],
  'V': ['#   #', '#   #', '#   #', '#   #', '#   #', ' # # ', '  #  '],
  'W': ['#   #', '#   #', '#   #', '# # #', '# # #', '## ##', '#   #'],
  'X': ['#   #', '#   #', ' # # ', '  #  ', ' # # ', '#   #', '#   #'],
  'Y': ['#   #', '#   #', ' # # ', '  #  ', '  #  ', '  #  ', '  #  '],
  'Z': ['#####', '    #', '   # ', '  #  ', ' #   ', '#    ', '#####'],
  '[': [' ### ', ' #   ', ' #   ', ' #   ', ' #   ', ' #   ', ' ### '],
  '\\': ['#    ', ' #   ', ' #   ', '  #  ', '   # ', '   # ', '    #'],
  ']': [' ### ', '   # ', '   # ', '   # ', '   # ', '   # ', ' ### '],
  '^': ['  #  ', ' # # ', '#   #', '     ', '     ', '     ', '     '],
  '_': ['     ', '     ', '     ', '     ', '     ', '     ', '#####'],
  '`': [' #   ', '  #  ', '     ', '     ', '     ', '     ', '     '],
  '{': ['   # ', '  #  ', '  #  ', ' #   ', '  #  ', '  #  ', '   # '],
  '|': ['  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '  #  '],
  '}': [' #   ', '  #  ', '  #  ', '   # ', '  #  ', '  #  ', ' #   '],
  '~': ['     ', ' #   ', '# # #', '   # ', '     ', '     ', '     '],
};

/** 未收录字符的替代字形（一个空心方块） */
Font.tofu = ['#####', '#   #', '#   #', '#   #', '#   #', '#   #', '#####'];

/**
 * Kotlin: fun advance(ch: Char, scale: Int): Int = CELL_W * scale
 *
 * 中文是"全角"：宽度等于一个中文字形的宽度，不再用 ASCII 的 6px。
 */
Font.advance = function (ch, scale) {
  if (this.isCJK(ch)) return this.CJK_W * scale;
  return 6 * scale;
};

/**
 * Kotlin: Char.uppercaseChar() —— 字库只收录 ASCII，这里做等价的大写映射。
 * 代理对（surrogate pair）对应的码位不受影响，与原版行为一致。
 */
function uppercaseChar(ch) {
  if (ch.length !== 1) return ch;
  const c = ch.charCodeAt(0);
  if (c >= 0x61 && c <= 0x7A) return String.fromCharCode(c - 32);
  return ch;
}

/**
 * 直接在画布上绘制字形。逐点绘制，简单可靠。
 * scale 为整数倍放大，保证像素锐利。
 *
 * Kotlin: val glyph = raw[ch] ?: raw[ch.uppercaseChar()] ?: tofu
 * 这里**不能**用 `||` —— 空数组虽然为真，但这个映射表里所有值都是 7 行数组，
 * 用 `||` 恰好也能工作；不过 `raw[ch]` 取不到时是 undefined，语义上属于 elvis，
 * 因此写成显式的 null/undefined 判断，与 Kotlin 逐级回退完全对应。
 */
Font.draw = function (c, ch, x, y, color, scale) {
  if (ch === '\n' || ch === '\r' || ch === '\t') return;

  // ---- 中文：查点阵表 ----
  if (this.isCJK(ch)) {
    const rows = this.cjk ? this.cjk[ch.codePointAt(0)] : undefined;
    const w = this.CJK_W, h = this.CJK_H;
    if (rows === undefined) {
      // 字库没收录这个字：画一个方框，让缺字看得见（而不是静默消失）
      const t = Math.max(1, scale);
      c.strokeRect(x, y, w * scale, h * scale, color);
      c.fillRect(x + w * scale / 2 - t, y + h * scale / 2 - t, t * 2, t * 2, color);
      return;
    }
    for (let r = 0; r < h; r++) {
      const bits = rows[r];
      if (!bits) continue;              // 空行直接跳过
      for (let col = 0; col < w; col++) {
        if ((bits >> (w - 1 - col)) & 1) {
          if (scale === 1) c.blend(x + col, y + r, color);
          else c.fillRect(x + col * scale, y + r * scale, scale, scale, color);
        }
      }
    }
    return;
  }

  // ---- ASCII：查 5x7 字模 ----
  let glyph = this.raw[ch];
  if (glyph === undefined) glyph = this.raw[uppercaseChar(ch)];
  if (glyph === undefined) glyph = this.tofu;
  const dy = this.ASCII_DY;
  for (let row = 0; row < 7; row++) {
    const line = glyph[row];
    for (let col = 0; col < 5; col++) {
      if (col < line.length && line[col] === '#') {
        if (scale === 1) {
          c.blend(x + col, y + row + dy, color);
        } else {
          c.fillRect(x + col * scale, y + (row + dy) * scale, scale, scale, color);
        }
      }
    }
  }
};

/* ================================================================== *
 * 5. nova.kernel.App —— 应用基类
 * ================================================================== */

/**
 * 应用基类 —— NovaDesk 的"可执行格式"。
 *
 * 每个应用往自己的 surface 上画界面，通过 onTouch 收本地坐标事件。
 * 应用完全不知道安卓存在，也不会自己去画窗口标题栏 —— 那是内核的职责。
 *
 * Kotlin 里是 `abstract class App`；JS 没有 abstract，draw 以抛错表达
 * "子类必须实现"的约束。
 */
export class App {
  constructor(name) {
    this.name = name;
    /** 所属窗口，由内核在创建时注入（Kotlin: lateinit var win: Window） */
    this.win = null;
    /** 内核提供的系统服务（Kotlin: lateinit var sys: Kernel） */
    this.sys = null;
  }

  /** 应用图标字符（点阵字库能画出来的符号） */
  icon() { return '?'; }

  /** 窗口初始尺寸。Kotlin: intArrayOf(460, 300) */
  defaultSize() { return [460, 300]; }

  /** 每帧绘制。内核会在需要时调用。 */
  draw(c) {
    throw new Error('App.draw(c) is abstract: ' + this.name);
  }

  /** 每帧更新逻辑。返回 true 表示需要重绘。 */
  update(dtMs) { return false; }

  /** 触摸事件，坐标是 surface 本地坐标。返回 true 表示消费了事件。 */
  onTouch(x, y, phase) { return false; }

  /** 键盘输入（字符，0 表示功能键）。 */
  onChar(ch) { return false; }

  onKey(code) { return false; }

  /** 窗口尺寸变化 */
  onResize(w, h) {}

  /** 窗口关闭时的清理 */
  onClose() {}
}

// Kotlin: companion object { const val PHASE_DOWN = 0; ... }
App.PHASE_DOWN = 0;
App.PHASE_MOVE = 1;
App.PHASE_UP = 2;

/* ================================================================== *
 * 6. nova.kernel.Window —— 窗口
 * ================================================================== */

/**
 * 窗口 —— NovaDesk 的核心抽象。
 *
 * 注意：窗口不是安卓的 View，不带任何系统控件。
 * 它只是一块内存画布 + 一组几何信息，由内核自己合成到显存。
 */
export class Window {
  constructor(title, x, y, w, h, app) {
    this.title = title;
    this.x = x;
    this.y = y;
    this.w = w;
    this.h = h;
    this.app = app;

    /** 窗口内容画布。app 往这里画。 */
    this.surface = new Canvas(
      Math.max(1, w - 2 * Theme.BORDER),
      Math.max(1, h - Theme.TITLE_H - Theme.BORDER)
    );

    this.visible = true;
    this.maximized = false;
    /** 最大化之前的位置，用于还原 */
    this.restoreX = x;
    this.restoreY = y;
    this.restoreW = w;
    this.restoreH = h;
  }

  resizeSurface() {
    const nw = Math.max(1, this.w - 2 * Theme.BORDER);
    const nh = Math.max(1, this.h - Theme.TITLE_H - Theme.BORDER);
    if (this.surface.w !== nw || this.surface.h !== nh) {
      this.surface = new Canvas(nw, nh);
      this.app.onResize(nw, nh);
    }
  }

  /** 标题栏矩形。Kotlin: intArrayOf(x, y, w, Theme.TITLE_H) */
  titleRect() { return [this.x, this.y, this.w, Theme.TITLE_H]; }

  /** 关闭按钮中心相对窗口的位置 */
  closeBtnCx() { return this.x + this.w - 14; }
  /** Kotlin: y + Theme.TITLE_H / 2（整数除法） */
  closeBtnCy() { return this.y + (Theme.TITLE_H / 2 | 0); }
  minBtnCx() { return this.x + this.w - 34; }
  minBtnCy() { return this.y + (Theme.TITLE_H / 2 | 0); }
  maxBtnCx() { return this.x + this.w - 54; }
  maxBtnCy() { return this.y + (Theme.TITLE_H / 2 | 0); }

  hitTitle(px, py) {
    return px >= this.x && px < this.x + this.w && py >= this.y && py < this.y + Theme.TITLE_H;
  }

  /** 命中半径平方 49（= 7^2） */
  hitClose(px, py) {
    const dx = px - this.closeBtnCx();
    const dy = py - this.closeBtnCy();
    return dx * dx + dy * dy <= 49;
  }

  hitMin(px, py) {
    const dx = px - this.minBtnCx();
    const dy = py - this.closeBtnCy();
    return dx * dx + dy * dy <= 49;
  }

  hitMax(px, py) {
    const dx = px - this.maxBtnCx();
    const dy = py - this.closeBtnCy();
    return dx * dx + dy * dy <= 49;
  }

  /** 内容区原点（屏幕坐标） */
  contentX() { return this.x + Theme.BORDER; }
  contentY() { return this.y + Theme.TITLE_H; }

  /**
   * 把屏幕坐标转成内容区的本地坐标；不在内容区则返回 null。
   * Kotlin 返回 intArrayOf(lx, ly)，未命中返回 null —— JS 用长度 2 的数组/null 对应。
   */
  toLocal(px, py) {
    const lx = px - this.contentX();
    const ly = py - this.contentY();
    if (lx < 0 || ly < 0 || lx >= this.surface.w || ly >= this.surface.h) return null;
    return [lx, ly];
  }

  contains(px, py) {
    return px >= this.x && px < this.x + this.w && py >= this.y && py < this.y + this.h;
  }
}

/* ================================================================== *
 * 7. nova.kernel.Vfs —— 虚拟文件系统
 * ================================================================== */

/**
 * NovaDesk 虚拟文件系统 —— 内存实现。
 *
 * 所有路径都是 NovaDesk 内部路径（如 /home/note.txt），
 * 通过 ABI 的 open/read/write 与宿主沙箱目录交换数据。
 * 重启后内存部分丢失，但标记为 persistent 的文件会写回宿主。
 *
 * Kotlin 用 HashMap<String, StringBuilder>；StringBuilder 在这里就是普通 string
 * （append -> 拼接）。
 */
export class Vfs {
  constructor(host) {
    this.host = host;
    /** Kotlin: private val files = HashMap<String, StringBuilder>() */
    this.files = new Map();
    /** Kotlin: private val dirs = HashSet<String>() */
    this.dirs = new Set();

    // ---- init { ... } ----
    this.dirs.add('/');
    this.dirs.add('/home');
    this.dirs.add('/apps');
    this.dirs.add('/tmp');

    this.files.set('/readme.txt',
      'Welcome to NovaDesk.\n' +
      'This is not an Android app UI.\n' +
      'Every pixel here is drawn by our own kernel.\n' +
      '\n' +
      'Try the Terminal and type: help\n');

    this.files.set('/home/welcome.txt',
      'NovaDesk is a small operating system\n' +
      'running inside an Android sandbox.\n' +
      'Android only provides: a framebuffer,\n' +
      'touch events, and a clock.\n' +
      'Everything else you see is ours.\n');
  }

  /** Kotlin: files.containsKey(norm(path)) || dirs.contains(norm(path)) */
  exists(path) {
    const p = this.norm(path);
    return this.files.has(p) || this.dirs.has(p);
  }

  isDir(path) { return this.dirs.has(this.norm(path)); }

  /** Kotlin: files[norm(path)]?.toString() */
  read(path) {
    const v = this.files.get(this.norm(path));
    return v === undefined ? null : v;
  }

  write(path, content) {
    const p = this.norm(path);
    this.files.set(p, content);
    // 确保父目录存在
    // Kotlin: p.substringBeforeLast('/', "/")
    const idx = p.lastIndexOf('/');
    const parent = idx < 0 ? '/' : p.substring(0, idx);
    if (parent.length > 0) this.dirs.add(parent);
  }

  /**
   * Kotlin: files.getOrPut(p) { StringBuilder() }.append(content)
   * 注意 getOrPut 用**原始键** p（已经 norm 过），键存在时取已有值追加。
   */
  append(path, content) {
    const p = this.norm(path);
    const cur = this.files.get(p);
    this.files.set(p, (cur === undefined ? '' : cur) + content);
  }

  /** Kotlin: files.remove(norm(path)) != null */
  delete(path) { return this.files.delete(this.norm(path)); }

  mkdir(path) { this.dirs.add(this.norm(path)); }

  /** 列出目录下的条目名（不含路径） */
  list(dir) {
    const d = this.norm(dir);
    const prefix = d === '/' ? '/' : d + '/';
    const out = [];
    // Kotlin 迭代 HashMap/HashSet；迭代顺序属实现细节，
    // 本函数末尾会 sorted()，因此顺序不影响结果。
    for (const f of this.files.keys()) {
      if (f.startsWith(prefix) && f !== d) {
        const rest = f.substring(prefix.length);
        if (rest.length > 0 && !rest.includes('/')) out.push(rest);
      }
    }
    for (const sub of this.dirs) {
      if (sub.startsWith(prefix) && sub !== d) {
        const rest = sub.substring(prefix.length);
        if (rest.length > 0 && !rest.includes('/')) out.push(rest + '/');
      }
    }
    // Kotlin: out.sorted() —— 按 UTF-16 码元序，与 JS 默认 sort 一致
    return out.sort();
  }

  /** 把内存文件持久化到宿主沙箱 */
  persist(path) {
    // Kotlin: val content = read(path) ?: return false
    const content = this.read(path);
    if (content === null) return false;
    // Kotlin: host.open("/novadesk/" + norm(path).trimStart('/'), true)
    const fd = this.host.open('/novadesk/' + this.norm(path).replace(/^\/+/, ''), true);
    if (fd < 0) return false;
    // Kotlin: content.toByteArray(Charsets.UTF_8)
    this.host.write(fd, utf8Encode(content));
    this.host.close(fd);
    return true;
  }

  /** Kotlin: 归一化路径 —— 补前导斜杠，去掉尾部斜杠 */
  norm(p) {
    let s = String(p).trim();
    if (!s.startsWith('/')) s = '/' + s;
    // Kotlin: while (s.length > 1 && s.endsWith("/")) s = s.dropLast(1)
    while (s.length > 1 && s.endsWith('/')) s = s.slice(0, -1);
    return s;
  }
}

/**
 * Kotlin: String.toByteArray(Charsets.UTF_8)
 * 优先用浏览器/Node 自带的 TextEncoder，缺失时退回手写编码。
 */
function utf8Encode(s) {
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(s);
  const bytes = [];
  for (let i = 0; i < s.length; i++) {
    let cp = s.charCodeAt(i);
    if (cp >= 0xD800 && cp <= 0xDBFF && i + 1 < s.length) {
      const lo = s.charCodeAt(i + 1);
      if (lo >= 0xDC00 && lo <= 0xDFFF) {
        cp = ((cp - 0xD800) << 10) + (lo - 0xDC00) + 0x10000;
        i++;
      }
    }
    if (cp < 0x80) {
      bytes.push(cp);
    } else if (cp < 0x800) {
      bytes.push(0xC0 | (cp >> 6), 0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
      bytes.push(0xE0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3F), 0x80 | (cp & 0x3F));
    } else {
      bytes.push(
        0xF0 | (cp >> 18),
        0x80 | ((cp >> 12) & 0x3F),
        0x80 | ((cp >> 6) & 0x3F),
        0x80 | (cp & 0x3F)
      );
    }
  }
  return Uint8Array.from(bytes);
}

// kernel-apps.js —— NovaDesk 内置应用（Kotlin → JavaScript 机械翻译）
//
// 本文件是 nova/apps 下六个内置应用的逐行忠实移植：
//   TerminalApp.kt / FilesApp.kt / AboutApp.kt / ClockApp.kt / NotesApp.kt / PaintApp.kt
//
// 翻译约定：
//   * 行为、布局算式、命令集、字符串一律与 Kotlin 原版保持一致，不做任何"改良"。
//   * IntArray(n)            -> new Int32Array(n)
//   * intArrayOf(a, b)       -> [a, b]
//   * StringBuilder          -> 普通字符串（+= / slice）
//   * ArrayList<T>()         -> []
//   * 0xFFxxxxxx.toInt()     -> 0xFFxxxxxx（JS 中即为正整数）
//   * String.split(Regex("\\s+")) -> String.split(/\s+/)
//   * coerceAtMost/Least/In  -> Math.min/Math.max/手写 clamp
//   * in a until b           -> for (let i = a; i < b; i++)
//   * in a..b（闭区间）       -> for (let i = a; i <= b; i++)，倒序区间亦然
//   * 整数除法 i / j          -> Math.trunc(i / j)（Kotlin 的 Int/Int 是截断除法）

import { App, Canvas, Font, Theme } from './kernel-core.js';

/* ------------------------------------------------------------------ *
 * 小工具：模拟 Kotlin 的整数运算语义
 * ------------------------------------------------------------------ */

/** Kotlin 的 Int / Int —— 向零截断的整数除法 */
function idiv(a, b) {
    return Math.trunc(a / b);
}

/** Kotlin 的区间迭代辅助：in a..b（含两端，b < a 时为空） */
function rangeStep(a, b) {
    return a <= b ? 1 : -1;
}

/** 由 (起始, 结束) 生成闭区间数组，供 for...of 使用 */
function range(a, b) {
    const out = [];
    const st = rangeStep(a, b);
    for (let i = a; st > 0 ? i <= b : i >= b; i += st) out.push(i);
    return out;
}

/** Kotlin 的 String.padEnd(width) —— 用空格补齐到 width（超出则原样返回） */
function padEnd(s, width) {
    return s.length >= width ? s : s + ' '.repeat(width - s.length);
}

/** Kotlin 的 String.substringBeforeLast(delimiter, missingDelimiterValue) */
function substringBeforeLast(s, delim, missing) {
    const i = s.lastIndexOf(delim);
    return i < 0 ? missing : s.substring(0, i);
}

/** Kotlin 的 String.trimEnd(ch) —— 去掉结尾连续的该字符 */
function trimEndChar(s, ch) {
    let e = s.length;
    while (e > 0 && s[e - 1] === ch) e--;
    return s.substring(0, e);
}

/**
 * Kotlin 的 trim() —— 去掉首尾的空白字符。
 * 这里刻意与 Kotlin 保持一致：不裁剪 \u00A0 等 Unicode 空白（JS 的 String.trim 会）。
 */
function ktTrim(s) {
    const isWs = (c) => c === ' ' || c === '\t' || c === '\n' || c === '\r' || c === '\f' || c === '\u000B';
    let a = 0;
    let b = s.length;
    while (a < b && isWs(s[a])) a++;
    while (b > a && isWs(s[b - 1])) b--;
    return s.substring(a, b);
}

/**
 * Kotlin 的 split("\n") —— 纯字面量分割，保留尾部的空串。
 * JS 的 String.prototype.split('\n') 语义与之一致，单独包一层是为了标明意图。
 */
function splitLines(s) {
    return s.split('\n');
}

/** Kotlin 的 coerceIn(min, max)：先把 min/max 排序，再夹取 */
function coerceIn(v, lo, hi) {
    const a = Math.min(lo, hi);
    const b = Math.max(lo, hi);
    return v < a ? a : (v > b ? b : v);
}

/** 角度制转弧度（Kotlin Math.toRadians） */
function toRadians(deg) {
    return deg * Math.PI / 180;
}

/**
 * Kotlin 的 Float 语义：单精度。JS 的 Math.fround 与 Float.floatToIntBits 一致，
 * 用来复现 Kotlin 里 Float 与 Int 之间的截断/取整差异。
 */
function f32(v) {
    return Math.fround(v);
}

/**
 * 字符转字符串。
 * onChar / onKey 可能收到数字（码位）或字符串，这里统一成"一个字符的字符串"。
 */
function chStr(ch) {
    if (typeof ch === 'number') return String.fromCharCode(ch);
    return ch == null ? '' : String(ch);
}

/**
 * Kotlin 的 Char.code —— JS 里拿单个"字符"的码位。
 * 注意不能用 codePointAt：它会把代理对当成一个码位，与 Kotlin 的 UTF-16 Char 语义不同。
 */
function chCode(s) {
    return s.length > 0 ? s.charCodeAt(0) : 0;
}

/**
 * NovaKey 常量（Kotlin: nova.abi.NovaKey）。
 * 内核若从 './kernel-core.js' 导出了 NovaKey 就优先使用它，否则退回本地定义，
 * 两边数值完全一致。
 * 之所以做成"延迟解析"而不是顶层 import，是为了在本文件与 kernel-core.js
 * 并行开发期间也能独立通过语法检查。
 */
let _novaKeyCache = null;
function novaKeyTable() {
    if (_novaKeyCache) return _novaKeyCache;
    _novaKeyCache = {
        BACK: 1,
        ENTER: 2,
        ESC: 3,
        UP: 4,
        DOWN: 5,
        LEFT: 6,
        RIGHT: 7,
        TAB: 8
    };
    return _novaKeyCache;
}

/** 允许内核注入它自己的 NovaKey 表（可选） */
export function setNovaKey(table) {
    if (table && typeof table === 'object') _novaKeyCache = table;
}

export const NovaKey = novaKeyTable();

/**
 * 从内核注入的 vfs 里取（可能存在的）append 方法。
 * 注意 Kotlin 里 append(file, text) / append(c) 是完全不同的两个方法，
 * 这里始终按"文件名 + 文本"两参数语义处理。
 */
function vfsAppend(vfs, path, text) {
    if (vfs && typeof vfs.append === 'function') vfs.append(path, text);
}

/** 读取可选的 Theme 成员，缺失时回退到 Kotlin Theme 里的取值（对外导出，便于 host 复用） */
export function themeColor(name, fallback) {
    const v = Theme ? Theme[name] : undefined;
    return typeof v === 'number' ? v : fallback;
}

/** 取窗口 surface 的宽度。Kotlin 直接读 win.surface.w；这里容忍 win 尚未注入。 */
function surfaceW(app) {
    return (app.win && app.win.surface) ? app.win.surface.w : 0;
}

/** 取窗口 surface 的高度 */
function surfaceH(app) {
    return (app.win && app.win.surface) ? app.win.surface.h : 0;
}

/* ------------------------------------------------------------------ *
 * 辅助函数：导出给 host / 其他模块复用
 * ------------------------------------------------------------------ */

export { idiv, padEnd, substringBeforeLast, trimEndChar, ktTrim, coerceIn, f32 };

/* ================================================================== *
 * TerminalApp —— 终端，NovaDesk 的 shell
 * ================================================================== */

/**
 * 终端 —— NovaDesk 的 shell。
 * 这是证明系统"活着"的应用：能打字、能执行命令、能读写自己的文件系统。
 */
export class TerminalApp extends App {
    constructor() {
        super('terminal');

        /** 滚动缓冲（Kotlin: ArrayList<String> lines） */
        this.lines = [];
        /** 当前输入行（Kotlin: StringBuilder input） */
        this.input = '';
        this.scroll = 0;
        this.cwd = '/home';
        this.cursorBlink = 0;
        this.showCursor = true;

        /** 历史记录 */
        this.history = [];
        this.histIndex = -1;

        // Kotlin 的 init 块
        this.lines.push('NovaDesk 终端 v1.0');
        this.lines.push('输入 help 查看命令列表');
        this.lines.push('');
    }

    icon() {
        return '>_';
    }

    defaultSize() {
        return [520, 320];
    }

    draw(c) {
        c.clear(0xFF0C1016);

        const lineH = Font.CELL_H + 3;
        const maxLines = idiv(c.h - 8, lineH);
        const start = Math.max(0, this.lines.length - maxLines + 1 + this.scroll);

        let y = 4;
        let i = start;
        while (i < this.lines.length && y < c.h - lineH) {
            const ln = this.lines[i];
            // Kotlin 的 when { } 无主语分支 —— 顺序判断，先命中先返回
            let color;
            if (ln.startsWith('$ ')) color = 0xFF7EE787;
            else if (ln.startsWith('!')) color = 0xFFFF7B72;
            else if (ln.startsWith('#')) color = Theme.TEXT_DIM;
            else color = Theme.TEXT;
            c.text(ln, 5, y, color, 1);
            y += lineH;
            i++;
        }

        // 当前输入行
        const prompt = '$' + this.input;
        c.text(prompt, 5, y, Theme.TEXT, 1);
        if (this.showCursor) {
            const cx = 5 + c.textWidth(prompt, 1);
            c.fillRect(cx, y, 5, Font.CELL_H, 0xFF7EE787);
        }
    }

    update(dtMs) {
        this.cursorBlink += dtMs;
        if (this.cursorBlink > 500) {
            this.cursorBlink = 0;
            this.showCursor = !this.showCursor;
            return true;
        }
        return false;
    }

    onChar(ch) {
        const s = chStr(ch);
        const code = chCode(s);
        if (s === '\n' || s === '\r') {
            this.execute();
        } else if (s === '\b') {
            if (this.input.length > 0) this.input = this.input.slice(0, -1);
        } else {
            if (code >= 32 && code < 127) this.input += s;
        }
        this.scroll = 0;
        return true;
    }

    onKey(code) {
        const K = novaKeyTable();
        if (code === K.UP) { this.recallHistory(-1); return true; }
        if (code === K.DOWN) { this.recallHistory(1); return true; }
        return false;
    }

    recallHistory(dir) {
        if (this.history.length === 0) return;
        this.histIndex = coerceIn(this.histIndex + dir, -1, this.history.length - 1);
        this.input = '';
        if (this.histIndex >= 0 && this.histIndex < this.history.length) {
            this.input += this.history[this.histIndex];
        }
    }

    execute() {
        const cmd = ktTrim(this.input);
        this.input = '';
        this.lines.push('$ ' + cmd);
        if (cmd.length > 0) {
            this.history.push(cmd);
            this.histIndex = this.history.length;
        }
        this.runCommand(cmd);
        this.lines.push('');
        // 限制滚动缓冲
        while (this.lines.length > 400) this.lines.shift();
    }

    runCommand(cmd) {
        if (cmd.length === 0) return;
        const parts = cmd.split(/\s+/);
        const op = parts[0].toLowerCase();
        const args = parts.slice(1);

        switch (op) {
            case 'help': {
                this.lines.push('命令：');
                this.lines.push('  help           显示这个帮助');
                this.lines.push('  ls [目录]      列出目录内容');
                this.lines.push('  cat <文件>     显示文件内容');
                this.lines.push('  cd <目录>      切换目录');
                this.lines.push('  pwd            显示当前目录');
                this.lines.push('  echo <文字>    输出文字');
                this.lines.push('  write <文件> <文字>   写入文件');
                this.lines.push('  append <文件> <文字>  追加到文件');
                this.lines.push('  rm <文件>      删除文件');
                this.lines.push('  mkdir <目录>   新建目录');
                this.lines.push('  save <文件>    保存到沙箱');
                this.lines.push('  date           系统运行时间');
                this.lines.push('  mem            显存信息');
                this.lines.push('  clear          清屏');
                this.lines.push('  apps           列出已装应用');
                break;
            }
            case 'ls': {
                const d = args.length === 0 ? this.cwd : this.resolve(args[0]);
                if (!this.sys.vfs.isDir(d) && !this.sys.vfs.exists(d)) {
                    this.lines.push('!找不到目录：' + d);
                    return;
                }
                const items = this.sys.vfs.list(d);
                if (items.length === 0) {
                    this.lines.push('(空目录)');
                } else {
                    // 三列排布
                    let row = '';
                    items.forEach((n, i) => {
                        row += padEnd(n, 16);
                        if ((i + 1) % 3 === 0) {
                            this.lines.push(row);
                            row = '';
                        }
                    });
                    if (row.length > 0) this.lines.push(row);
                }
                break;
            }
            case 'cat': {
                if (args.length === 0) { this.lines.push('!用法：cat <文件>'); return; }
                const f = this.resolve(args[0]);
                const content = this.sys.vfs.read(f);
                if (content == null) {
                    this.lines.push('!找不到文件：' + f);
                } else {
                    for (const l of splitLines(content)) this.lines.push(l);
                }
                break;
            }
            case 'cd': {
                const d = args.length === 0 ? '/home' : this.resolve(args[0]);
                if (this.sys.vfs.isDir(d)) this.cwd = d;
                else this.lines.push('!找不到目录：' + d);
                break;
            }
            case 'pwd':
                this.lines.push(this.cwd);
                break;
            case 'echo':
                this.lines.push(args.join(' '));
                break;
            case 'write': {
                if (args.length < 2) { this.lines.push('!用法：write <文件> <文字>'); return; }
                const f = this.resolve(args[0]);
                const text = args.slice(1).join(' ');
                this.sys.vfs.write(f, text);
                this.lines.push('#已写入 ' + f + '，共 ' + text.length + ' 字节');
                break;
            }
            case 'append': {
                if (args.length < 2) { this.lines.push('!用法：append <文件> <文字>'); return; }
                const f = this.resolve(args[0]);
                vfsAppend(this.sys.vfs, f, args.slice(1).join(' ') + '\n');
                this.lines.push('#已追加到 ' + f);
                break;
            }
            case 'rm': {
                if (args.length === 0) { this.lines.push('!用法：rm <文件>'); return; }
                const f = this.resolve(args[0]);
                if (this.sys.vfs.delete(f)) this.lines.push('#已删除 ' + f);
                else this.lines.push('!找不到文件：' + f);
                break;
            }
            case 'mkdir': {
                if (args.length === 0) { this.lines.push('!用法：mkdir <目录>'); return; }
                this.sys.vfs.mkdir(this.resolve(args[0]));
                this.lines.push('#已创建 ' + this.resolve(args[0]));
                break;
            }
            case 'save': {
                if (args.length === 0) { this.lines.push('!用法：save <文件>'); return; }
                const f = this.resolve(args[0]);
                if (this.sys.vfs.persist(f)) this.lines.push('#已保存 ' + f + ' 到沙箱');
                else this.lines.push('!保存失败：' + f);
                break;
            }
            case 'date': {
                const s = this.sys.uptimeSeconds();
                this.lines.push('运行时间：' + idiv(s, 60) + '分 ' + (s % 60) + '秒  （基于 sys_clock_ms 的单调时钟）');
                break;
            }
            case 'mem': {
                this.lines.push('帧缓冲：' + this.sys.screenW() + 'x' + this.sys.screenH() + ' ARGB_8888');
                this.lines.push('字节数：' + (this.sys.screenW() * this.sys.screenH() * 4));
                this.lines.push('渲染器：自研软件光栅化');
                break;
            }
            case 'apps':
                for (const n of this.sys.appNames()) this.lines.push('  ' + n);
                break;
            case 'clear':
                this.lines.length = 0; // Kotlin: lines.clear()
                return;
            case 'uname': {
                this.lines.push('NovaDesk 1.0（沙箱运行）');
                this.lines.push('宿主：浏览器 Canvas');
                this.lines.push('架构：JavaScript');
                break;
            }
            default:
                this.lines.push('!未知命令：' + op + '（输入 help 查看帮助）');
                break;
        }
    }

    /** 相对路径 → 绝对路径 */
    resolve(p) {
        if (p.startsWith('/')) return p;
        if (p === '..') {
            const parent = substringBeforeLast(this.cwd, '/', '');
            return parent.length === 0 ? '/' : parent;
        }
        return this.cwd === '/' ? '/' + p : this.cwd + '/' + p;
    }
}

/* ================================================================== *
 * FilesApp —— 文件管理器
 * ================================================================== */

/** 文件管理器 —— 浏览 NovaDesk 自己的 VFS */
export class FilesApp extends App {
    constructor() {
        super('files');

        this.dir = '/';
        this.items = [];             // Kotlin: List<String> items = emptyList()
        this.selected = 0;
        this.showContent = null;     // Kotlin: String? showContent = null
        this.contentScroll = 0;
        this.lastW = 0;
    }

    icon() {
        return '[]';
    }

    defaultSize() {
        return [440, 300];
    }

    onResize(w, h) {
        this.lastW = w;
    }

    onClose() {
    }

    refresh() {
        this.items = this.sys.vfs.list(this.dir);
        this.selected = coerceIn(this.selected, 0, Math.max(0, this.items.length - 1));
    }

    draw(c) {
        c.clear(0xFF14181F);
        // Kotlin 的懒加载刷新：首帧 items 为空且还没量过宽度时拉一次列表
        if (this.items.length === 0 && this.lastW === 0) {
            this.refresh();
            this.lastW = c.w;
        }

        // 路径栏
        c.fillRect(0, 0, c.w, 22, 0xFF1E242C);
        c.text(this.dir, 8, 8, Theme.ACCENT, 1);
        c.fillRect(0, 22, c.w, 1, 0x30FFFFFF);

        if (this.showContent != null) {
            this.drawContent(c);
            return;
        }

        const rowH = 20;
        const listW = idiv(c.w, 2);
        let y = 28;
        this.items.forEach((name, i) => {
            if (y > c.h - rowH) return; // Kotlin 的 break：后续迭代形同空转
            const isDir = name.endsWith('/');
            const sel = i === this.selected;
            if (sel) c.fillRect(2, y - 2, listW - 4, rowH, 0x304A90D9);
            const label = isDir ? name.slice(0, -1) : name; // Kotlin dropLast(1)
            const color = isDir ? 0xFF7EA6E0 : Theme.TEXT;
            c.text((isDir ? '[+] ' : '    ') + label, 8, y + 3, color, 1);
            y += rowH;
        });
        if (this.items.length === 0) c.text('(空目录)', 10, 30, Theme.TEXT_DIM, 1);

        // 右侧预览
        c.fillRect(listW, 23, 1, c.h - 23, 0x30FFFFFF);
        const selName = this.selected >= 0 && this.selected < this.items.length
            ? this.items[this.selected] : undefined; // Kotlin: items.getOrNull(selected)
        if (selName != null && !selName.endsWith('/')) {
            const path = this.dir === '/' ? '/' + selName : this.dir + '/' + selName;
            const txt = this.sys.vfs.read(path) || '';
            let py = 28;
            const maxLines = idiv(c.h - 34, Font.CELL_H + 3);
            for (const l of splitLines(txt).slice(0, maxLines)) { // Kotlin: .take(maxLines)
                c.text(l, listW + 8, py, Theme.TEXT_DIM, 1);
                py += Font.CELL_H + 3;
            }
        } else if (selName != null) {
            c.text('目录', listW + 8, 28, Theme.TEXT_DIM, 1);
        }

        // 底部提示
        c.fillRect(0, c.h - 18, c.w, 18, 0xFF1E242C);
        c.text('点击打开  |  点顶部返回上级', 8, c.h - 13, Theme.TEXT_DIM, 1);
    }

    drawContent(c) {
        const txt = this.showContent;
        if (txt == null) return;
        // Kotlin 源码里的 `.let { }` 是空操作，这里保持一致：只画标题，不消费返回值
        c.text('查看：' + txt, 8, 4, 0xFF7EE787, 1);
        const body = this.sys.vfs.read(txt); // Kotlin: ?: "(cannot read"
        const text = body == null ? '(无法读取)' : body;
        const lines = splitLines(text);
        const lineH = Font.CELL_H + 3;
        const maxLines = idiv(c.h - 30, lineH);
        const start = coerceIn(this.contentScroll, 0, Math.max(0, lines.length - maxLines));
        let y = 26;
        for (let i = start; i < Math.min(lines.length, start + maxLines); i++) {
            c.text(lines[i], 8, y, Theme.TEXT, 1);
            y += lineH;
        }
        c.fillRect(0, c.h - 18, c.w, 18, 0xFF1E242C);
        c.text('点击任意处返回', 8, c.h - 13, Theme.TEXT_DIM, 1);
    }

    onTouch(x, y, phase) {
        if (phase !== App.PHASE_DOWN) return false;
        if (this.showContent != null) {
            this.showContent = null;
            this.contentScroll = 0;
            return true;
        }
        if (y < 24) return false;

        // 注意：Kotlin 原版这里是 win.surface.w，不是每帧缓存的 c.w
        if (x < idiv(surfaceW(this), 2)) {
            const idx = idiv(y - 28, 20);
            if (idx >= 0 && idx < this.items.length) {
                const name = this.items[idx];
                this.selected = idx;
                const path = this.dir === '/' ? '/' + name : this.dir + '/' + name;
                if (name.endsWith('/')) {
                    if (name === '../') {
                        const parent = substringBeforeLast(this.dir, '/', '');
                        this.dir = parent.length === 0 ? '/' : parent;
                    } else {
                        this.dir = trimEndChar(path, '/'); // Kotlin: path.trimEnd('/')
                    }
                    this.selected = 0;
                    this.refresh();
                } else {
                    this.showContent = path;
                    this.contentScroll = 0;
                }
            }
        } else {
            this.showContent = null;
        }
        return true;
    }
}

/* ================================================================== *
 * AboutApp —— 关于本机
 * ================================================================== */

/** 关于本机 —— 说明 NovaDesk 是什么 */
export class AboutApp extends App {
    constructor() {
        super('about');
        this.scroll = 0;
    }

    icon() {
        return '(i)';
    }

    defaultSize() {
        return [430, 250];
    }

    draw(c) {
        c.clear(0xFF14181F);
        c.fillRect(0, 0, c.w, 34, 0xFF1B2A3A);
        c.text('NovaDesk', 12, 11, 0xFF7EA6E0, 2);

        // Kotlin 的 trimIndent()：这里已手工去缩进、去首尾空行，内容逐字节保留
        const body = [
            '一个小型操作系统，',
            '运行在浏览器沙箱里。',
            '',
            '浏览器只提供：',
            '  - 一块画布（像素）',
            '  - 输入事件',
            '  - 一个时钟',
            '  - 一个私有目录',
            '',
            '其余部分都是我们自己的：',
            '  - 合成器与窗口管理器',
            '  - 字体与光栅化器',
            '  - 文件系统',
            '  - 全部应用程序',
            '  - 输入分发',
            '',
            '没有使用任何 HTML 控件',
            '来绘制这些内容。浏览器',
            '并不知道窗口是什么。',
            '',
            '试试：拖动标题栏，',
            '拖住右下角改变大小，',
            '还能打开开始菜单。'
        ].join('\n');

        const lines = splitLines(body);
        const lineH = Font.CELL_H + 4;
        const maxLines = idiv(c.h - 44, lineH);
        const start = coerceIn(this.scroll, 0, Math.max(0, lines.length - maxLines));
        let y = 44;
        for (let i = start; i < Math.min(lines.length, start + maxLines); i++) {
            const color = lines[i].startsWith('  -') ? Theme.ACCENT : Theme.TEXT;
            c.text(lines[i], 12, y, color, 1);
            y += lineH;
        }
        c.fillRect(0, c.h - 16, c.w, 16, 0xFF1B2A3A);
        c.text('在窗口内上下滑动可滚动', 8, c.h - 12, Theme.TEXT_DIM, 1);
    }

    onTouch(x, y, phase) {
        if (phase === App.PHASE_MOVE) {
            // Kotlin 原版就是每次 move 固定 -1（小步长滚动），照搬
            this.scroll = Math.max(0, this.scroll - 1);
            return true;
        }
        return false;
    }
}

/* ================================================================== *
 * ClockApp —— 时钟
 * ================================================================== */

/** 时钟 —— 用系统单调时钟驱动的模拟表盘 */
export class ClockApp extends App {
    constructor() {
        super('clock');

        this.secAngle = 0;    // Kotlin Float
        this.minAngle = 0;    // Kotlin Float
        this.hourAngle = 0;   // Kotlin Float
        this.lastSec = -1;
    }

    icon() {
        return '(o)';
    }

    defaultSize() {
        return [300, 300];
    }

    draw(c) {
        c.clear(0xFF0C1016);
        const cx = idiv(c.w, 2);
        const cy = idiv(c.h, 2);
        const r = Math.min(cx, cy) - 16;
        if (r < 20) return;

        // 表盘
        for (const dy of range(-r, r)) {
            for (const dx of range(-r, r)) {
                const d2 = dx * dx + dy * dy;
                if (d2 <= r * r) c.blend(cx + dx, cy + dy, 0xFF161C24);
                else if (d2 <= (r + 2) * (r + 2)) c.blend(cx + dx, cy + dy, 0xFF3A4756);
            }
        }

        // 刻度
        for (let i = 0; i < 12; i++) {
            const a = toRadians(i * 30 - 90);
            const x0 = cx + Math.trunc(Math.cos(a) * (r - 12));
            const y0 = cy + Math.trunc(Math.sin(a) * (r - 12));
            const x1 = cx + Math.trunc(Math.cos(a) * (r - 4));
            const y1 = cy + Math.trunc(Math.sin(a) * (r - 4));
            c.line(x0, y0, x1, y1, (i % 3 === 0) ? Theme.ACCENT : 0xFF556070);
        }

        // 指针
        // 长度在 Kotlin 里是 (r * 0.5f).toInt() 这类表达式：先按 Float 单精度算，再截断成 Int。
        // 这里用 f32 精确复现（Float 的截断结果与 double 可能差 1px）。
        this.hand(c, cx, cy, this.hourAngle, Math.trunc(f32(f32(r) * 0.5)), 4, 0xFFE6EDF3);
        this.hand(c, cx, cy, this.minAngle, Math.trunc(f32(f32(r) * 0.72)), 3, 0xFF9FB4C7);
        this.hand(c, cx, cy, this.secAngle, Math.trunc(f32(f32(r) * 0.85)), 1, 0xFFE05252);

        // 中心轴
        for (const dy of range(-3, 3)) {
            for (const dx of range(-3, 3)) {
                if (dx * dx + dy * dy <= 9) c.blend(cx + dx, cy + dy, Theme.ACCENT);
            }
        }

        const s = this.sys.uptimeSeconds();
        const label = this.pad(idiv(s, 3600)) + ':' + this.pad(idiv(s, 60) % 60) + ':' + this.pad(s % 60);
        c.textCentered(label, cx, c.h - 22, Theme.TEXT, 1);
        c.textCentered('系统运行时间', cx, c.h - 12, Theme.TEXT_DIM, 1);
    }

    hand(c, cx, cy, angle, len, thick, color) {
        const a = toRadians(f32(angle) - 90); // (angle.toDouble() - 90.0) 再转弧度
        const ex = cx + Math.trunc(Math.cos(a) * len);
        const ey = cy + Math.trunc(Math.sin(a) * len);
        const half = idiv(thick, 2); // Kotlin 的 (thick / 2) 是整数除法
        for (let o = -half; o <= half; o++) { // Kotlin: -half..half 闭区间
            c.line(cx + o, cy, ex + o, ey, color);
            c.line(cx, cy + o, ex, ey + o, color);
        }
    }

    update(dtMs) {
        // 从系统时钟推导角度，而不是累加
        const total = this.sys.uptimeSeconds();
        const s = total % 60;
        this.secAngle = f32(f32(s * 6));
        this.minAngle = f32(f32(f32(idiv(total, 60) % 60) * 6) + f32(s * 0.1));
        this.hourAngle = f32(f32(f32(idiv(total, 3600) % 12) * 30) + f32(f32(idiv(total, 60) % 60) * 0.5));
        return true;
    }

    pad(v) {
        return v < 10 ? '0' + v : '' + v;
    }
}

/* ================================================================== *
 * NotesApp —— 记事本
 * ================================================================== */

/** 记事本 —— 演示应用读写 VFS，并可通过 save 持久化到安卓沙箱 */
export class NotesApp extends App {
    constructor() {
        super('notes');

        this.buf = '';                    // Kotlin: StringBuilder buf
        this.path = '/home/note.txt';
        this.saved = true;
        this.status = '';
        this.statusUntil = 0;
        this.blink = 0;
        this.showCursor = true;
        this.scrollLine = 0;

        // Kotlin 的 init 块：载入已有内容（如果有）
        // sys 还未注入，延迟到首次 draw
    }

    icon() {
        return '~~';
    }

    defaultSize() {
        return [430, 290];
    }

    draw(c) {
        if (this.sys.vfs.exists(this.path) && this.buf.length === 0 && this.saved) {
            const t = this.sys.vfs.read(this.path);
            this.buf += (t == null ? '' : t);
        }

        c.clear(0xFF14181F);

        // 工具栏
        c.fillRect(0, 0, c.w, 22, 0xFF1E242C);
        c.text(this.saved ? '已保存' : '未保存', 8, 8,
            this.saved ? Theme.TEXT_DIM : 0xFFFFA657, 1);
        c.text('| ' + this.path, 60, 8, Theme.TEXT_DIM, 1);
        c.text('保存', c.w - 52, 8, Theme.ACCENT, 1);
        c.fillRect(0, 22, c.w, 1, 0x30FFFFFF);

        // 正文
        const lineH = Font.CELL_H + 3;
        const lines = splitLines(this.buf);
        const maxLines = idiv(c.h - 30, lineH);
        const start = coerceIn(this.scrollLine, 0, Math.max(0, lines.length - maxLines));
        let y = 28;
        for (let i = start; i < Math.min(lines.length, start + maxLines); i++) {
            c.text(lines[i], 8, y, Theme.TEXT, 1);
            y += lineH;
        }

        // 光标画在最后一行末尾
        if (this.showCursor) {
            const lastIdx = lines.length - 1;
            if (lastIdx >= start && lastIdx < start + maxLines) {
                const ly = 28 + (lastIdx - start) * lineH;
                const lx = 8 + c.textWidth(lines[lastIdx], 1);
                c.fillRect(lx + 1, ly, 5, Font.CELL_H, Theme.ACCENT);
            }
        }

        // 状态栏
        if (this.status.length > 0 && this.sys.uptimeSeconds() * 1000 < this.statusUntil) {
            c.fillRoundRect(8, c.h - 26, c.textWidth(this.status, 1) + 16, 18, 4, 0xE02D3748);
            c.text(this.status, 16, c.h - 21, 0xFF7EE787, 1);
        }
    }

    update(dtMs) {
        this.blink += dtMs;
        if (this.blink > 500) {
            this.blink = 0;
            this.showCursor = !this.showCursor;
            return true;
        }
        return false;
    }

    onChar(ch) {
        const s = chStr(ch);
        const code = chCode(s);
        if (s === '\n' || s === '\r') {
            this.buf += '\n';
        } else if (s === '\b') {
            if (this.buf.length > 0) this.buf = this.buf.slice(0, -1);
        } else {
            if (code >= 32) this.buf += s;
        }
        this.saved = false;
        return true;
    }

    onTouch(x, y, phase) {
        if (phase !== App.PHASE_DOWN) return false;
        if (y < 22 && x > surfaceW(this) - 66) {
            this.sys.vfs.write(this.path, this.buf);
            this.saved = true;
            const ok = this.sys.vfs.persist(this.path);
            this.status = ok ? '已保存到沙箱' : '仅保存在内存';
            this.statusUntil = this.sys.uptimeSeconds() * 1000 + 2000;
            return true;
        }
        return false;
    }

    onKey(code) {
        return false;
    }
}

/* ================================================================== *
 * PaintApp —— 画板
 * ================================================================== */

/** 画板 —— 直接用触摸在 NovaDesk 的画布上画像素 */
export class PaintApp extends App {
    constructor() {
        super('paint');

        this.surface = new Canvas(1, 1);
        this.lastX = -1;
        this.lastY = -1;
        this.colorIdx = 0;
        this.brush = 3;

        this.palette = [
            0xFFE6EDF3, 0xFFE05252, 0xFF52C452,
            0xFF4A90D9, 0xFFE0A852, 0xFFB05FD9
        ];
    }

    icon() {
        return '/\\';
    }

    defaultSize() {
        return [460, 320];
    }

    // JS 没有方法重载，Kotlin 的 override fun onResize(w, h) 直接对应这里
    onResize(w, h) {
        this.surface = new Canvas(Math.max(1, w), Math.max(1, h - 24));
    }

    draw(c) {
        // 尺寸对不上就换一块新画布，并把旧内容贴过去
        if (this.surface.w !== c.w || this.surface.h !== c.h - 24) {
            const ns = new Canvas(Math.max(1, c.w), Math.max(1, c.h - 24));
            ns.clear(0xFF0C1016);
            ns.blit(this.surface, 0, 0, this.surface.w, this.surface.h, 0, 0);
            this.surface = ns;
        }
        c.clear(0xFF0C1016);
        c.blit(this.surface, 0, 0, this.surface.w, this.surface.h, 0, 0);

        // 调色板工具条
        c.fillRect(0, c.h - 24, c.w, 24, 0xFF1E242C);
        let x = 6;
        this.palette.forEach((col, i) => {
            c.fillRoundRect(x, c.h - 20, 16, 16, 3, col);
            if (i === this.colorIdx) c.strokeRect(x - 1, c.h - 21, 18, 18, 0xFFFFFFFF);
            x += 22;
        });
        c.text('清空', c.w - 52, c.h - 17, 0xFFE05252, 1);
    }

    onTouch(x, y, phase) {
        if (y >= this.surface.h) {
            if (phase === App.PHASE_DOWN) {
                if (x > this.surface.w - 66) {
                    this.surface.clear(0xFF0C1016);
                } else {
                    const i = idiv(x - 6, 22);
                    if (i >= 0 && i < this.palette.length) this.colorIdx = i;
                }
            }
            return true;
        }
        switch (phase) {
            case App.PHASE_DOWN:
                this.lastX = x;
                this.lastY = y;
                this.stamp(x, y);
                break;
            case App.PHASE_MOVE:
                if (this.lastX >= 0) {
                    // 在两点之间插值，避免快速滑动断线
                    const steps = Math.max(Math.abs(x - this.lastX), Math.abs(y - this.lastY));
                    const denom = Math.max(1, steps);
                    for (let s = 1; s <= Math.max(1, steps); s++) { // Kotlin: 1..maxOf(1, steps)
                        const t = f32(s / denom); // s.toFloat() / maxOf(1, steps) —— Float 单精度除法
                        this.stamp(
                            Math.trunc(this.lastX + (x - this.lastX) * t),
                            Math.trunc(this.lastY + (y - this.lastY) * t)
                        );
                    }
                }
                this.lastX = x;
                this.lastY = y;
                break;
            case App.PHASE_UP:
                this.lastX = -1;
                this.lastY = -1;
                break;
        }
        return true;
    }

    stamp(cx, cy) {
        const col = this.palette[this.colorIdx];
        for (let dy = -this.brush; dy <= this.brush; dy++) { // Kotlin: -brush..brush
            for (let dx = -this.brush; dx <= this.brush; dx++) {
                if (dx * dx + dy * dy <= this.brush * this.brush) {
                    this.surface.blend(cx + dx, cy + dy, col);
                }
            }
        }
    }
}

/* ================================================================== *
 * 注册表
 * ================================================================== */

/** 名称 -> 工厂函数。内核按名字创建应用实例。 */
export const BUILTIN_APPS = {
    terminal: () => new TerminalApp(),
    files: () => new FilesApp(),
    about: () => new AboutApp(),
    clock: () => new ClockApp(),
    notes: () => new NotesApp(),
    paint: () => new PaintApp()
};

/** 名称 -> 构造器（内核若想直接 new 也用得上） */
export const BUILTIN_APP_CLASSES = {
    terminal: TerminalApp,
    files: FilesApp,
    about: AboutApp,
    clock: ClockApp,
    notes: NotesApp,
    paint: PaintApp
};

/**
 * 把内置应用注册进内核。
 *
 * 内核若提供 registerApp(name, factory) 就逐个注册；否则返回 BUILTIN_APPS，
 * 由调用方自己塞进它的注册表。
 *
 * @param {object} [kernel] 内核实例
 * @returns {object} BUILTIN_APPS
 */
export function registerApps(kernel) {
    if (kernel && typeof kernel.registerApp === 'function') {
        for (const name of Object.keys(BUILTIN_APPS)) {
            kernel.registerApp(name, BUILTIN_APPS[name]);
        }
    }
    return BUILTIN_APPS;
}

/** 创建全部内置应用（name -> 实例）。内核若要一次性拿到所有实例可用它。 */
export function createAllApps() {
    const out = {};
    for (const name of Object.keys(BUILTIN_APPS)) out[name] = BUILTIN_APPS[name]();
    return out;
}

/* ================================================================== *
 * 状态保存 / 恢复
 *
 * 挂在原型上而不是改写六个类的主体 —— 内核只要求应用"可选地"
 * 实现 saveState() / loadState(obj)，实现了就恢复内容，没实现就只恢复窗口。
 * 这样"退出页面再进来"能连终端滚屏、记事本正文、画板涂鸦一起还原。
 * ================================================================== */

/** 终端：滚屏内容、当前输入、当前目录、历史 */
TerminalApp.prototype.saveState = function () {
    return {
        lines: this.lines.slice(-400),   // 和内核里的滚动缓冲上限保持一致
        input: this.input,
        cwd: this.cwd,
        history: this.history.slice(-200),
    };
};
TerminalApp.prototype.loadState = function (o) {
    if (!o) return;
    if (Array.isArray(o.lines)) this.lines = o.lines.slice();
    if (typeof o.input === 'string') this.input = o.input;
    if (typeof o.cwd === 'string') this.cwd = o.cwd;
    if (Array.isArray(o.history)) this.history = o.history.slice();
    this.histIndex = this.history.length;
    this.scroll = 0;
};

/** 文件管理器：当前目录和选中项 */
FilesApp.prototype.saveState = function () {
    return { dir: this.dir, selected: this.selected };
};
FilesApp.prototype.loadState = function (o) {
    if (!o) return;
    if (typeof o.dir === 'string') this.dir = o.dir;
    if (typeof o.selected === 'number') this.selected = o.selected;
    this.showContent = null;
    this.contentScroll = 0;
    this.lastW = 0;          // 逼它下次绘制时重新列目录
    this.items = [];
};

/** 记事本：正文和文件路径 */
NotesApp.prototype.saveState = function () {
    return { buf: this.buf, path: this.path, saved: this.saved };
};
NotesApp.prototype.loadState = function (o) {
    if (!o) return;
    if (typeof o.buf === 'string') this.buf = o.buf;
    if (typeof o.path === 'string') this.path = o.path;
    this.saved = o.saved !== false;
    this.scrollLine = 0;
};

/**
 * 画板：整张位图。
 *
 * 转成 base64 存。像素数组是 w*h*4 字节，一张 300x150 的画布约 180KB，
 * 转成 base64 约 240KB —— localStorage 一般有 5MB，够用。
 * 但为了别把配额撑爆，超过 512KB 就不存了（画面丢一次，总比整个存档写失败好）。
 */
PaintApp.prototype.saveState = function () {
    try {
        const px = this.surface.px;
        const bytes = new Uint8Array(px.buffer, 0, px.length * 4);
        if (bytes.length > 524288) {
            return { tooBig: true, w: this.surface.w, h: this.surface.h };
        }
        let s = '';
        const CH = 0x8000;
        for (let i = 0; i < bytes.length; i += CH) {
            s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
        }
        return {
            w: this.surface.w, h: this.surface.h,
            data: btoa(s),
            colorIdx: this.colorIdx, brush: this.brush,
        };
    } catch (e) {
        return null;
    }
};
PaintApp.prototype.loadState = function (o) {
    if (!o) return;
    if (typeof o.colorIdx === 'number') this.colorIdx = o.colorIdx;
    if (typeof o.brush === 'number') this.brush = o.brush;
    if (!o.data || o.tooBig) return;
    try {
        const bin = atob(o.data);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const px = new Int32Array(bytes.buffer);
        const w = o.w, h = o.h;
        if (px.length !== w * h) return;
        const c = new Canvas(w, h);
        c.px.set(px);
        this.surface = c;
    } catch (e) { /* 存档坏了就当作没存过 */ }
};

export default BUILTIN_APPS;

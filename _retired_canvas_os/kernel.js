/**
 * NovaDesk 内核（JavaScript 版）
 *
 * 与 Kotlin 版 Kernel.kt 逐行对应，行为完全一致。
 * 这个文件不依赖任何浏览器 API —— 宿主通过 host 对象注入那 8 个系统调用。
 *
 * 职责：
 *   1. 合成：把所有窗口画到一块全屏画布上（桌面 → 窗口 → 任务栏 → 光标）
 *   2. 输入分发：坐标 → 命中哪个窗口 → 标题拖动 / 按钮 / 内容
 *   3. 生命周期：创建、聚焦、关闭窗口
 *   4. 服务：时钟、文件系统、启动菜单
 */

import { Canvas } from './kernel-core.js';
import { Theme } from './kernel-core.js';
import { Window } from './kernel-core.js';
import { Vfs } from './kernel-core.js';
import { App } from './kernel-core.js';
import { Font } from './kernel-core.js';
import { NovaEvent, NovaKey } from './kernel-core.js';
import { BUILTIN_APPS } from './kernel-apps.js';

/** 应用 key -> 中文名。窗口标题、任务栏、桌面图标、开始菜单都用它。 */
const APP_LABELS = {
  terminal: '终端',
  files: '文件',
  about: '关于',
  clock: '时钟',
  notes: '记事本',
  paint: '画板',
};

/** 允许用中文指令打开窗口 */
const APP_ALIASES = {
  '终端': 'terminal',
  '文件': 'files',
  '关于': 'about',
  '时钟': 'clock',
  '记事本': 'notes',
  '画板': 'paint',
};

/** 欢迎页背景色（比桌面更暗，像开机画面） */
const WELCOME_BG = 0xFF05080C;

export class Kernel {
  constructor(host) {
    this.host = host;
    this.vfs = new Vfs(host);
    this.screen = new Canvas(host.width, host.height);

    /** 桌面图标命中区，每帧重建 */
    this.desktopIconHit = [];

    /** 窗口栈，末尾是最上层（也是焦点窗口） */
    this.windows = [];

    /** 可启动的应用工厂 */
    this.factories = new Map();

    // 拖动状态
    this.dragWin = null;
    this.dragOffX = 0;
    this.dragOffY = 0;
    this.dragging = false;

    // 缩放状态
    this.resizeWin = null;
    this.resizeStartX = 0;
    this.resizeStartY = 0;
    this.resizeStartW = 0;
    this.resizeStartH = 0;

    // 启动菜单
    this.startMenuOpen = false;

    // 光标（指针位置指示）
    this.pointerX = -1;
    this.pointerY = -1;
    this.pointerVisible = false;

    this.lastFrameMs = 0;
    this.needsRedraw = true;
    this.uptimeMs = 0;

    /** 已按下的指针目标，用于把 Move/Up 派发给同一个窗口 */
    this.touchWin = null;

    // ---- 欢迎页（开机第一屏：一行大字 + 一个输入框）----
    this.welcome = true;
    this.welcomeInput = '';
    this.welcomeArt = null;        // 艺术字画布，渲染一次后缓存
    this.welcomeMsg = '';          // 指令出错时的一行红字提示
    this.welcomeBlink = 0;
    this.showCursor = true;
    /** 用户名字，欢迎页里输入的第一个指令之外的文字可当作名字 */
    this.userName = '';

    // 窗口默认尺寸是按 1280x720 设计的，这里按屏幕高度等比缩放
    this.uiScale = this.screen.h / 720;

    // ---- 初始化 ----
    this.registerBuiltinApps();

    // 先看有没有上次退出时存下来的状态；有就整个恢复，没有才显示欢迎页。
    if (this.restoreState()) {
      this.welcome = false;
    }
  }

  /** 应用 key -> 中文显示名 */
  labelOf(name) {
    return APP_LABELS[name] || name;
  }

  // ---------------- 欢迎页 ----------------

  /**
   * 指令分发。欢迎页里输入的就是这个。
   * 支持 `terminal`、`open terminal`，以及中文的 `终端`。
   */
  runCommand(raw) {
    const line = String(raw).trim();
    if (line === '') {
      // 直接回车 = 进系统，默认给一个终端
      this.enterSystem();
      this.launch('terminal');
      return;
    }
    const parts = line.split(/\s+/);
    let cmd = parts[0].toLowerCase();
    if (cmd === 'open' && parts[1]) cmd = parts[1].toLowerCase();
    cmd = APP_ALIASES[cmd] || cmd;

    if (this.factories.has(cmd)) {
      this.welcomeInput = '';
      this.welcomeMsg = '';
      this.enterSystem();
      this.launch(cmd);
      return;
    }
    if (cmd === 'help' || cmd === '帮助') {
      this.welcomeMsg = '可用指令：' + this.appNames().map((n) => this.labelOf(n)).join(' ');
      return;
    }
    this.welcomeMsg = '未知指令：' + parts[0];
    this.needsRedraw = true;
  }

  /** 离开欢迎页，进入桌面 */
  enterSystem() {
    if (!this.welcome) return;
    this.welcome = false;
    this.welcomeMsg = '';
    this.saveState();
    this.needsRedraw = true;
  }

  handleWelcomeChar(ch) {
    this.welcomeBlink = 0;
    this.showCursor = true;
    if (ch === '\n' || ch === '\r') {
      this.runCommand(this.welcomeInput);
    } else if (ch === '\b') {
      this.welcomeInput = this.welcomeInput.slice(0, -1);
      this.welcomeMsg = '';
    } else if (ch.charCodeAt(0) >= 32 && this.welcomeInput.length < 32) {
      this.welcomeInput += ch;
      this.welcomeMsg = '';
    }
    this.needsRedraw = true;
  }

  /** 画欢迎页：一行大字 + 一个输入框，不做别的装饰 */
  drawWelcome() {
    const s = this.screen;
    const w = s.w, h = s.h;
    s.clear(WELCOME_BG);

    // 一行大字（渲染一次后缓存，因为描边+斜体不便宜）
    if (!this.welcomeArt) {
      this.welcomeArt = s.renderTextArt('Welcome to NovaDesk', 0xFFFFFFFF, 0xFF000000, 6, 0.28);
    }
    s.blitArtCentered(this.welcomeArt, w >> 1, Math.floor(h * 0.3));

    // 输入框
    const bw = Math.min(420, Math.floor(w * 0.56));
    const bh = 34;
    const bx = (w - bw) >> 1;
    const by = Math.floor(h * 0.62);

    s.fillRoundRect(bx, by, bw, bh, 6, 0xFF10161E);
    s.strokeRect(bx, by, bw, bh, this.welcomeMsg ? 0xFFE05252 : 0xFF33404F);

    const ty = by + ((bh - Font.CELL_H) >> 1) + 2;
    const text = this.welcomeInput;
    if (text.length === 0) {
      s.text('输入指令，如 terminal', bx + 12, ty, 0xFF46505F, 1);
    } else {
      s.text(text, bx + 12, ty, 0xFFE6EDF3, 1);
    }
    if (this.showCursor) {
      const cx = bx + 12 + (text.length ? s.textWidth(text, 1) : 0);
      s.fillRect(cx + 1, ty - 2, 2, Font.CELL_H, 0xFF4A90D9);
    }
    if (this.welcomeMsg) {
      s.textCentered(this.welcomeMsg, w >> 1, by + bh + 12, 0xFFE05252, 1);
    }
  }

  // ---------------- 状态保存 / 恢复 ----------------

  /**
   * 序列化整个系统状态。
   * 只通过 ABI 的文件接口落地，内核不直接碰 localStorage。
   */
  serializeState() {
    const wins = [];
    for (const w of this.windows) {
      let appState = null;
      try { appState = w.app.saveState ? w.app.saveState() : null; } catch (_) { appState = null; }
      wins.push({
        name: w.app.name, x: w.x, y: w.y, w: w.w, h: w.h,
        visible: w.visible, maximized: w.maximized,
        restore: [w.restoreX, w.restoreY, w.restoreW, w.restoreH],
        state: appState,
      });
    }
    const files = [];
    try {
      for (const [p, v] of this.vfs.files) files.push([p, v]);
    } catch (_) {}
    return JSON.stringify({
      v: 1,
      user: this.userName,
      wins: wins,
      files: files,
      uptime: this.uptimeMs,
    });
  }

  /** 把状态写进宿主（浏览器里落到 localStorage） */
  saveState() {
    try {
      const json = this.serializeState();
      const fd = this.host.open('/state.json', true);
      if (fd < 0) return false;
      this.host.write(fd, new TextEncoder().encode(json));
      this.host.close(fd);
      return true;
    } catch (_) {
      return false;
    }
  }

  /** 节流保存：打字时不希望每敲一个字就写一次磁盘 */
  saveStateSoon() {
    this._dirty = true;
  }

  /** 每帧调用；如果标了脏且距上次保存超过 1.5 秒，就落盘 */
  flushStateIfDirty() {
    if (!this._dirty) return;
    const now = this.host.clockMs();
    if (this._lastSave && now - this._lastSave < 1500) return;
    this._lastSave = now;
    this._dirty = false;
    this.saveState();
  }

  /** 从宿主读回状态并整个恢复。没有存档返回 false。 */
  restoreState() {
    let json = null;
    try {
      const fd = this.host.open('/state.json', false);
      if (fd < 0) return false;
      const bytes = this.host.read(fd, 4 << 20);
      this.host.close(fd);
      if (!bytes || bytes.length === 0) return false;
      json = new TextDecoder().decode(bytes);
    } catch (_) {
      return false;
    }
    if (!json) return false;

    let data;
    try { data = JSON.parse(json); } catch (_) { return false; }
    if (!data || data.v !== 1) return false;

    this.userName = data.user || '';
    // 恢复文件系统
    if (Array.isArray(data.files)) {
      for (const pair of data.files) {
        if (Array.isArray(pair) && pair.length === 2) this.vfs.files.set(pair[0], pair[1]);
      }
    }
    // 恢复窗口
    if (Array.isArray(data.wins)) {
      for (const rec of data.wins) {
        const f = this.factories.get(rec.name);
        if (!f) continue;
        const app = f();
        const win = new Window(this.labelOf(app.name), rec.x, rec.y, rec.w, rec.h, app);
        win.visible = rec.visible !== false;
        win.maximized = !!rec.maximized;
        if (Array.isArray(rec.restore) && rec.restore.length === 4) {
          win.restoreX = rec.restore[0]; win.restoreY = rec.restore[1];
          win.restoreW = rec.restore[2]; win.restoreH = rec.restore[3];
        }
        app.win = win;
        app.sys = this;
        win.resizeSurface();
        if (rec.state && app.loadState) {
          try { app.loadState(rec.state); } catch (_) {}
        }
        this.windows.push(win);
      }
    }
    return true;
  }

  registerBuiltinApps() {
    for (const name of Object.keys(BUILTIN_APPS)) {
      this.factories.set(name, BUILTIN_APPS[name]);
    }
  }

  appNames() {
    return Array.from(this.factories.keys());
  }

  screenW() { return this.screen.w; }
  screenH() { return this.screen.h; }
  uptimeSeconds() { return Math.floor(this.uptimeMs / 1000); }

  // ---------------- 窗口管理 ----------------

  launch(name) {
    const f = this.factories.get(name);
    if (!f) return null;
    const app = f();
    const size = app.defaultSize();
    // 应用的默认尺寸是按 1280x720 设计的，这里按屏幕高度等比缩放，
    // 这样 800x450 下窗口不会占满整个屏幕。
    const k = this.uiScale;
    const dw = Math.round(size[0] * k);
    const dh = Math.round(size[1] * k);
    // 级联摆放，避免完全重叠
    const off = Math.round((this.windows.length % 5) * 24 * k);
    const ww = Math.min(dw, this.screen.w - 24);
    const wh = Math.min(dh, this.screen.h - Theme.TASKBAR_H - 16);
    const wx = Math.min(Math.round(24 * k) + off, Math.max(0, this.screen.w - ww - 6));
    const wy = Math.min(Math.round(18 * k) + off, Math.max(0, this.screen.h - Theme.TASKBAR_H - wh - 6));
    const win = new Window(this.labelOf(app.name), wx, wy, ww, wh, app);
    app.win = win;
    app.sys = this;
    this.windows.push(win);
    win.resizeSurface();
    this.focus(win);
    this.needsRedraw = true;
    this.saveState();
    return win;
  }

  close(win) {
    win.app.onClose();
    const i = this.windows.indexOf(win);
    if (i >= 0) this.windows.splice(i, 1);
    if (this.windows.length > 0) this.focus(this.windows[this.windows.length - 1]);
    this.needsRedraw = true;
    this.saveState();
  }

  focus(win) {
    const i = this.windows.indexOf(win);
    if (i >= 0) {
      this.windows.splice(i, 1);
      this.windows.push(win);
    }
    this.needsRedraw = true;
  }

  moveTo(win, nx, ny) {
    win.x = nx;
    win.y = ny;
    this.needsRedraw = true;
  }

  clampWindow(win) {
    // 保证窗口始终有一块可抓取的区域留在屏幕内。
    // 之前只保证 80px，导致窗口能被拖到几乎完全出界（内容看不见也抓不回来）。
    const keep = Math.min(120, Math.max(80, Math.floor(win.w * 0.4)));
    const maxX = this.screen.w - keep;
    const maxY = this.screen.h - Theme.TASKBAR_H - Theme.TITLE_H;
    if (win.x > maxX) win.x = maxX;
    if (win.y > maxY) win.y = maxY;
    if (win.x < -(win.w - keep)) win.x = -(win.w - keep);
    if (win.y < 0) win.y = 0;
  }

  // ---------------- 事件 ----------------

  handleEvent(e) {
    if (e instanceof NovaEvent.Resize) {
      // 屏幕尺寸变化：重建画布并夹紧所有窗口
      this.screen = new Canvas(e.w, e.h);
      for (const w of this.windows) {
        if (w.x + w.w > e.w) w.x = Math.max(0, e.w - w.w);
        if (w.y + w.h > e.h - Theme.TASKBAR_H) {
          w.y = Math.max(0, e.h - Theme.TASKBAR_H - w.h);
        }
      }
      this.needsRedraw = true;
    } else if (e instanceof NovaEvent.Key) {
      this.handleKey(e);
    } else if (e instanceof NovaEvent.Text) {
      this.sendChar(e.ch);
    } else if (e instanceof NovaEvent.Down) {
      this.handleDown(e.x, e.y);
    } else if (e instanceof NovaEvent.Move) {
      this.handleMove(e.x, e.y);
    } else if (e instanceof NovaEvent.Up) {
      this.handleUp(e.x, e.y);
    }
  }

  handleKey(e) {
    if (!e.down) return;
    if (e.code === NovaKey.BACK) {
      if (this.startMenuOpen) { this.startMenuOpen = false; this.needsRedraw = true; return; }
      const top = this.windows.length ? this.windows[this.windows.length - 1] : null;
      if (top) this.close(top);
      return;
    }
    // 焦点窗口优先
    const top = this.windows.length ? this.windows[this.windows.length - 1] : null;
    if (!top) return;
    if (top.app.onKey(e.code)) { this.needsRedraw = true; return; }
    if (e.code === NovaKey.ESC) {
      if (!this.startMenuOpen) this.launch('terminal');
    } else if (e.code === NovaKey.TAB) {
      if (this.windows.length > 1) {
        const cur = this.windows.pop();
        this.windows.unshift(cur);
        this.focus(cur);
      }
    }
    this.needsRedraw = true;
  }

  sendChar(ch) {
    // 欢迎页：输入进的是指令框，不是窗口
    if (this.welcome) { this.handleWelcomeChar(ch); return; }
    const top = this.windows.length ? this.windows[this.windows.length - 1] : null;
    if (!top) return;
    if (top.app.onChar(ch)) { this.needsRedraw = true; this.saveStateSoon(); }
  }

  handleDown(px, py) {
    this.pointerX = px; this.pointerY = py; this.pointerVisible = true;
    this.needsRedraw = true;

    // 欢迎页上没有可点的东西 —— 只用键盘
    if (this.welcome) return;

    // 1. 任务栏最高优先级
    if (py >= this.screen.h - Theme.TASKBAR_H) {
      this.handleTaskbar(px, py);
      return;
    }

    // 2. 启动菜单覆盖层
    if (this.startMenuOpen) {
      if (this.handleStartMenuClick(px, py)) return;
      this.startMenuOpen = false;
      // 落在菜单外则吞掉这次点击
      return;
    }

    // 3. 桌面图标
    const names = this.appNames();
    for (const r of this.desktopIconHit) {
      if (px >= r[0] && px < r[0] + r[2] && py >= r[1] && py < r[1] + r[3]) {
        if (r[4] < names.length) {
          const w = this.launch(names[r[4]]);
          if (w) this.focus(w);
        }
        return;
      }
    }

    // 4. 从上到下找命中的窗口
    for (let i = this.windows.length - 1; i >= 0; i--) {
      const w = this.windows[i];
      if (!w.visible || !w.contains(px, py)) continue;
      this.focus(w);

      if (w.hitClose(px, py)) { this.close(w); return; }
      if (w.hitMin(px, py)) { w.visible = false; this.needsRedraw = true; return; }
      if (w.hitMax(px, py)) { this.toggleMaximize(w); return; }

      if (w.hitTitle(px, py)) {
        // 标题栏空白处 → 拖动
        this.dragWin = w;
        this.dragging = false;
        this.dragOffX = px - w.x;
        this.dragOffY = py - w.y;
        return;
      }

      // 右下角缩放热区
      if (px >= w.x + w.w - 22 && py >= w.y + w.h - 22) {
        this.resizeWin = w;
        this.resizeStartX = px; this.resizeStartY = py;
        this.resizeStartW = w.w; this.resizeStartH = w.h;
        return;
      }

      // 内容区
      const local = w.toLocal(px, py);
      if (local !== null) {
        this.touchWin = w;
        w.app.onTouch(local[0], local[1], App.PHASE_DOWN);
        this.needsRedraw = true;
      }
      return;
    }
  }

  handleMove(px, py) {
    this.pointerX = px; this.pointerY = py; this.pointerVisible = true;

    if (this.dragWin) {
      const w = this.dragWin;
      const nx = px - this.dragOffX;
      const ny = py - this.dragOffY;
      if (Math.abs(nx - w.x) > 1 || Math.abs(ny - w.y) > 1) this.dragging = true;
      w.x = nx; w.y = ny;
      this.clampWindow(w);
      this.needsRedraw = true;
      return;
    }

    if (this.resizeWin) {
      const w = this.resizeWin;
      const nw = Math.max(Theme.MIN_W, this.resizeStartW + (px - this.resizeStartX));
      const nh = Math.max(Theme.MIN_H, this.resizeStartH + (py - this.resizeStartY));
      w.w = nw; w.h = nh;
      w.resizeSurface();
      this.needsRedraw = true;
      return;
    }

    if (this.touchWin) {
      const w = this.touchWin;
      const local = w.toLocal(px, py);
      if (local !== null) {
        w.app.onTouch(local[0], local[1], App.PHASE_MOVE);
        this.needsRedraw = true;
      }
      return;
    }
    this.needsRedraw = true;
  }

  handleUp(px, py) {
    if (this.dragging) this.needsRedraw = true;
    this.dragWin = null;
    this.dragging = false;
    this.resizeWin = null;
    if (this.touchWin) {
      const w = this.touchWin;
      const local = w.toLocal(px, py);
      if (local !== null) w.app.onTouch(local[0], local[1], App.PHASE_UP);
      this.needsRedraw = true;
    }
    this.touchWin = null;
    // 拖动/缩放/点击结束后记一笔，位置和内容都能还原
    this.saveStateSoon();
  }

  toggleMaximize(w) {
    if (w.maximized) {
      w.x = w.restoreX; w.y = w.restoreY; w.w = w.restoreW; w.h = w.restoreH;
      w.maximized = false;
    } else {
      w.restoreX = w.x; w.restoreY = w.y; w.restoreW = w.w; w.restoreH = w.h;
      w.x = 0; w.y = 0;
      w.w = this.screen.w;
      w.h = this.screen.h - Theme.TASKBAR_H;
      w.maximized = true;
    }
    w.resizeSurface();
    this.needsRedraw = true;
  }

  // ---------------- 任务栏 ----------------

  taskbarItemRects() {
    const out = [];
    let x = 44;
    const y = this.screen.h - Theme.TASKBAR_H + 4;
    const hh = Theme.TASKBAR_H - 8;
    const clockW = 62;
    for (const w of this.windows) {
      // 中文名是"全角"，每个字比 ASCII 宽得多，所以按显示名估宽
      let ww = 14;
      for (const ch of this.labelOf(w.app.name)) {
        ww += (Font.isCJK(ch) ? 22 : 7);
      }
      ww = Math.min(ww, 110);
      if (x + ww > this.screen.w - clockW - 4) break;   // 别压到右边的时钟
      out.push([w, [x, y, ww, hh]]);
      x += ww + 4;
    }
    return out;
  }

  handleTaskbar(px, py) {
    // 开始按钮
    if (px >= 4 && px <= 38) {
      this.startMenuOpen = !this.startMenuOpen;
      this.needsRedraw = true;
      return;
    }
    // 时钟区域
    if (px >= this.screen.w - 70) { this.needsRedraw = true; return; }

    for (const [w, r] of this.taskbarItemRects()) {
      if (px >= r[0] && px < r[0] + r[2] && py >= r[1] && py < r[1] + r[3]) {
        const top = this.windows.length ? this.windows[this.windows.length - 1] : null;
        if (!w.visible) { w.visible = true; this.focus(w); }
        else if (top === w) w.visible = false;
        else this.focus(w);
        this.needsRedraw = true;
        this.saveStateSoon();
        return;
      }
    }
    this.needsRedraw = true;
  }

  startMenuItemRects() {
    const names = this.appNames();
    const out = [];
    const mw = 132;
    const ih = 28;
    const mx = 4;
    const totalH = 26 + names.length * ih + 6;
    const my = this.screen.h - Theme.TASKBAR_H - totalH;
    names.forEach((n, i) => {
      out.push([n, [mx + 4, my + 26 + i * ih, mw - 8, ih - 2]]);
    });
    return out;
  }

  handleStartMenuClick(px, py) {
    for (const [name, r] of this.startMenuItemRects()) {
      if (px >= r[0] && px < r[0] + r[2] && py >= r[1] && py < r[1] + r[3]) {
        this.launch(name);
        this.startMenuOpen = false;
        this.needsRedraw = true;
        return true;
      }
    }
    // 点击开始按钮本身
    if (px >= 4 && px <= 46 && py >= this.screen.h - Theme.TASKBAR_H) {
      this.startMenuOpen = false;
      this.needsRedraw = true;
      return true;
    }
    return false;
  }

  // ---------------- 主循环 ----------------

  /**
   * 处理所有待办事件 + 更新逻辑 + 需要时重绘。
   * 宿主每帧调用一次。
   */
  tick() {
    const now = this.host.clockMs();
    const dt = this.lastFrameMs === 0 ? 16 : Math.min(Math.max(now - this.lastFrameMs, 0), 100);
    this.lastFrameMs = now;
    this.uptimeMs += dt;

    // 排空输入队列
    let guard = 0;
    while (guard++ < 256) {
      const e = this.host.pollEvent();
      if (!e) break;
      this.handleEvent(e);
    }

    // 欢迎页：只画那一屏，光标闪烁
    if (this.welcome) {
      this.welcomeBlink += dt;
      if (this.welcomeBlink > 500) {
        this.welcomeBlink = 0;
        this.showCursor = !this.showCursor;
        this.needsRedraw = true;
      }
      if (this.needsRedraw) {
        this.drawWelcome();
        this.host.present(this.screen.px);
        this.needsRedraw = false;
      }
      return;
    }

    // 更新应用
    for (const w of this.windows) {
      if (w.visible && w.app.update(dt)) this.needsRedraw = true;
    }
    if (this.startMenuOpen) this.needsRedraw = true;

    // 状态落盘（节流）
    this.flushStateIfDirty();

    if (this.needsRedraw) {
      this.compose();
      this.host.present(this.screen.px);
      this.needsRedraw = false;
    }
  }

  // ---------------- 合成 ----------------

  compose() {
    // 1. 先让每个应用把界面画进自己的窗口表面。
    //    这是内核的职责：应用只负责"画内容"，不管自己在屏幕哪。
    for (const w of this.windows) {
      if (w.visible) {
        w.surface.clear(0x00000000);
        w.app.draw(w.surface);
      }
    }
    // 2. 再合成整屏
    this.drawDesktop();
    for (const w of this.windows) if (w.visible) this.drawWindow(w);
    this.drawTaskbar();
    if (this.startMenuOpen) this.drawStartMenu();
    this.drawPointer();
  }

  drawDesktop() {
    this.desktopIconHit = [];
    // 纵向渐变
    const s = this.screen;
    for (let y = 0; y < s.h; y++) {
      const t = y / s.h;
      const r = this.lerp((Theme.DESK_TOP >> 16) & 0xFF, (Theme.DESK_BOTTOM >> 16) & 0xFF, t);
      const g = this.lerp((Theme.DESK_TOP >> 8) & 0xFF, (Theme.DESK_BOTTOM >> 8) & 0xFF, t);
      const b = this.lerp(Theme.DESK_TOP & 0xFF, Theme.DESK_BOTTOM & 0xFF, t);
      const c = ((0xFF << 24) | (r << 16) | (g << 8) | b) >>> 0;
      const base = y * s.w;
      for (let x = 0; x < s.w; x++) s.px[base + x] = c;
    }
    // 桌面上的隐形网格，给点"电脑"的感觉
    const grid = 0x14FFFFFF;
    let gx = 0;
    while (gx < s.w) {
      for (let y = 0; y < s.h - Theme.TASKBAR_H; y++) s.blend(gx, y, grid);
      gx += 80;
    }
    let gy = 0;
    while (gy < s.h - Theme.TASKBAR_H) {
      for (let x = 0; x < s.w; x++) s.blend(x, gy, grid);
      gy += 80;
    }

    // 桌面图标（左上角一列）
    const icons = this.appNames();
    const IW = 56, IH = 62, GAP = 6;
    let iy = 10;
    icons.forEach((name, i) => {
      const ix = 8;
      s.fillRoundRect(ix, iy, IW, IH, 6, 0x18FFFFFF);
      let glyph = '?';
      try { glyph = this.factories.get(name)().icon(); } catch (_) {}
      s.text(glyph, ix + (IW >> 1) - (s.textWidth(glyph, 2) >> 1), iy + 6, Theme.ACCENT, 2);
      s.textCentered(this.labelOf(name), ix + (IW >> 1), iy + IH - Font.CELL_H - 4, Theme.TEXT_DIM, 1);
      this.desktopIconHit.push([ix, iy, IW, IH, i]);
      iy += IH + GAP;
    });
  }

  drawWindow(w) {
    const s = this.screen;
    const top = this.windows.length ? this.windows[this.windows.length - 1] : null;
    const focused = top === w;

    // 阴影
    s.fillRoundRect(w.x + 4, w.y + 5, w.w, w.h, 6, Theme.WIN_SHADOW);
    // 主体
    s.fillRoundRect(w.x, w.y, w.w, w.h, 6, Theme.WIN_BG);
    // 标题栏
    s.fillRect(w.x + 1, w.y + 1, w.w - 2, Theme.TITLE_H - 1,
      focused ? Theme.WIN_TITLE_ACTIVE : Theme.WIN_TITLE_INACTIVE);
    // 标题文字
    s.text(w.title, w.x + 10, w.y + 9, focused ? Theme.TEXT_TITLE : Theme.TEXT_DIM, 1);

    // 三个按钮
    this.dot(w.closeBtnCx(), w.closeBtnCy(), Theme.BTN_CLOSE);
    this.dot(w.minBtnCx(), w.minBtnCy(), Theme.BTN_MIN);
    this.dot(w.maxBtnCx(), w.maxBtnCy(), Theme.BTN_MAX);

    // 边框
    s.strokeRect(w.x, w.y, w.w, w.h,
      focused ? Theme.WIN_BORDER_ACTIVE : Theme.WIN_BORDER_INACTIVE);

    // 内容
    s.blit(w.surface, 0, 0, w.surface.w, w.surface.h, w.contentX(), w.contentY());

    // 右下角缩放抓手
    const hx = w.x + w.w - 4;
    const hy = w.y + w.h - 4;
    for (let i = 0; i < 3; i++) {
      s.line(hx - i * 4, hy, hx, hy - i * 4, 0x60FFFFFF);
    }
  }

  dot(cx, cy, color) {
    const s = this.screen;
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        if (dx * dx + dy * dy <= 16) s.blend(cx + dx, cy + dy, color);
      }
    }
  }

  drawTaskbar() {
    const s = this.screen;
    const ty = s.h - Theme.TASKBAR_H;
    s.fillRect(0, ty, s.w, Theme.TASKBAR_H, Theme.TASKBAR_BG);
    s.fillRect(0, ty, s.w, 1, 0x30FFFFFF);

    // 开始按钮
    const active = this.startMenuOpen;
    s.fillRoundRect(4, ty + 4, 34, Theme.TASKBAR_H - 8, 4,
      active ? Theme.ACCENT : Theme.TASKBAR_ITEM);
    // 用四个小方块拼一个"窗口"图标
    const ic = active ? 0xFFFFFFFF : Theme.ACCENT;
    s.fillRect(11, ty + 10, 6, 5, ic);
    s.fillRect(21, ty + 10, 6, 5, ic);
    s.fillRect(11, ty + 17, 6, 5, ic);
    s.fillRect(21, ty + 17, 6, 5, ic);

    // 窗口按钮
    const top = this.windows.length ? this.windows[this.windows.length - 1] : null;
    for (const [w, r] of this.taskbarItemRects()) {
      const isTop = top === w;
      s.fillRoundRect(r[0], r[1], r[2], r[3], 3,
        (isTop && w.visible) ? Theme.TASKBAR_ITEM_ACTIVE : Theme.TASKBAR_ITEM);
      s.text(this.labelOf(w.app.name), r[0] + 7, r[1] + 4,
        w.visible ? Theme.TEXT : Theme.TEXT_DIM, 1);
      if (isTop && w.visible) s.fillRect(r[0], r[1] + r[3] - 2, r[2], 2, Theme.ACCENT);
    }

    // 右侧时钟
    const secs = Math.floor(this.uptimeMs / 1000);
    const hh = Math.floor(secs / 3600) % 100;
    const mm = Math.floor(secs / 60) % 60;
    const ss = secs % 60;
    const clock = this.pad(hh) + ':' + this.pad(mm) + ':' + this.pad(ss);
    s.text(clock, s.w - 62, ty + 11, Theme.TEXT_DIM, 1);
  }

  pad(v) {
    return v < 10 ? '0' + v : String(v);
  }

  drawStartMenu() {
    const s = this.screen;
    const names = this.appNames();
    const mw = 132;
    const ih = 28;
    const totalH = 26 + names.length * ih + 6;
    const mx = 4;
    const my = s.h - Theme.TASKBAR_H - totalH;
    s.fillRoundRect(mx + 3, my + 4, mw, totalH, 8, Theme.WIN_SHADOW);
    s.fillRoundRect(mx, my, mw, totalH, 8, 0xF01E2228);
    s.strokeRect(mx, my, mw, totalH, 0xFF4A90D9);
    s.text('应用', mx + 10, my + 6, Theme.ACCENT, 1);
    s.fillRect(mx + 6, my + 24, mw - 12, 1, 0x30FFFFFF);
    names.forEach((n, i) => {
      const r = [mx + 4, my + 26 + i * ih, mw - 8, ih - 2];
      s.fillRoundRect(r[0], r[1], r[2], r[3], 4, 0x18FFFFFF);
      s.text(this.labelOf(n), r[0] + 10, r[1] + 4, Theme.TEXT, 1);
    });
  }

  drawPointer() {
    if (!this.pointerVisible) return;
    // 指针点画一个小圆环
    const s = this.screen;
    for (let dy = -10; dy <= 10; dy++) {
      for (let dx = -10; dx <= 10; dx++) {
        const d2 = dx * dx + dy * dy;
        if (d2 >= 64 && d2 <= 100) s.blend(this.pointerX + dx, this.pointerY + dy, 0x80FFFFFF);
      }
    }
  }

  lerp(a, b, t) {
    return Math.floor(a + (b - a) * t);
  }
}

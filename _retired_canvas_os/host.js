/**
 * NovaDesk 浏览器宿主
 *
 * 这是 Kotlin 版服务端三个文件的对应物：
 *   AndroidHost.kt  ->  BrowserHost  （文件、时钟、事件队列）
 *   NovaView.kt     ->  BlitSurface  （把像素缓冲贴到 canvas）
 *   NovaActivity.kt ->  boot()       （开机、主循环）
 *
 * 它只实现那 8 个系统调用，不含任何"窗口/按钮"概念。
 * 浏览器在这里同样被降级为：一块画布 + 输入事件 + 一个时钟。
 */

import { NovaEvent, NovaKey } from './kernel-core.js';
import { Kernel } from './kernel.js';

export class BrowserHost {
  constructor(width, height) {
    this.width = width;
    this.height = height;
    /** 内核画完的一整帧，原样交给 canvas */
    this.frame = new Int32Array(width * height);
    this._events = [];
    this._t0 = performance.now();
    /** 内存文件系统之外的持久层（localStorage） */
    this._files = new Map();
    this.loadFromStorage();
  }

  resize(w, h) {
    this.width = w;
    this.height = h;
    this.frame = new Int32Array(w * h);
    this.push(new NovaEvent.Resize(w, h));
  }

  push(e) {
    if (this._events.length < 512) this._events.push(e);
  }

  // ---------------- ABI 实现 ----------------

  present(argb) {
    // 宿主不遍历、不解析、不修改像素 —— 只保存这一帧。
    // 内核在这块内存里已经画好了窗口、文字、任务栏。
    if (argb !== this.frame) this.frame.set(argb.subarray(0, this.frame.length));
  }

  clockMs() {
    return performance.now() - this._t0;
  }

  pollEvent() {
    return this._events.length ? this._events.shift() : null;
  }

  // 文件系统：落在 localStorage，模拟"手机上的私有目录"
  _key(path) { return 'novadesk:' + path.replace(/^\/+/, ''); }

  loadFromStorage() {
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('novadesk:')) {
          this._files.set(k.slice(9), localStorage.getItem(k) || '');
        }
      }
    } catch (_) { /* 隐私模式下 localStorage 可能不可用 */ }
  }

  open(path, create) {
    const p = path.replace(/^\/+/, '');
    if (!this._files.has(p)) {
      if (!create) return -1;
      this._files.set(p, '');
    }
    this._openPath = p;
    return 1;
  }

  read(fd, maxLen) {
    const p = this._openPath;
    if (!p || !this._files.has(p)) return null;
    const s = this._files.get(p);
    return new TextEncoder().encode(s).slice(0, maxLen);
  }

  write(fd, data) {
    const p = this._openPath;
    if (!p) return -1;
    const s = new TextDecoder().decode(data);
    this._files.set(p, s);
    try { localStorage.setItem(this._key(p), s); } catch (_) {}
    return data.length;
  }

  close(fd) { this._openPath = null; }

  list(dir) {
    const d = dir.replace(/^\/+/, '');
    const out = [];
    for (const k of this._files.keys()) {
      if (k.startsWith(d) && !k.slice(d.length).includes('/')) out.push(k);
    }
    return out;
  }

  exit(code) { /* 网页里不退出 */ }
}

/**
 * 把内核的 Int32 像素缓冲贴到 canvas 上。
 * 这是 NovaView.kt 的对应物。
 */
export class BlitSurface {
  constructor(canvas, host) {
    this.canvas = canvas;
    this.host = host;
    this.ctx = canvas.getContext('2d', { alpha: false });
    this.img = null;
    this.buf32 = null;
    this.w = 0;
    this.h = 0;
  }

  render() {
    const w = this.host.width;
    const h = this.host.height;
    if (w <= 1 || h <= 1) return;

    if (this.w !== w || this.h !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
      this.img = this.ctx.createImageData(w, h);
      this.buf32 = new Uint32Array(this.img.data.buffer);
      this.w = w;
      this.h = h;
    }

    // 内核交出的一整屏，直接搬进 ImageData。
    // 安卓那边用的是 Bitmap.setPixels，这里是同一件事。
    //
    // 通道顺序必须小心：
    //   内核像素是 ARGB（0xAARRGGBB）
    //   ImageData 在内存里是 R,G,B,A 四个字节
    //   小端机器上读成 Uint32 就是 0xAABBGGRR
    // 所以要把 R 和 B 对调，且全部用无符号运算避免符号位污染。
    const src = this.host.frame;
    const dst = this.buf32;
    for (let i = 0; i < dst.length; i++) {
      const c = src[i];
      const a = (c >>> 24) & 0xff;
      const r = (c >>> 16) & 0xff;
      const g = (c >>> 8) & 0xff;
      const b = c & 0xff;
      dst[i] = ((a << 24) | (b << 16) | (g << 8) | r) >>> 0;
    }
    this.ctx.putImageData(this.img, 0, 0);
  }
}

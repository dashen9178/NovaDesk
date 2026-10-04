/**
 * NovaDesk 浏览器引导器
 *
 * 对应 Kotlin 版的 NovaActivity.kt：
 *   - 建立宿主和内核
 *   - 铺满屏幕、处理缩放
 *   - 跑主循环（requestAnimationFrame）
 *   - 把鼠标/触摸/键盘翻译成 NovaEvent 灌进内核
 *
 * 屏幕上没有任何 HTML 控件 —— 你看到的一切都由内核画进那块像素缓冲。
 */

import { NovaEvent, NovaKey } from './kernel-core.js';
import { Font, Canvas } from './kernel-core.js';
import { Kernel } from './kernel.js';
import { BrowserHost, BlitSurface } from './host.js';

/**
 * NovaDesk 的虚拟分辨率。按窗口大小等比缩放，保持 16:9。
 *
 * 定成 800x450 而不是 1280x720，是为了让中文看得清：
 * 中文字形是 20 多像素的点阵，虚拟分辨率越低，它在屏幕上占的比例越大。
 * 手机横屏大约 700x390 CSS 像素，800x450 缩放后中文差不多是 14px，够读。
 */
const VW = 800;
const VH = 450;

export function boot(canvas) {
  const host = new BrowserHost(VW, VH);
  const screen = new BlitSurface(canvas, host);
  const kernel = new Kernel(host);

  // ---------------- 坐标换算 ----------------
  // 浏览器像素 -> NovaDesk 虚拟坐标
  function toVirtual(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    const x = Math.round((clientX - r.left) / r.width * VW);
    const y = Math.round((clientY - r.top) / r.height * VH);
    return [x, y];
  }

  // ---------------- 输入 ----------------
  // 用 pointer 事件统一鼠标、触摸、手写笔
  let lastTouch = 0;

  canvas.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    canvas.setPointerCapture?.(ev.pointerId);
    const [x, y] = toVirtual(ev.clientX, ev.clientY);
    host.push(new NovaEvent.Down(x, y, host.clockMs()));
    canvas.focus();
  });

  canvas.addEventListener('pointermove', (ev) => {
    const [x, y] = toVirtual(ev.clientX, ev.clientY);
    host.push(new NovaEvent.Move(x, y, host.clockMs()));
  });

  const endPointer = (ev) => {
    const [x, y] = toVirtual(ev.clientX, ev.clientY);
    host.push(new NovaEvent.Up(x, y, host.clockMs()));
  };
  canvas.addEventListener('pointerup', endPointer);
  canvas.addEventListener('pointercancel', endPointer);

  // 阻止移动端的手势干扰（缩放/滚动）
  canvas.addEventListener('touchstart', (ev) => ev.preventDefault(), { passive: false });
  canvas.addEventListener('touchmove', (ev) => ev.preventDefault(), { passive: false });

  // ---------------- 键盘 ----------------
  // 内核只认字符和少数功能键。这里把浏览器按键翻译过去。
  window.addEventListener('keydown', (ev) => {
    // 功能键优先
    let handled = true;
    switch (ev.key) {
      case 'Enter':
        host.push(new NovaEvent.Text('\n'));
        break;
      case 'Backspace':
        host.push(new NovaEvent.Text('\b'));
        break;
      case 'Escape':
        host.push(new NovaEvent.Key(NovaKey.ESC, true, host.clockMs()));
        break;
      case 'Tab':
        host.push(new NovaEvent.Key(NovaKey.TAB, true, host.clockMs()));
        break;
      case 'ArrowUp':
        host.push(new NovaEvent.Key(NovaKey.UP, true, host.clockMs()));
        break;
      case 'ArrowDown':
        host.push(new NovaEvent.Key(NovaKey.DOWN, true, host.clockMs()));
        break;
      case 'ArrowLeft':
        host.push(new NovaEvent.Key(NovaKey.LEFT, true, host.clockMs()));
        break;
      case 'ArrowRight':
        host.push(new NovaEvent.Key(NovaKey.RIGHT, true, host.clockMs()));
        break;
      default:
        // 单个可打印字符
        if (ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey) {
          host.push(new NovaEvent.Text(ev.key));
        } else {
          handled = false;
        }
    }
    if (handled) {
      ev.preventDefault();
      ev.stopPropagation();
    }
  }, true);

  // 手机上的软键盘：一个隐藏 input 承接输入
  const ime = document.getElementById('nova-ime');
  if (ime) {
    let lastLen = 0;
    ime.addEventListener('input', () => {
      const s = ime.value;
      if (s.length > lastLen) {
        for (let i = lastLen; i < s.length; i++) {
          const c = s[i];
          if (c !== '\n') host.push(new NovaEvent.Text(c));
        }
      } else if (s.length < lastLen) {
        for (let i = 0; i < lastLen - s.length; i++) host.push(new NovaEvent.Text('\b'));
      }
      lastLen = s.length;
      if (s.length > 64) { ime.value = ''; lastLen = 0; }
    });
    // 第一次触摸屏幕就把软键盘叫出来（和安卓版行为一致）
    canvas.addEventListener('pointerdown', () => {
      if (Date.now() - lastTouch > 1000) {
        lastTouch = Date.now();
        ime.focus({ preventScroll: true });
      }
    });
  }

  // ---------------- 主循环 ----------------
  let running = true;

  function frame() {
    if (!running) return;
    try {
      kernel.tick();
      screen.render();
    } catch (err) {
      console.error('[NovaDesk] kernel loop error:', err);
      running = false;
      const el = document.getElementById('nova-error');
      if (el) {
        el.style.display = 'block';
        el.textContent = 'Kernel error: ' + (err && err.message ? err.message : String(err));
      }
      return;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // 调试入口：控制台里可以摸到内核
  window.__nova = { kernel, host, screen, NovaEvent, NovaKey, Font, Canvas, boot };

  return { kernel, host, screen };
}

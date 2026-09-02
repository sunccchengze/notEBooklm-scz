/* =========================================================================
   惯性平滑滚动 —— 参照英仔官网的 Lenis 配置（lerp .095 / wheelMultiplier .95）
   零依赖实现，对任意可滚动容器生效。
   ========================================================================= */

const LERP = 0.095;          // 每帧向目标靠拢的比例，越小越"重"
const WHEEL_MULT = 0.95;     // 滚轮增益
const EPS = 0.35;            // 收敛阈值

class Inertia {
  constructor(el) {
    this.el = el;
    this.target = el.scrollTop;
    this.running = false;
    this.raf = null;

    // 指针/触摸设备用原生滚动，体验更好
    this.onWheel = this.onWheel.bind(this);
    this.tick = this.tick.bind(this);
    el.addEventListener("wheel", this.onWheel, { passive: false });

    // 外部（如代码设置 scrollTop）改变时重新同步
    el.addEventListener("scroll", () => {
      if (!this.running) this.target = el.scrollTop;
    }, { passive: true });
  }

  get max() {
    return Math.max(0, this.el.scrollHeight - this.el.clientHeight);
  }

  onWheel(e) {
    // 没有可滚动空间就交还给浏览器
    if (this.max <= 0) return;
    if (e.ctrlKey) return;                 // 缩放手势
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;  // 横向滚动

    const next = clamp(this.target + e.deltaY * WHEEL_MULT, 0, this.max);

    // 已经到边界，让事件冒泡出去（overscroll-behavior 会兜住）
    if (next === this.target && (this.target <= 0 || this.target >= this.max)) return;

    e.preventDefault();
    this.target = next;
    this.start();
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.raf = requestAnimationFrame(this.tick);
  }

  tick() {
    const cur = this.el.scrollTop;
    const diff = this.target - cur;

    if (Math.abs(diff) < EPS) {
      this.el.scrollTop = this.target;
      this.running = false;
      return;
    }

    this.el.scrollTop = cur + diff * LERP;
    this.raf = requestAnimationFrame(this.tick);
  }

  /** 平滑滚到底（新消息进来时用） */
  toBottom() {
    this.target = this.max;
    this.start();
  }

  /** 立即对齐，不做动画 */
  sync() {
    this.target = this.el.scrollTop;
  }
}

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

const registry = new WeakMap();

export function smooth(el) {
  if (!el) return null;
  // 触屏设备保留原生滚动（更跟手，也避免与系统惯性打架）
  if (window.matchMedia("(pointer: coarse)").matches) return null;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return null;

  let inst = registry.get(el);
  if (!inst) {
    inst = new Inertia(el);
    registry.set(el, inst);
  }
  return inst;
}

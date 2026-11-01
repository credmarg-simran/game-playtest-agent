// Injected before any game code runs. Puts the page clock under our control so a slow
// decision-maker can play a fast game: the game only advances when we ask it to.
//
// One virtual frame = 1000/fps ms. requestAnimationFrame callbacks, performance.now(),
// Date and (optionally) setTimeout/setInterval all run on virtual time.
(function () {
  if (window.__playtest) return;
  const cfg = Object.assign({ fps: 60, framesPerTick: 20, virtualTimers: true }, window.__PLAYTEST_CFG || {});
  const FRAME_MS = 1000 / cfg.fps;

  const realRAF = window.requestAnimationFrame.bind(window);
  const realNow = performance.now.bind(performance);
  const RealDate = Date;
  const realSetTimeout = window.setTimeout.bind(window);
  const realClearTimeout = window.clearTimeout.bind(window);
  const realSetInterval = window.setInterval.bind(window);
  const realClearInterval = window.clearInterval.bind(window);

  let vnow = realNow();
  const dateOffset = RealDate.now() - vnow;
  let frame = 0;
  let paused = false;
  let pending = 0;
  let waiters = [];
  let rafQueue = [];
  let rafId = 1;
  const errors = [];
  const games = [];

  function logError(err, where) {
    errors.push({ message: String((err && err.message) || err), where, frame });
    if (errors.length > 200) errors.shift();
  }

  performance.now = () => vnow;
  class VirtualDate extends RealDate {
    constructor(...args) { if (args.length) super(...args); else super(vnow + dateOffset); }
    static now() { return Math.floor(vnow + dateOffset); }
  }
  window.Date = VirtualDate;

  window.requestAnimationFrame = cb => { const id = rafId++; rafQueue.push({ id, cb }); return id; };
  window.cancelAnimationFrame = id => { rafQueue = rafQueue.filter(r => r.id !== id); };

  const timers = new Map();
  let timerId = 1e6;
  if (cfg.virtualTimers) {
    window.setTimeout = (cb, delay, ...args) => {
      delay = Math.max(0, Number(delay) || 0);
      if (delay === 0) return realSetTimeout(cb, 0, ...args);
      const id = timerId++;
      timers.set(id, { cb, args, at: vnow + delay, every: 0 });
      return id;
    };
    window.setInterval = (cb, delay, ...args) => {
      delay = Math.max(1, Number(delay) || 1);
      const id = timerId++;
      timers.set(id, { cb, args, at: vnow + delay, every: delay });
      return id;
    };
    window.clearTimeout = window.clearInterval = id => {
      if (timers.has(id)) timers.delete(id); else { realClearTimeout(id); realClearInterval(id); }
    };
  }

  function runTimers() {
    const due = [...timers].filter(([, t]) => t.at <= vnow).sort((a, b) => a[1].at - b[1].at);
    for (const [id, t] of due) {
      if (!timers.has(id)) continue;
      if (t.every) t.at = Math.max(t.at + t.every, vnow + 1); else timers.delete(id);
      try { typeof t.cb === 'function' ? t.cb(...t.args) : (0, eval)(String(t.cb)); }
      catch (e) { logError(e, 'timer'); }
    }
  }

  function runFrame() {
    vnow += FRAME_MS;
    frame++;
    const queue = rafQueue;
    rafQueue = [];
    for (const r of queue) {
      try { r.cb(vnow); } catch (e) { logError(e, 'frame'); realSetTimeout(() => { throw e; }, 0); }
    }
    runTimers();
  }

  function tick() {
    realRAF(tick);
    if (pending > 0) {
      let n = Math.min(pending, cfg.framesPerTick);
      while (n-- > 0) { runFrame(); pending--; }
      if (pending === 0) { const w = waiters; waiters = []; w.forEach(resolve => resolve(frame)); }
    } else if (!paused) {
      runFrame();
    }
  }
  realRAF(tick);

  window.addEventListener('error', ev => logError(ev.error || ev.message, 'window'));
  window.addEventListener('unhandledrejection', ev => logError(ev.reason, 'promise'));

  // Engine hooks. Phaser builds that set window.Phaser get wrapped here; bundled builds are
  // rewritten at build time to call __ptNew instead of `new Game(...)`.
  window.__ptNew = (Ctor, ...args) => { const g = new Ctor(...args); games.push(g); window.__game = g; return g; };
  let phaser = window.Phaser;
  try {
    Object.defineProperty(window, 'Phaser', {
      configurable: true,
      get() { return phaser; },
      set(P) {
        phaser = P;
        if (P && typeof P.Game === 'function' && !P.Game.__wrapped) {
          const Orig = P.Game;
          P.Game = new Proxy(Orig, { construct: (t, a, nt) => { const g = Reflect.construct(t, a, nt); games.push(g); window.__game = g; return g; } });
          P.Game.__wrapped = true;
        }
      },
    });
  } catch (e) { /* page defined it first */ }

  function plain(v, depth) {
    if (v === null || v === undefined) return v;
    const t = typeof v;
    if (t === 'number') return Number.isFinite(v) ? Math.round(v * 100) / 100 : null;
    if (t === 'boolean') return v;
    if (t === 'string') return v.slice(0, 120);
    if (t === 'function' || depth <= 0) return undefined;
    if (Array.isArray(v)) return v.slice(0, 12).map(x => plain(x, depth - 1));
    if (t === 'object') {
      if (v instanceof Node) return undefined;
      const out = {};
      let n = 0;
      for (const k of Object.keys(v)) {
        if (n++ > 25) break;
        try { const pv = plain(v[k], depth - 1); if (pv !== undefined) out[k] = pv; } catch (e) { }
      }
      return out;
    }
    return undefined;
  }

  // Evaluate named JS expressions against the page. When we captured an engine instance it is
  // available as `game`; otherwise `game` is left alone so a page global of that name still works.
  const compiled = new Map();
  function read(exprs) {
    const out = {};
    for (const [name, expr] of Object.entries(exprs || {})) {
      try {
        const key = (games.length ? 'g:' : 'n:') + expr;
        if (!compiled.has(key)) {
          const body = '"use strict"; return (' + expr + ');';
          compiled.set(key, games.length ? Function('game', body) : Function(body));
        }
        out[name] = plain(compiled.get(key)(games[0]), 3);
      } catch (e) { out[name] = null; }
    }
    return out;
  }

  // Globals with game-ish names. Used when we have no source code to tell us where state lives.
  const NAMES = /score|lives|life|level|health|hp|coins|wave|stage|best|high|over|dead|alive|state|game/i;
  function scanGlobals() {
    const out = {};
    for (const k of Object.keys(window)) {
      if (!NAMES.test(k) || k.startsWith('__')) continue;
      try {
        const v = plain(window[k], 2);
        if (v !== undefined && v !== null && JSON.stringify(v).length < 1500) out[k] = v;
      } catch (e) { }
    }
    return out;
  }

  // Without source, a captured Phaser game still shows its scenes and their fields at runtime.
  const ENGINE_KEYS = new Set(['sys', 'game', 'anims', 'cache', 'registry', 'sound', 'textures', 'events', 'cameras',
    'add', 'make', 'scene', 'children', 'data', 'input', 'load', 'lights', 'plugins', 'time', 'tweens', 'physics',
    'matter', 'scale', 'renderer', 'facebook']);
  function engineState() {
    const g = games[0];
    if (!g || !g.scene || !g.scene.getScenes) return null;
    const scenes = g.scene.getScenes(true).map(s => {
      const fields = {};
      for (const k of Object.keys(s)) {
        if (k.startsWith('_') || ENGINE_KEYS.has(k)) continue;
        const v = s[k];
        const t = typeof v;
        if (t === 'number' || t === 'boolean' || t === 'string') fields[k] = plain(v, 0);
        else if (Array.isArray(v)) {
          const first = v.find(x => x && typeof x === 'object');
          fields[k] = 'array(' + v.length + ')' + (first ? ' of {' + Object.keys(first).filter(x => !x.startsWith('_')).slice(0, 15).join(', ') + '}' : '');
        } else if (v && t === 'object' && typeof v.x === 'number' && typeof v.y === 'number') {
          fields[k] = 'object at (' + Math.round(v.x) + ', ' + Math.round(v.y) + ')';
        }
      }
      return { key: s.scene.key, fields };
    });
    let registry = {};
    try { registry = plain(g.registry.getAll(), 2) || {}; } catch (e) { }
    return { scenes, registry, size: [g.scale && g.scale.width, g.scale && g.scale.height] };
  }

  function visibleText() {
    return document.body ? document.body.innerText.replace(/\s+/g, ' ').trim().slice(0, 600) : '';
  }

  function buttons() {
    const out = [];
    for (const el of document.querySelectorAll('button, a[href], [role=button], [onclick], input[type=button]')) {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) out.push({ text: (el.innerText || el.value || '').trim().slice(0, 40), x: r.x + r.width / 2, y: r.y + r.height / 2 });
    }
    return out.slice(0, 20);
  }

  function canvasRect() {
    let best = null;
    for (const c of document.querySelectorAll('canvas')) {
      const r = c.getBoundingClientRect();
      if (!best || r.width * r.height > best.w * best.h) best = { x: r.x, y: r.y, w: r.width, h: r.height };
    }
    return best;
  }

  const api = {
    pause() { paused = true; },
    resume() { paused = false; },
    step(n) {
      n = Math.max(0, Math.floor(n || 0));
      if (!n) return Promise.resolve(frame);
      pending += n;
      return new Promise(resolve => waiters.push(resolve));
    },
    frame: () => frame,
    errors: since => errors.filter(e => e.frame >= (since || 0)),
    engine: () => (games.length ? (games[0].scene && games[0].loop ? 'phaser' : 'hooked') : (window.me && window.me.state ? 'melonjs' : 'unknown')),
    read, scanGlobals, visibleText, buttons, canvasRect, engineState,
  };
  Object.defineProperty(window, '__playtest', { value: api });
})();

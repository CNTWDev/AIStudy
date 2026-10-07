/* 乐园小游戏的共用引擎（所有游戏都用，不依赖任何库）。
   Play.stage    画布：固定逻辑高度、宽度跟屏幕比例走，按设备像素比画得清楚
   Play.loop     主循环：固定步长更新、切到后台自动暂停
   Play.FX       特效：粒子、飘字、冲击波、震屏
   Play.sfx      音效：用 Web Audio 现场合成，不用下载素材；可以静音（记在本机）
   Play.music    背景音乐：几小节轻快的循环
   Play.scene    背景：天空、云、远山、草地、星星
   Play.input    键盘 + 触屏按钮
   Play.ui       倒计时、提示条
   Play.ease     缓动函数
   出题、判分、经验值都在服务器（app/arena/），见 quiz.js 和 match.js。 */
(function () {
  const Play = window.Play = {};
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rand = (a, b) => a + Math.random() * (b - a);
  Play.clamp = clamp; Play.rand = rand;
  Play.lerp = (a, b, t) => a + (b - a) * t;
  Play.pick = arr => arr[Math.floor(Math.random() * arr.length)];

  // ---------------------------------------------------------------- 缓动
  Play.ease = {
    linear: t => t,
    outCubic: t => 1 - Math.pow(1 - t, 3),
    inCubic: t => t * t * t,
    inOutSine: t => -(Math.cos(Math.PI * t) - 1) / 2,
    outBack: t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
    outElastic: t => t === 0 || t === 1 ? t : Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * (2 * Math.PI) / 3) + 1,
  };

  // ---------------------------------------------------------------- 画布
  /* 逻辑坐标：高度固定 H，宽度 W 跟着容器比例变（minW–maxW 之间），游戏代码只用逻辑坐标。 */
  Play.stage = function (canvas, opts) {
    const o = Object.assign({height: 450, minW: 640, maxW: 1400}, opts || {});
    const ctx = canvas.getContext('2d');
    const st = {canvas, ctx, H: o.height, W: o.minW, dpr: 1, onResize: null};
    st.resize = function () {
      const r = canvas.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const W = Math.round(clamp(o.height * r.width / r.height, o.minW, o.maxW));
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const scale = r.height / o.height * dpr;
      const old = st.W;
      st.W = W; st.dpr = dpr; st.scale = scale;
      canvas.width = Math.round(W * scale); canvas.height = Math.round(o.height * scale);
      if (old !== W && st.onResize) st.onResize(W, old);
    };
    st.begin = function () {   // 每帧开始：清屏并换成逻辑坐标
      ctx.setTransform(st.scale, 0, 0, st.scale, 0, 0);
      ctx.clearRect(0, 0, st.W, st.H);
    };
    st.resize();
    window.addEventListener('resize', st.resize);
    if (window.ResizeObserver) new ResizeObserver(() => st.resize()).observe(canvas);
    return st;
  };

  // ---------------------------------------------------------------- 主循环
  Play.loop = function (update, render) {
    const STEP = 1 / 60;
    let acc = 0, last = 0, raf = 0, running = false, paused = false;
    const frame = ts => {
      if (!running) return;
      const dt = Math.min(0.1, (ts - last) / 1000 || 0); last = ts;
      if (!paused) {
        acc += dt;
        let n = 0;
        while (acc >= STEP && n < 6) { update(STEP); acc -= STEP; n++; }
        if (n === 6) acc = 0;
      }
      render(paused);
      raf = requestAnimationFrame(frame);
    };
    const api = {
      start() { if (running) return; running = true; paused = false; last = performance.now(); acc = 0; raf = requestAnimationFrame(frame); },
      stop() { running = false; cancelAnimationFrame(raf); },
      pause() { paused = true; }, resume() { if (paused) { paused = false; last = performance.now(); } },
      get paused() { return paused; }, get running() { return running; },
    };
    document.addEventListener('visibilitychange', () => { if (document.hidden && running && api.onHide) api.onHide(); });
    return api;
  };

  // ---------------------------------------------------------------- 特效
  Play.FX = class {
    constructor() { this.parts = []; this.texts = []; this.rings = []; this.shakeT = 0; this.shakeA = 0; this.flashT = 0; this.flashC = '#fff'; }
    burst(x, y, o) {
      o = Object.assign({n: 14, colors: ['#ffd23f', '#ff8fab', '#7bdff2', '#b8f2a6'], speed: 260, life: 0.8, size: 6, shape: 'circle', grav: 600, spread: Math.PI * 2, dir: -Math.PI / 2}, o || {});
      for (let i = 0; i < o.n; i++) {
        const a = o.dir + (Math.random() - 0.5) * o.spread, sp = o.speed * rand(0.4, 1);
        this.parts.push({x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, life: o.life * rand(0.6, 1), max: o.life, size: o.size * rand(0.6, 1.3),
                         color: Play.pick(o.colors), shape: o.shape, grav: o.grav, rot: rand(0, 6), vr: rand(-8, 8)});
      }
    }
    text(x, y, str, o) {
      o = Object.assign({color: '#ff7a00', size: 28, life: 1, rise: 70, stroke: '#fff'}, o || {});
      this.texts.push(Object.assign({x, y, str, t: 0}, o));
    }
    ring(x, y, o) { this.rings.push(Object.assign({x, y, r: 6, grow: 520, life: 0.45, max: 0.45, color: '#fff', width: 6}, o || {})); }
    shake(a, t) { this.shakeA = Math.max(this.shakeA, a); this.shakeT = Math.max(this.shakeT, t || 0.25); }
    flash(c, t) { this.flashC = c || '#fff'; this.flashT = t || 0.12; }
    offset() { return this.shakeT > 0 ? [rand(-1, 1) * this.shakeA, rand(-1, 1) * this.shakeA] : [0, 0]; }
    update(dt) {
      for (const p of this.parts) { p.life -= dt; p.vy += p.grav * dt; p.x += p.vx * dt; p.y += p.vy * dt; p.vx *= 0.99; p.rot += p.vr * dt; }
      this.parts = this.parts.filter(p => p.life > 0);
      for (const t of this.texts) t.t += dt;
      this.texts = this.texts.filter(t => t.t < t.life);
      for (const r of this.rings) { r.life -= dt; r.r += r.grow * dt; }
      this.rings = this.rings.filter(r => r.life > 0);
      this.shakeT = Math.max(0, this.shakeT - dt); if (!this.shakeT) this.shakeA = 0;
      this.flashT = Math.max(0, this.flashT - dt);
    }
    draw(ctx) {
      for (const r of this.rings) {
        ctx.globalAlpha = clamp(r.life / r.max, 0, 1); ctx.strokeStyle = r.color; ctx.lineWidth = r.width;
        ctx.beginPath(); ctx.arc(r.x, r.y, r.r, 0, Math.PI * 2); ctx.stroke();
      }
      for (const p of this.parts) {
        ctx.globalAlpha = clamp(p.life / p.max * 1.5, 0, 1); ctx.fillStyle = p.color;
        if (p.shape === 'star') { Play.starPath(ctx, p.x, p.y, p.size, p.rot); ctx.fill(); }
        else if (p.shape === 'confetti') { ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2); ctx.restore(); }
        else { ctx.beginPath(); ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2); ctx.fill(); }
      }
      ctx.globalAlpha = 1;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      for (const t of this.texts) {
        const k = t.t / t.life, pop = t.t < 0.15 ? Play.ease.outBack(t.t / 0.15) : 1;
        ctx.globalAlpha = k > 0.7 ? (1 - k) / 0.3 : 1;
        ctx.font = `900 ${Math.round(t.size * pop)}px "Baloo 2", "PingFang SC", system-ui, sans-serif`;
        const y = t.y - t.rise * Play.ease.outCubic(k);
        if (t.stroke) { ctx.lineWidth = 6; ctx.strokeStyle = t.stroke; ctx.lineJoin = 'round'; ctx.strokeText(t.str, t.x, y); }
        ctx.fillStyle = t.color; ctx.fillText(t.str, t.x, y);
      }
      ctx.globalAlpha = 1;
    }
    drawFlash(ctx, W, H) {
      if (this.flashT > 0) { ctx.globalAlpha = this.flashT * 3; ctx.fillStyle = this.flashC; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1; }
    }
  };

  Play.starPath = function (ctx, x, y, r, rot) {
    ctx.beginPath();
    for (let i = 0; i < 10; i++) {
      const a = (rot || 0) - Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * 0.48 : r;
      ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr);
    }
    ctx.closePath();
  };
  Play.roundRect = function (ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  };

  // ---------------------------------------------------------------- 音效（Web Audio 合成）
  const store = {get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v === '1'; } catch (e) { return d; } },
                 set(k, v) { try { localStorage.setItem(k, v ? '1' : '0'); } catch (e) { /* 隐私模式 */ } }};
  let AC = null, master = null;
  function ac() {
    if (!AC) {
      const C = window.AudioContext || window.webkitAudioContext;
      if (!C) return null;
      AC = new C(); master = AC.createGain(); master.gain.value = 0.5; master.connect(AC.destination);
    }
    if (AC.state === 'suspended') AC.resume();
    return AC;
  }
  function tone(f, t0, dur, o) {
    const a = ac(); if (!a) return;
    o = Object.assign({type: 'sine', vol: 0.25, slide: 0, attack: 0.005}, o || {});
    const osc = a.createOscillator(), g = a.createGain();
    osc.type = o.type; osc.frequency.setValueAtTime(f, t0);
    if (o.slide) osc.frequency.exponentialRampToValueAtTime(Math.max(30, f + o.slide), t0 + dur);
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(o.vol, t0 + o.attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g); g.connect(o.dest || master); osc.start(t0); osc.stop(t0 + dur + 0.02);
  }
  function noise(t0, dur, o) {
    const a = ac(); if (!a) return;
    o = Object.assign({vol: 0.25, freq: 1200, q: 1}, o || {});
    const len = Math.ceil(a.sampleRate * dur), buf = a.createBuffer(1, len, a.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
    src.buffer = buf; f.type = 'bandpass'; f.frequency.value = o.freq; f.Q.value = o.q; g.gain.value = o.vol;
    src.connect(f); f.connect(g); g.connect(master); src.start(t0);
  }
  const SFX = {
    click: t => tone(660, t, 0.06, {type: 'triangle', vol: 0.15}),
    pop: t => tone(520, t, 0.09, {type: 'sine', vol: 0.22, slide: 400}),
    coin: t => { tone(988, t, 0.08, {type: 'square', vol: 0.12}); tone(1319, t + 0.07, 0.16, {type: 'square', vol: 0.12}); },
    right: t => [523, 659, 784, 1047].forEach((f, i) => tone(f, t + i * 0.07, 0.14, {type: 'triangle', vol: 0.2})),
    crit: t => [523, 784, 1047, 1319, 1568].forEach((f, i) => tone(f, t + i * 0.06, 0.18, {type: 'square', vol: 0.12})),
    wrong: t => { tone(330, t, 0.16, {type: 'sawtooth', vol: 0.12, slide: -120}); tone(247, t + 0.15, 0.22, {type: 'sawtooth', vol: 0.12, slide: -80}); },
    freeze: t => { for (let i = 0; i < 5; i++) tone(1800 + i * 300, t + i * 0.04, 0.12, {type: 'sine', vol: 0.06}); },
    jump: t => tone(300, t, 0.16, {type: 'square', vol: 0.1, slide: 500}),
    swing: t => noise(t, 0.12, {vol: 0.18, freq: 2400, q: 0.7}),
    hit: t => { noise(t, 0.1, {vol: 0.35, freq: 900, q: 0.8}); tone(160, t, 0.12, {type: 'square', vol: 0.15, slide: -80}); },
    hurt: t => tone(220, t, 0.18, {type: 'sawtooth', vol: 0.12, slide: -100}),
    power: t => { tone(220, t, 0.5, {type: 'sawtooth', vol: 0.1, slide: 880}); noise(t + 0.35, 0.3, {vol: 0.3, freq: 600}); },
    laser: t => tone(1400, t, 0.18, {type: 'square', vol: 0.08, slide: -1100}),
    shield: t => tone(600, t, 0.2, {type: 'sine', vol: 0.15, slide: 300}),
    buy: t => [784, 988, 1175].forEach((f, i) => tone(f, t + i * 0.05, 0.1, {type: 'triangle', vol: 0.15})),
    whoosh: t => noise(t, 0.3, {vol: 0.2, freq: 800, q: 0.5}),
    boom: t => { noise(t, 0.5, {vol: 0.45, freq: 300, q: 0.6}); tone(90, t, 0.4, {type: 'sine', vol: 0.3, slide: -50}); },
    star: t => [1047, 1319, 1568].forEach((f, i) => tone(f, t + i * 0.05, 0.25, {type: 'sine', vol: 0.12})),
    go: t => { tone(523, t, 0.12, {type: 'square', vol: 0.12}); tone(1047, t + 0.12, 0.3, {type: 'square', vol: 0.14}); },
    tick: t => tone(880, t, 0.05, {type: 'square', vol: 0.08}),
    win: t => [523, 659, 784, 1047, 784, 1047, 1319].forEach((f, i) => tone(f, t + i * 0.11, 0.2, {type: 'triangle', vol: 0.2})),
    lose: t => [392, 349, 330, 262].forEach((f, i) => tone(f, t + i * 0.18, 0.3, {type: 'triangle', vol: 0.16})),
    sticker: t => [784, 1047, 1319, 1568, 2093].forEach((f, i) => tone(f, t + i * 0.08, 0.3, {type: 'sine', vol: 0.12})),
  };
  Play.sfx = {
    on: store.get('play.sfx', true),
    play(name, delay) { if (!this.on || !SFX[name]) return; const a = ac(); if (a) SFX[name](a.currentTime + (delay || 0)); },
    toggle() { this.on = !this.on; store.set('play.sfx', this.on); return this.on; },
    unlock() { ac(); },
  };

  // ---------------------------------------------------------------- 背景音乐（简单的循环旋律）
  Play.music = {
    on: store.get('play.music', true), timer: 0, step: 0, gain: null,
    // C 大调五声音阶上的小旋律 + 低音，每拍一个音
    melody: [0, 2, 4, 7, 9, 7, 4, 2, 0, 4, 7, 12, 9, 7, 4, -1],
    bass: [0, 0, 5, 5, 7, 7, 5, 5],
    start(tempo) {
      if (!this.on || this.timer) return;
      const a = ac(); if (!a) return;
      if (!this.gain) { this.gain = a.createGain(); this.gain.gain.value = 0.35; this.gain.connect(master); }
      const beat = 60 / (tempo || 132);
      const scale = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16];
      this.timer = setInterval(() => {
        if (!AC) return;
        const t = AC.currentTime + 0.05, i = this.step++;
        const m = this.melody[i % this.melody.length];
        if (m >= 0) tone(523.25 * Math.pow(2, m / 12), t, beat * 0.8, {type: 'triangle', vol: 0.06, dest: this.gain});
        if (i % 2 === 0) tone(130.81 * Math.pow(2, scale[this.bass[(i / 2) % this.bass.length]] / 12), t, beat * 1.6, {type: 'sine', vol: 0.09, dest: this.gain});
      }, beat * 1000);
    },
    stop() { clearInterval(this.timer); this.timer = 0; },
    toggle(tempo) { this.on = !this.on; store.set('play.music', this.on); if (this.on) this.start(tempo); else this.stop(); return this.on; },
  };

  // ---------------------------------------------------------------- 背景
  Play.scene = {
    themes: {
      day: {sky: ['#8fd3ff', '#dff4ff'], hill: ['#9be3a5', '#6cc88a'], ground: '#7cd27a', soil: '#c99a6b', cloud: '#fff'},
      sunset: {sky: ['#ffb38a', '#ffe3c2'], hill: ['#f6a6b2', '#d98aa0'], ground: '#8fcf7a', soil: '#b9875c', cloud: '#fff4ea'},
      night: {sky: ['#2b2d6e', '#5b4b9a'], hill: ['#3b3f8f', '#2f3275'], ground: '#4a5bb0', soil: '#33307a', cloud: '#8f8fd6', stars: true},
      candy: {sky: ['#ffd6f0', '#e8f0ff'], hill: ['#c7b8ff', '#a99bf5'], ground: '#ffcfe3', soil: '#e9a7c6', cloud: '#fff'},
    },
    clouds: null,
    sky(ctx, W, H, theme) {
      const th = this.themes[theme] || this.themes.day;
      const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, th.sky[0]); g.addColorStop(1, th.sky[1]);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      if (th.stars) {
        ctx.fillStyle = '#fff';
        for (let i = 0; i < 40; i++) {
          const x = (i * 137.5) % W, y = (i * 71.3) % (H * 0.55), tw = 0.5 + 0.5 * Math.sin(performance.now() / 600 + i);
          ctx.globalAlpha = 0.4 + 0.6 * tw; ctx.fillRect(x, y, 2, 2);
        }
        ctx.globalAlpha = 1;
      }
    },
    cloud(ctx, x, y, s, color) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, 22 * s, 0, Math.PI * 2); ctx.arc(x + 26 * s, y - 10 * s, 28 * s, 0, Math.PI * 2);
      ctx.arc(x + 56 * s, y, 22 * s, 0, Math.PI * 2); ctx.arc(x + 28 * s, y + 8 * s, 22 * s, 0, Math.PI * 2);
      ctx.fill();
    },
    drawClouds(ctx, W, theme, t, speed) {
      const th = this.themes[theme] || this.themes.day;
      if (!this.clouds) this.clouds = Array.from({length: 6}, (_, i) => ({x: i * 260 + rand(0, 120), y: rand(40, 150), s: rand(0.6, 1.2), v: rand(6, 16)}));
      ctx.globalAlpha = 0.9;
      for (const c of this.clouds) {
        const x = ((c.x - t * (c.v + (speed || 0) * 0.1)) % (W + 300) + W + 300) % (W + 300) - 150;
        this.cloud(ctx, x, c.y, c.s, th.cloud);
      }
      ctx.globalAlpha = 1;
    },
    hills(ctx, W, H, ground, theme, scroll) {
      const th = this.themes[theme] || this.themes.day;
      const layer = (color, amp, base, k, off) => {
        ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(0, H);
        for (let x = 0; x <= W + 20; x += 20) {
          const xx = x + (scroll || 0) * k + off;
          ctx.lineTo(x, ground - base - amp * (0.6 * Math.sin(xx / 140) + 0.4 * Math.sin(xx / 57 + 1)));
        }
        ctx.lineTo(W, H); ctx.fill();
      };
      layer(th.hill[0], 34, 70, 0.2, 0);
      layer(th.hill[1], 22, 30, 0.45, 300);
    },
    ground(ctx, W, H, y, theme, scroll) {
      const th = this.themes[theme] || this.themes.day;
      ctx.fillStyle = th.soil; ctx.fillRect(0, y, W, H - y);
      ctx.fillStyle = th.ground; ctx.fillRect(0, y - 4, W, 18);
      ctx.fillStyle = 'rgba(255,255,255,.25)';
      const s = ((scroll || 0) % 48 + 48) % 48;
      for (let x = -s; x < W; x += 48) { ctx.beginPath(); ctx.arc(x + 12, y + 30, 4, 0, Math.PI * 2); ctx.arc(x + 36, y + 46, 3, 0, Math.PI * 2); ctx.fill(); }
      // 草叶
      ctx.fillStyle = th.ground;
      for (let x = -s; x < W; x += 24) { ctx.beginPath(); ctx.moveTo(x, y - 2); ctx.lineTo(x + 5, y - 12); ctx.lineTo(x + 10, y - 2); ctx.fill(); }
    },
  };

  // ---------------------------------------------------------------- 输入
  Play.input = {
    keys: {}, pressed: {},
    init(map) {   // map: {left: ['ArrowLeft','a'], ...}
      this.map = {};
      for (const [act, ks] of Object.entries(map)) for (const k of ks) this.map[k.toLowerCase()] = act;
      window.addEventListener('keydown', e => {
        if (this.blocked) return;
        const act = this.map[e.key.toLowerCase()]; if (!act) return;
        if (!this.keys[act]) this.pressed[act] = true;
        this.keys[act] = true; e.preventDefault();
      });
      window.addEventListener('keyup', e => { const act = this.map[e.key.toLowerCase()]; if (act) this.keys[act] = false; });
      window.addEventListener('blur', () => { this.keys = {}; });
    },
    hold(el, act) {   // 触屏按钮：按住 = 一直按着这个键
      if (!el) return;
      const on = e => { e.preventDefault(); if (this.blocked) return; if (!this.keys[act]) this.pressed[act] = true; this.keys[act] = true; el.classList.add('down'); Play.sfx.unlock(); };
      const off = e => { e.preventDefault(); this.keys[act] = false; el.classList.remove('down'); };
      el.addEventListener('pointerdown', on); el.addEventListener('pointerup', off);
      el.addEventListener('pointerleave', off); el.addEventListener('pointercancel', off);
      el.addEventListener('contextmenu', e => e.preventDefault());
    },
    take(act) { const v = !!this.pressed[act]; this.pressed[act] = false; return v; },
    clear() { this.keys = {}; this.pressed = {}; },
  };

  // ---------------------------------------------------------------- 界面小部件
  Play.ui = {
    countdown(el, onDone) {   // 3 · 2 · 1 · 开始！
      const seq = ['3', '2', '1', '开始！'];
      let i = 0;
      el.hidden = false;
      const next = () => {
        if (i >= seq.length) { el.hidden = true; onDone && onDone(); return; }
        el.innerHTML = `<span class="pl-cd-n">${seq[i]}</span>`;
        Play.sfx.play(i === 3 ? 'go' : 'tick');
        i++; setTimeout(next, i === 4 ? 600 : 700);
      };
      next();
    },
    toast(el, text, kind) {
      el.textContent = text; el.className = 'pl-toast on ' + (kind || '');
      clearTimeout(el._t); el._t = setTimeout(() => { el.className = 'pl-toast'; }, 1800);
    },
    soundButtons(root, tempo) {   // 右上角 🔊 / 🎵 开关
      const s = root.querySelector('[data-sfx]'), m = root.querySelector('[data-music]');
      const paint = () => { if (s) s.textContent = Play.sfx.on ? '🔊' : '🔇'; if (m) { m.textContent = '🎵'; m.classList.toggle('off', !Play.music.on); } };
      if (s) s.onclick = () => { Play.sfx.toggle(); paint(); };
      if (m) m.onclick = () => { Play.music.toggle(tempo); paint(); };
      paint();
    },
  };
})();

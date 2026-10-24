/* 手感：答对一题的即时反馈（声音 + 震动 + 一下打击感 + 飞进计数的 +1），学习页和乐园游戏共用这一份
   （docs/DESIGN.md 5.52，讨论稿 docs/答对即时激励-讨论稿.md）。
   Juice.sfx      音效：用 Web Audio 现场合成，不用下载素材；全站一个开关（记在本机），游戏里的 🔊 也是它
   Juice.buzz     震动：只有安卓的浏览器能震（iPhone / iPad 的网页不支持，电脑没有），震不了就什么都不做
   Juice.answer   答完一题：对了「叮咚」，连对一题升一个音（五声音阶，升到第 8 个封顶），3/5/10 连对一个小和弦，
                  难题答对一个更亮的音；答错一个低而柔的「嗒」，不震
   Juice.done     一项做完：过关的和弦 + 长一点的震动
   Juice.thunk / Juice.plus   按下去沉一下；「+1」从题目飞到「这一项 N 道」上
   三种人：kid 小学（音色可爱）、teen 初中以上（干净、短）、adult 成人（声音默认关）。晚上 9 点到早上 7 点音量减半。 */
(function () {
  const J = window.Juice = {};
  const body = () => document.body || {dataset: {}};
  J.who = () => body().dataset.tone !== 'kid' ? 'adult' : body().dataset.teen ? 'teen' : 'kid';
  J.still = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------------------------------------------------------------- 开关（以前游戏用 play.sfx，接着认）
  const store = {get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
                 set(k, v) { try { localStorage.setItem(k, v ? '1' : '0'); } catch (e) { /* 隐私模式 */ } }};
  J.store = store;
  function soundOn() {
    const v = store.get('sfx') ?? store.get('play.sfx');
    return v === null ? J.who() !== 'adult' : v === '1';
  }

  // ---------------------------------------------------------------- 合成器
  let AC = null, master = null;
  const night = () => { const h = new Date().getHours(); return h >= 21 || h < 7; };
  function ac() {
    if (!AC) {
      const C = window.AudioContext || window.webkitAudioContext;
      if (!C) return null;
      AC = new C(); master = AC.createGain(); master.connect(AC.destination);
    }
    if (AC.state === 'suspended') AC.resume();
    master.gain.value = night() ? 0.25 : 0.5;
    return AC;
  }
  // 主题的音色（DESIGN 5.45）：旋律类的音（三角波）换成主题的波形，整体按主题升降；噪声类（打击、爆炸）不变
  function tone(f, t0, dur, o) {
    const a = ac(); if (!a) return;
    const LOOK = window.LOOK || {};
    o = Object.assign({type: 'sine', vol: 0.25, slide: 0, attack: 0.005}, o || {});
    if (LOOK.wave && o.type === 'triangle') { o.type = LOOK.wave; if (LOOK.wave === 'square') o.vol *= 0.6; }  // 方波听着更响，压一点
    f *= LOOK.pitch || 1;
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
  J.ac = ac; J.tone = tone; J.bus = () => (ac(), master);

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

    // ---- 学习页答题（Juice.answer / done 用）。n = 连对第几题
    tap: t => tone(1250, t, 0.03, {type: 'sine', vol: 0.05}),
    ding: (t, n) => {
      const i = Math.min(7, Math.max(0, n - 1)), f1 = STEP[i], f2 = STEP[i + 2];
      if (J.who() === 'kid') { tone(f1, t, 0.12, {type: 'triangle', vol: 0.18}); tone(f2, t + 0.07, 0.2, {type: 'triangle', vol: 0.18}); }
      else { tone(f1 * 2, t, 0.07, {type: 'sine', vol: 0.12}); tone(f2 * 2, t + 0.05, 0.12, {type: 'sine', vol: 0.1}); }
    },
    chord: (t, n) => {
      const i = Math.min(7, Math.max(0, n - 1)), w = J.who() === 'kid' ? 'triangle' : 'sine', k = J.who() === 'kid' ? 1 : 2;
      [0, 2, 4].forEach((d, j) => tone(STEP[Math.min(9, i + d)] * k, t + j * 0.05, 0.28, {type: w, vol: 0.13}));
    },
    nope: t => tone(J.who() === 'kid' ? 247 : 294, t, 0.12, {type: 'sine', vol: 0.1, slide: -30}),
    done: t => [523, 659, 784, 1047, 1319].forEach((f, i) => tone(J.who() === 'kid' ? f : f * 2, t + i * 0.09, 0.22, {type: J.who() === 'kid' ? 'triangle' : 'sine', vol: 0.16})),
  };
  // C 大调五声音阶，从中音 do 往上 10 个：怎么连着升都好听
  const STEP = [523.25, 587.33, 659.25, 783.99, 880, 1046.5, 1174.66, 1318.51, 1567.98, 1760];

  J.sfx = {
    get on() { return soundOn(); },
    play(name, delay, n) { if (!soundOn() || !SFX[name]) return; const a = ac(); if (a) SFX[name](a.currentTime + (delay || 0), n || 1); },
    toggle() { const v = !soundOn(); store.set('sfx', v); return v; },
    unlock() { if (soundOn()) ac(); },
  };

  // ---------------------------------------------------------------- 震动：跟声音同一个开关
  const BUZZ = {tap: 8, right: 15, combo: [20, 40, 20], hard: [25, 30, 40], done: [30, 50, 30, 50, 80]};
  J.buzz = kind => { try { if (soundOn() && navigator.vibrate) navigator.vibrate(BUZZ[kind] || 0); } catch (e) { /* 不支持 */ } };

  // ---------------------------------------------------------------- 画面：打击感、+1
  J.thunk = el => {
    if (!el || J.still()) return;
    el.classList.remove('j-thunk'); void el.offsetWidth; el.classList.add('j-thunk');
    setTimeout(() => el.classList.remove('j-thunk'), 450);
  };
  J.bump = el => { if (!el) return; el.classList.remove('j-bump'); void el.offsetWidth; el.classList.add('j-bump'); };
  // 「+1」从题目飞到计数上（没有计数就往上飘一下）；落下时计数跳一下
  J.plus = (from, to) => {
    if (!from) return;
    if (J.still()) return J.bump(to);
    const a = from.getBoundingClientRect(), p = document.createElement('span');
    p.className = 'j-plus'; p.textContent = '+1';
    p.style.left = (a.left + a.width / 2) + 'px'; p.style.top = (a.top + Math.min(a.height, 80) / 2) + 'px';
    document.body.appendChild(p);
    const b = to && to.getBoundingClientRect(), seen = b && b.bottom > 0 && b.top < innerHeight;
    const dx = seen ? b.left + b.width / 2 - (a.left + a.width / 2) : 0, dy = seen ? b.top + b.height / 2 - (a.top + Math.min(a.height, 80) / 2) : -60;
    requestAnimationFrame(() => { p.style.transform = `translate(${dx}px, ${dy}px) scale(${seen ? 0.7 : 1})`; p.style.opacity = seen ? '0.9' : '0'; });
    setTimeout(() => { p.remove(); if (seen) J.bump(to); }, 480);
  };

  // ---------------------------------------------------------------- 答完一题、一项做完
  J.answer = (ok, o) => {
    o = o || {};
    if (!ok) { J.sfx.play('nope'); return; }
    const n = o.streak || 1, big = n === 3 || n === 5 || n % 10 === 0;
    J.thunk(o.el);
    if (o.hard) { J.sfx.play('crit'); J.buzz('hard'); }
    else if (big) { J.sfx.play('chord', 0, n); J.buzz('combo'); }
    else { J.sfx.play('ding', 0, n); J.buzz('right'); }
  };
  J.press = () => { J.sfx.play('tap'); J.buzz('tap'); };
  J.done = () => { J.sfx.play('done'); J.buzz('done'); };

  // 浏览器要先有一次点按才能出声：第一次按下时顺手解锁
  addEventListener('pointerdown', () => J.sfx.unlock(), {once: true, capture: true});
  addEventListener('keydown', () => J.sfx.unlock(), {once: true, capture: true});
})();

/* 往上跳：小蜜蜂一直往上弹，← →（鼠标跟着走、手指左右拖也行）控制落在哪块板上，背景一直往下沉，掉出屏幕底边就倒下，最快到塔顶就赢。
   关卡（app/arena/games/jump.py）：单位一屏高 = 1（H）；plats [x 天空柱宽的几分之几, y 离地多高, 种类]，
     n 普通、s 弹簧、m 左右移动、x 易碎（踩一次就碎）、b 一闪一闪（亮的时候才踩得住）；gates 题板的高度；honey 蜂蜜；wind 风 [y0, y1, 方向]。
   手感：重力比地球「轻」（一跳 0.22 H、0.55 秒到顶，在空中飘得久、好控制），弹簧 0.6 H，题板上踩对的超级弹簧 1.25 H。
     板子只在往下落时接得住（往上穿过去没事），左右穿屏。
   背景下沉：镜头按这一关的 scroll 匀速往上走，人跳到中线以上镜头立刻跟上（弹簧时背景哗地沉下去）；镜头只往上走。
     开局、复活后、题板进了屏幕（到答完）镜头停住不追人。
   题板：一整条软云（掉不下去），软云上方 RISE 处是 2～4 块答案板（按选项个数），再往上一大段云带只有超级弹簧弹得过去。
     从下面跳上来穿过软云就停在云上，小蜜蜂坐着不弹，慢慢想：← → 换答案（小蜜蜂在云上走过去）、空格 / ↑ / 回车跳上去；
     也可以直接点答案牌。第一次落在哪块板上就是答案（记一次作答）：对了板子底下弹出超级弹簧，一下飞过云带，路上一串蜂蜜；
     错了板子软软塌下去，掉回云上，那块牌子变灰，再选；连错两次，对的那块亮起来、题上写出答案，踩上去是普通弹簧，刚好够过云带。
     想题的前 GATE_FREE 秒不算进用时。
   掉出屏幕底边：倒下，外壳盖上题目，和所有游戏一样答对 1 道就起来（REVIVE_NEED），起来站在一朵救援云上，镜头停 2 秒。
   几个人一起（最多 4 个）：同一座塔各跳各的（题也是自己的），每隔一会儿告诉大家我在哪（jp），别人画成半透明的小蜜蜂（互不碰撞），
   右边的高度条上画出每个人；到顶了告诉大家我的用时（jp done），谁先到顶谁第一；大家都到了由开房间的人报过关。 */
(function () {
  const INK = (window.LOOK && window.LOOK.ink) || '#2b2340';
  const FONT = '"Baloo 2", "Fredoka", "PingFang SC", system-ui, sans-serif';
  const G = 1.45, V_JUMP = 0.8, V_SPRING = Math.sqrt(2 * G * 0.6), V_SUPER = Math.sqrt(2 * G * 1.25), V_CAP = Math.sqrt(2 * G * 0.08);
  const VX = 0.9, AX = 7;                                   // 左右最快（天空柱宽 / 秒）、加速度（松手很快停）
  const RISE = 0.16, BAND = 0.70, GATE_FREE = 8, WAIT0 = 2, WAIT_REVIVE = 2, M_PER_H = 10;
  const BEE = 0.03;                                         // 小蜜蜂半径（H）
  const ROLE = {sun: ['#ffcf2e', '#f0a800'], berry: ['#ff7a8a', '#e24a5f'], mint: ['#6fe0b0', '#2fb886'], sky: ['#7cc4ff', '#3a8fe0']};
  const PAL = {   // 每个世界的天空和板子（第一个世界的天空跟孩子的主题走：Play.scene.themes.day）
    day: {sky: null, warm: '#fff5dc', top: '#fffaf0', side: '#ffc21a', side2: '#e8a300', cloud: '#ffffff', cs: '#d8ecfa', hill: ['#a8dc8e', '#86c96f'], far: '#bfe0f5'},
    candy: {sky: ['#ff9fd0', '#ffe6f3'], warm: '#fff6e6', top: '#fffaf6', side: '#7fdcc0', side2: '#4fbf9f', cloud: '#ffffff', cs: '#ffd9ec', hill: ['#ffb3d6', '#f592c2'], far: '#ffd3ea'},
    sunset: {sky: ['#ff8a5c', '#ffd59e'], warm: '#fff0cf', top: '#fff3e2', side: '#ff9152', side2: '#e06a2e', cloud: '#ffe9d6', cs: '#f6c3a3', hill: ['#b46aa0', '#8e4f86'], far: '#f3a07a'},
    night: {sky: ['#141a4a', '#4b3f9a'], warm: '#7a5fbf', top: '#e9f1ff', side: '#7aa7ff', side2: '#4f7ee6', cloud: '#5b56a8', cs: '#3f3a8a', hill: ['#2a2766', '#1d1b4f'], far: '#3a3a86', stars: true},
    lava: {sky: ['#2a1414', '#8a2c18'], warm: '#ff7a2a', top: '#ffe3c2', side: '#5a4a44', side2: '#3e322e', cloud: '#7a3a2c', cs: '#5a2a20', hill: ['#3a1610', '#24100c'], far: '#5a2018', stars: true},
  };

  let sh = null, stage = null, ctx = null, c = null, hud = null, hudEl = null;
  const fx = new Play.FX();
  let S = null, clock = 0, drag = null, mates = {}, sendT = 0;
  const touchy = matchMedia('(pointer: coarse)').matches;
  const mod = (a, m) => ((a % m) + m) % m;
  const wrapD = d => mod(d + 0.5, 1) - 0.5;                 // 左右穿屏时两点之间最近的距离

  // ------------------------------------------------------------ 关卡
  function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  const DEMO = {height: 3, width: 0.24, scroll: 0, move: {amp: 0, speed: 0}, gates: [], honey: [[0.3, 0.5], [0.7, 0.62]], wind: [],
                plats: [[0.5, 0.25, 'n'], [0.22, 0.42, 's'], [0.78, 0.55, 'n']], seed: 3};
  function fresh(lv, role) {
    lv = lv || DEMO;
    const r = rng(lv.seed || 1);
    const plats = lv.plats.map(([x, y, k], i) => ({x0: x, x, y, k, i, ph: r() * 6.28, broke: 0, sp: 0}));
    const gates = (lv.gates || []).map(y => ({y, q: null, opts: [], passed: false, mist: Array.from({length: 26}, () => [r(), r(), r()])}));
    return {mode: 'preview', lv, role: role || 'sun', plats, gates, honey: (lv.honey || []).map(([x, y]) => ({x, y, got: false})),
            x: 0.5, y: 0, vx: 0, vy: V_JUMP, cam: -0.12, hold: WAIT0, aim: null, auto: null, squash: 0, face: 1, spin: 0,
            gate: null, sel: 0, rescue: null, combo: 0, lastY: 0, top: 0,
            honeyN: 0, gatesOk: 0, free: 0, falls: 0, limit: 600, flags: 0, ghostTold: false, sent: false, winT: 0};
  }
  const scene = () => (S.lv.world && S.lv.world.scene) || 'day';
  const pal = () => PAL[scene()] || PAL.day;
  const width = () => S.lv.width || 0.22;
  const platX = p => p.k === 'm' ? mod(p.x0 + (S.lv.move.amp || 0.18) * Math.sin(clock * (S.lv.move.speed || 0.8) + p.ph), 1) : p.x0;
  const blinkOn = p => mod(clock + p.ph, 2.6) < 1.6;
  const solid = p => !p.broke && (p.k !== 'b' || blinkOn(p));

  // ------------------------------------------------------------ 题板
  function optsOf(g, k) {   // k 个选项：一排排开，中间的高一点点（设计稿）
    const w = Math.min(0.27, 0.9 / k);
    return Array.from({length: k}, (_, i) => ({o: i, x: (i + 0.5) / k, y: g.y + RISE + (k === 3 && i === 1 ? 0.015 : 0), w, tried: false, fall: 0}));
  }
  async function loadGate(g) {
    if (g.q || !c) return;
    g.q = {item: null, right: -1, done: false, armed: false, free: 0, busy: false, tried: 0};
    try {
      g.q.item = await sh.gateAsk('jump' + g.y);
      g.opts = optsOf(g, Math.min(4, (g.q.item.options || []).length || 1));
    } catch (e) { g.q.done = true; g.q.right = 0; g.opts = optsOf(g, 1); }
    if (S.gate === g) { pickSel(0); buildQ(); }
  }
  const openOpt = (g, o) => g.q && !o.tried && (!g.q.done || o.o === g.q.right);
  function pickSel(dir) {   // 换答案：往 dir 那边下一个还能选的（0：离小蜜蜂最近的）
    const g = S.gate; if (!g || !g.opts.length) return;
    const open = g.opts.filter(o => openOpt(g, o));
    if (!open.length) return;
    if (!dir) { S.sel = open.reduce((a, o) => Math.abs(o.x - S.x) < Math.abs(a.x - S.x) ? o : a).o; return; }
    const cur = open.findIndex(o => o.o === S.sel), i = cur < 0 ? 0 : mod(cur + dir, open.length);
    S.sel = open[i].o; Play.sfx.play('swing');
  }
  function rest(g) {   // 落到软云上：坐下来想
    S.mode = 'gate'; S.gate = g; S.y = g.y; S.vy = 0; S.vx = 0; S.auto = null; S.aim = null; S.squash = 1; S.combo = 0;
    if (!g.q) loadGate(g);
    if (!g.q.armed) { g.q.armed = true; const it = g.q.item; if (it && it.say && typeof Speak !== 'undefined') Speak.play(it.say, 'en', 'word'); }
    pickSel(0); buildQ(); Play.sfx.play('click');
  }
  function hop() {   // 从软云上跳向选中的答案板
    const g = S.gate; if (!g || !g.q || (!g.q.item && !g.q.done)) return;
    const o = g.opts.find(o => o.o === S.sel);
    if (!o || !openOpt(g, o)) { pickSel(0); return; }
    S.mode = 'play'; S.vy = V_JUMP; S.auto = o.x; S.squash = 1; Play.sfx.play('jump');
  }
  async function choose(g, o) {   // 第一次落在哪块板上就是答案
    const q = g.q;
    if (q.busy) return;
    if (q.done) return passGate(g, o, false);
    if (q.right >= 0) {   // 第一次答错时已经知道对的是哪块：再选
      if (o.o === q.right) { q.done = true; Play.sfx.play('star'); return passGate(g, o, false); }
      return wrong(g, o, true);
    }
    q.busy = true; S.mode = 'wait'; S.vy = 0; S.y = o.y; S.x = o.x;
    const a = await sh.gateAnswer(q.item, o.o);
    q.busy = false;
    if (S.mode !== 'wait') return;
    S.mode = 'play'; q.right = a.right;
    if (a.correct) { q.done = true; S.gatesOk++; passGate(g, o, true); }
    else if (a.lost) { q.done = true; passGate(g, o, false); }   // 没答上（过期、断网）：当作看答案过，不算错
    else wrong(g, o, false);
    buildQ();
  }
  function wrong(g, o, again) {
    o.tried = true; g.q.tried++; S.vy = 0; S.combo = 0;
    Play.sfx.play(again ? 'pop' : 'wrong'); fx.shake(4, 0.2);
    if (again && g.q.tried >= 2) { g.q.done = true; sh.toast('对的那块亮起来了，看看再跳上去', 'warn'); }
    else sh.toast(again ? '还不是它，再选一个' : '不是它，掉回云上再选一个', 'warn');
    pickSel(0); buildQ();
  }
  function passGate(g, o, sup) {   // 踩对：超级弹簧（一次答对）或普通弹簧（看了答案）
    g.passed = true; S.gate = null; S.mode = 'play'; S.y = o.y; S.auto = null;
    o.spring = clock;
    S.vy = sup ? V_SUPER : V_SPRING; S.spin = sup ? 1 : 0; S.squash = 1; S.combo++;
    const g2 = geo(), [sx, sy] = toScreen(g2, o.x, o.y);
    if (sup) {
      Play.sfx.play('right'); Play.sfx.play('power');
      if (window.Juice && Juice.buzz) Juice.buzz('right');
      sh.toast('🎉 答对！超级弹簧', 'good');
      for (let k = 1; k <= 9; k++) S.honey.push({x: o.x, y: o.y + 0.12 * k + 0.1, got: false, trail: true});   // 一路的蜂蜜
      fx.burst(sx, sy, {n: 30, colors: ['#ffc21a', '#fff', '#ff8fab', '#7bdff2'], speed: 420, shape: 'star'});
      fx.ring(sx, sy, {color: '#ffc21a', width: 8, grow: 700});
    } else { Play.sfx.play('jump'); sh.toast('弹过去啦，下次一次选对就是超级弹簧', 'good'); }
    buildQ();
  }

  // ------------------------------------------------------------ 小蜜蜂
  function land(p) {   // 落在一块板上
    const sp = p.k === 's';
    S.vy = sp ? V_SPRING : V_JUMP; S.squash = 1; S.spin = sp ? 1 : 0;
    if (sp) { p.sp = clock; Play.sfx.play('jump'); Play.sfx.play('whoosh'); } else Play.sfx.play('click');
    if (p.k === 'x') { p.broke = clock; Play.sfx.play('hit'); const g = geo(), [x, y] = toScreen(g, platX(p), p.y); fx.burst(x, y, {n: 12, colors: ['#c9b79c', '#a8957a'], speed: 160}); }
    if (p.y > S.lastY + 0.01) S.combo++; else if (p.y < S.lastY - 0.01) S.combo = 0;
    S.lastY = p.y;
  }
  function die() {
    S.mode = 'dead'; S.falls++; S.combo = 0; S.vy = 0;
    Play.sfx.play('hurt'); fx.shake(6, 0.3);
    if (c.quiz) c.quiz.item = null;
    c.die();
  }
  function win() {
    S.mode = 'win'; S.y = S.lv.height; S.vy = 0; S.vx = 0; buildQ();
    Play.sfx.play('win'); sh.toast('🏁 到塔顶啦！', 'good');
  }
  function checkpoint() {   // 每 1/4 一面旗；和 👻 上次的我比一比
    const k = Math.floor(S.top / S.lv.height * 4);
    if (k <= S.flags || k >= 4) return;
    S.flags = k;
    const g = ghost(), ahead = g != null && S.top / S.lv.height > g;
    sh.toast(`🚩 到 ${['', '1/4', '一半', '3/4'][k]} 啦${g == null ? '' : ahead ? '，比上次的我快！' : '，👻 上次的我在前面'}`, 'good');
    Play.sfx.play('star');
  }
  const seconds = () => Math.max(0, (c ? c.elapsed() : 0) - S.free);
  const ghost = () => S.lv.best ? Math.min(1, seconds() / S.lv.best) : null;   // 上次的我：按纪录的用时匀速往上
  const myRole = () => (c && c.me && ROLE[c.me.role] ? c.me.role : S.role);
  const nextGate = () => S.gates.find(g => !g.passed);

  // ------------------------------------------------------------ 更新
  function update(dt) {
    clock += dt; fx.update(dt);
    const s = S; if (!s) return;
    s.squash = Math.max(0, s.squash - dt * 5); s.spin = Math.max(0, s.spin - dt * 1.6);
    if (s.mode === 'preview') { bob(dt); return; }
    if (s.mode === 'gate') gateTick(dt);
    if (s.mode === 'play') physics(dt);
    if (s.mode === 'win') {
      s.winT += dt;
      if (Math.random() < 0.3) fx.burst(Math.random() * stage.W, stage.H * 0.25, {n: 4, shape: 'confetti', speed: 240});
      if (!s.sent && s.winT > 1.4) { s.sent = true; arrived(); }
    }
    if (s.mode === 'play' || s.mode === 'gate' || s.mode === 'wait') {
      const g = s.gate;
      if (g && g.q && g.q.armed && g.q.right < 0 && g.q.free < GATE_FREE) { const d = Math.min(dt, GATE_FREE - g.q.free); g.q.free += d; s.free += d; }
      if (c && c.elapsed() >= s.limit) { s.mode = 'done'; sh.toast('⏰ 时间到！', 'warn'); setTimeout(() => report('lose'), 900); }
    }
    camera(dt);
    if (s.mode === 'play' && s.y < s.cam - BEE * 2) die();
    const ng = nextGate();
    if (ng && ng.y < s.cam + 1.4) loadGate(ng);   // 快到题板了：先把题要来
    if (c && c.net && (sendT -= dt) <= 0 && s.mode !== 'win') { sendT = 0.25; c.net.send({t: 'jp', x: +s.x.toFixed(3), y: +s.y.toFixed(3)}); }
    hudTick();
  }
  function bob(dt) {   // 预览：在几块板上跳来跳去
    S.vy -= G * dt; S.y += S.vy * dt;
    const p = S.plats[Math.floor(clock / 1.1) % S.plats.length];
    S.x += (p.x0 - S.x) * Math.min(1, dt * 2);
    if (S.y <= p.y && S.vy < 0) { S.y = p.y; land(p); }
    if (S.y < -0.3) { S.y = 0; S.vy = V_JUMP; }
  }
  function steer(dt) {   // 想往哪边走：键盘（按住）、鼠标 / 手指（目标位置）、题板上跳向答案板（自动驾驶，一碰键就停）
    const s = S, inp = Play.input, l = inp.keys.left, r = inp.keys.right;
    inp.take('left'); inp.take('right'); inp.take('jump');   // 「按了一下」只在题板上用，空中按的不留到落在云上
    let want = 0;
    if (l || r) { s.aim = null; s.auto = null; want = (r ? 1 : 0) - (l ? 1 : 0); }
    else if (drag && drag.side) want = drag.side;
    else {
      const t = s.aim ?? s.auto;
      if (t != null) { const d = s.auto != null && s.aim == null ? wrapD(t - s.x) : t - s.x; want = Play.clamp(d * 9, -1, 1); if (Math.abs(d) < 0.004) want = 0; }
    }
    const tv = want * VX;
    s.vx += Play.clamp(tv - s.vx, -AX * dt, AX * dt) * (want ? 1 : 1.6);
    if (Math.abs(s.vx) > 0.05) s.face = Math.sign(s.vx);
  }
  function physics(dt) {
    const s = S;
    steer(dt);
    for (const w of s.lv.wind || []) if (s.y > w[0] && s.y < w[1]) s.vx += w[2] * 0.9 * dt;
    const py = s.y;
    s.vy -= G * dt; s.x = mod(s.x + s.vx * dt, 1); s.y += s.vy * dt;
    if (s.rescue) s.rescue.t -= dt;
    const ng = nextGate();
    if (ng && py < ng.y && s.y >= ng.y && s.vy > V_CAP) s.vy = V_CAP;   // 从下面穿过软云：最高到云上一点点，落回云上
    if (s.vy < 0) {
      const hit = landing(py, s.y);
      if (hit) {
        s.y = hit.y;
        if (hit.what === 'plat') land(hit.p);
        else if (hit.what === 'cloud') { if (hit.g.passed) { s.vy = V_JUMP; s.squash = 1; } else rest(hit.g); }
        else if (hit.what === 'opt') choose(hit.g, hit.o);
        else if (hit.what === 'rescue') { s.vy = V_JUMP; s.squash = 1; Play.sfx.play('click'); }
        else if (hit.what === 'top') return win();
      }
    }
    s.top = Math.max(s.top, s.y); checkpoint();
    for (const h of s.honey) {   // 吃蜂蜜
      if (h.got || Math.abs(h.y - s.y - BEE) > 0.05 || Math.abs(wrapD(h.x - s.x)) > 0.07) continue;
      h.got = true; s.honeyN++; Play.sfx.play('coin');
      const g = geo(), [x, y] = toScreen(g, h.x, h.y);
      fx.burst(x, y, {n: 6, colors: ['#ffc21a', '#fff'], speed: 140}); fx.text(x + 18, y, '+1', {size: 20, color: '#e08a00', rise: 40, life: 0.7});
    }
  }
  function landing(y0, y1) {   // 这一帧往下落时碰到的最高的那个（板子、软云、答案板、救援云、塔顶）
    const s = S, half = width() / 2 + BEE * 0.35;
    let best = null;
    const take = (y, what, o) => { if (y <= y0 + 1e-9 && y >= y1 && (!best || y > best.y)) best = Object.assign({y, what}, o); };
    if (s.lv.height && y1 <= s.lv.height && y0 >= s.lv.height) take(s.lv.height, 'top');
    if (s.rescue && s.rescue.t > 0) take(s.rescue.y, 'rescue');
    take(0, 'plat', {p: {k: 'n', y: 0}});   // 起点的草地
    for (const g of s.gates) {
      take(g.y, 'cloud', {g});
      if (g.passed || !g.q || !g.q.armed) continue;
      for (const o of g.opts) if (openOpt(g, o) && Math.abs(wrapD(o.x - s.x)) <= o.w / 2 + BEE * 0.35) take(o.y, 'opt', {g, o});
    }
    let i = lowIndex(y1);
    for (; i < s.plats.length && s.plats[i].y <= y0; i++) {
      const p = s.plats[i];
      if (solid(p) && Math.abs(wrapD(platX(p) - s.x)) <= half) take(p.y, 'plat', {p});
    }
    for (const g of s.gates) if (g.passed) for (const o of g.opts) if (o.spring && Math.abs(wrapD(o.x - s.x)) <= o.w / 2) take(o.y, 'plat', {p: {k: 's', y: o.y, sp: 0}});
    return best;
  }
  function lowIndex(y) { let lo = 0, hi = S.plats.length; while (lo < hi) { const m = (lo + hi) >> 1; if (S.plats[m].y < y) lo = m + 1; else hi = m; } return lo; }
  function gateTick(dt) {   // 坐在软云上：← → 换答案（小蜜蜂走过去），空格跳
    const s = S, inp = Play.input;
    if (inp.blocked) return;
    if (inp.take('left')) pickSel(-1);
    if (inp.take('right')) pickSel(1);
    const o = s.gate.opts.find(o => o.o === s.sel);
    if (o) { const d = o.x - s.x; s.x += Play.clamp(d, -dt * 0.8, dt * 0.8); if (Math.abs(d) > 0.01) s.face = Math.sign(d); }
    if (inp.take('jump')) hop();
  }
  function camera(dt) {
    const s = S;
    if (s.mode === 'dead' || s.mode === 'preview') return;
    s.hold = Math.max(0, s.hold - dt);
    const ng = nextGate(), gateNear = ng && ng.y < s.cam + 0.95;
    if (s.mode === 'play' && !s.hold && !gateNear) s.cam += s.lv.scroll * dt;
    if (s.gate) s.cam = Math.max(s.cam, s.cam + (s.gate.y - 0.3 - s.cam) * Math.min(1, dt * 3));   // 题板：软云放在下面三分之一，答案和题都看得见
    if (s.mode === 'win') s.cam = Math.max(s.cam, s.cam + (s.lv.height - 0.55 - s.cam) * Math.min(1, dt * 3));
    else s.cam = Math.max(s.cam, s.y - 0.5);
  }

  // ------------------------------------------------------------ 画面
  function geo() {
    const W = stage.W, H = stage.H, wide = W > H * 0.8;
    const CW = wide ? Math.round(H * 0.5625) : W - 30, CX = wide ? Math.round((W - CW) / 2) : 0;   // 窄屏：右边留一条给高度条
    return {W, H, CW, CX, wide};
  }
  const toScreen = (g, x, y) => [g.CX + x * g.CW, g.H - (y - S.cam) * g.H];
  function render() {
    const s = S, g = geo(), P = pal();
    stage.begin();
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    backdrop(g, P);
    ctx.save(); ctx.beginPath(); ctx.rect(g.CX, -50, g.CW, g.H + 100); ctx.clip();
    if (s.lv.height) finish(g, P);
    ground(g, P);
    for (const w of s.lv.wind || []) wind(g, w);
    const lo = lowIndex(s.cam - 0.1);
    for (let i = lo; i < s.plats.length && s.plats[i].y < s.cam + 1.1; i++) plat(g, s.plats[i], P);
    if (s.rescue && s.rescue.t > 0) { const [, y] = toScreen(g, 0, s.rescue.y); ctx.globalAlpha = Math.min(1, s.rescue.t); puff(g.CX + s.x * g.CW, y + 18, g.H / 900 * 0.55, P, true); ctx.globalAlpha = 1; }
    for (const h of s.honey) if (!h.got && h.y > s.cam - 0.05 && h.y < s.cam + 1.05) { const [x, y] = toScreen(g, h.x, h.y); honey(x, y + Math.sin(clock * 3 + h.x * 9) * 3, g.H / 900); }
    for (const gt of s.gates) gate(g, gt, P);   // 软云盖在下面的板子、蜂蜜上
    for (const p of others()) mate(g, p);
    if (s.vy > V_JUMP * 1.1) speed(g, Math.min(1, (s.vy - V_JUMP) / (V_SUPER - V_JUMP)));
    me(g);
    ctx.restore();
    if (g.wide) edges(g);
    if (s.mode !== 'preview') heightBar(g);
    if (s.mode === 'win') {
      ctx.font = `900 46px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.lineWidth = 8; ctx.strokeStyle = '#fff'; ctx.strokeText('🏁 到塔顶啦！', g.W / 2, g.H * 0.3); ctx.fillStyle = '#ff6a00'; ctx.fillText('🏁 到塔顶啦！', g.W / 2, g.H * 0.3);
    }
    ctx.restore();
    fx.draw(ctx); fx.drawFlash(ctx, g.W, g.H);
  }
  function skyStops(P) {
    if (P.sky) return [P.sky[0], P.sky[1], P.warm];
    const d = Play.scene.themes.day.sky;
    return [d[0], d[1], P.warm];
  }
  function backdrop(g, P) {   // 天空、远山（往下沉得慢）、三层云（越近沉得越快）
    const st = skyStops(P), gr = ctx.createLinearGradient(0, 0, 0, g.H);
    gr.addColorStop(0, st[0]); gr.addColorStop(0.6, st[1]); gr.addColorStop(1, st[2]);
    ctx.fillStyle = gr; ctx.fillRect(0, 0, g.W, g.H);
    const cam = S.cam;
    if (P.stars) {
      for (let i = 0; i < 70; i++) {
        const x = (i * 137.5) % g.W, y = mod(i * 71.3 + cam * g.H * 0.05, g.H), tw = 0.5 + 0.5 * Math.sin(clock * 1.6 + i);
        ctx.globalAlpha = 0.3 + 0.6 * tw; ctx.fillStyle = scene() === 'lava' ? '#ffb27a' : '#fff'; ctx.fillRect(x, y, 2, 2);
      }
      ctx.globalAlpha = 1;
    }
    const base = g.H * 0.93 + cam * g.H * 0.12;   // 远山：爬得越高沉得越深
    if (base < g.H + 200) {
      const hill = (b, a, ph, col) => { ctx.fillStyle = col; ctx.beginPath(); ctx.moveTo(0, g.H); for (let x = 0; x <= g.W + 20; x += 20) ctx.lineTo(x, b - Math.sin(x / 180 + ph) * a - Math.sin(x / 67 + ph * 2) * a * 0.3); ctx.lineTo(g.W, g.H); ctx.fill(); };
      ctx.globalAlpha = 0.7; hill(base - 60, 70, 1, P.far); ctx.globalAlpha = 1;
      hill(base, 50, 2.3, P.hill[0]); hill(base + 60, 36, 4.1, P.hill[1]);
    }
    const r = rng(11);
    for (const [k, n, sc, al] of [[0.18, 5, 0.9, 0.55], [0.4, 4, 0.7, 0.75]]) {   // 两层云，往下沉的速度不一样
      for (let i = 0; i < n; i++) {
        const x = r() * g.W, y = mod(r() * g.H * 1.6 + cam * g.H * k, g.H * 1.6) - g.H * 0.3, s = sc * (0.6 + r() * 0.6) * g.H / 900;
        ctx.globalAlpha = al; puff(x, y, s, P, false); ctx.globalAlpha = 1;
      }
    }
  }
  function edges(g) {   // 天空柱两边：淡一点，白边，左右穿屏的小箭头
    ctx.fillStyle = 'rgba(255,255,255,.18)'; ctx.fillRect(0, 0, g.CX, g.H); ctx.fillRect(g.CX + g.CW, 0, g.W - g.CX - g.CW, g.H);
    ctx.fillStyle = 'rgba(255,255,255,.6)'; ctx.fillRect(g.CX - 4, 0, 4, g.H); ctx.fillRect(g.CX + g.CW, 0, 4, g.H);
    ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.lineWidth = 3.5; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(g.CX - 26, g.H / 2 - 8); ctx.lineTo(g.CX - 18, g.H / 2); ctx.lineTo(g.CX - 26, g.H / 2 + 8);
    ctx.moveTo(g.CX + g.CW + 26, g.H / 2 - 8); ctx.lineTo(g.CX + g.CW + 18, g.H / 2); ctx.lineTo(g.CX + g.CW + 26, g.H / 2 + 8); ctx.stroke();
  }
  function puff(x, y, s, P, outline) {   // 一朵云（outline：描边的，孩子踩得住的云）
    const cs = [[0, 0, 38], [-40, 10, 28], [42, 8, 30], [-18, -20, 30], [20, -24, 28], [-62, 22, 18], [66, 20, 20]];
    const circles = (dx, dy, add) => { ctx.beginPath(); for (const [a, b, r] of cs) { ctx.moveTo(x + (a + dx) * s + (r + add) * s, y + (b + dy) * s); ctx.arc(x + (a + dx) * s, y + (b + dy) * s, (r + add) * s, 0, Math.PI * 2); } ctx.fill(); };
    if (outline) { ctx.fillStyle = INK; circles(0, 6, 3); circles(0, 0, 3); }
    ctx.fillStyle = P.cloud; circles(0, 0, 0);
    ctx.fillStyle = P.cs; ctx.globalAlpha *= 0.55; ctx.beginPath(); ctx.ellipse(x, y + 26 * s, 70 * s, 10 * s, 0, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha /= 0.55;
  }
  function ground(g, P) {   // 起点：一整条草地
    const [, y] = toScreen(g, 0, 0);
    if (y > g.H + 40) return;
    ctx.fillStyle = INK; ctx.fillRect(g.CX, y - 3, g.CW, g.H);
    ctx.fillStyle = P.hill[1]; ctx.fillRect(g.CX, y, g.CW, g.H);
    ctx.fillStyle = P.hill[0]; ctx.fillRect(g.CX, y, g.CW, 12);
  }
  function finish(g, P) {   // 塔顶：一整条描边的云 + 🏁
    const [, y] = toScreen(g, 0, S.lv.height);
    if (y < -120 || y > g.H + 120) return;
    cloudBand(g, y, P);
    const fx0 = g.CX + g.CW * 0.68, k = g.H / 900;
    ctx.strokeStyle = INK; ctx.lineWidth = 5 * k; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(fx0, y - 4); ctx.lineTo(fx0, y - 90 * k); ctx.stroke();
    for (let i = 0; i < 4; i++) for (let j = 0; j < 3; j++) { ctx.fillStyle = (i + j) % 2 ? INK : '#fff'; ctx.fillRect(fx0 + i * 14 * k, y - 90 * k + j * 12 * k, 14 * k, 12 * k); }
    ctx.lineWidth = 3 * k; ctx.strokeRect(fx0, y - 90 * k, 56 * k, 36 * k);
  }
  function cloudBand(g, y, P) {   // 横贯天空柱的软云：上下两串鼓包，描粗边（像设计稿里孩子踩得住的云）
    const k = g.H / 900, top = [], bot = [], T = 54 * k;
    for (let x = -20, i = 0; x <= g.CW + 40; x += 46 * k, i++) {
      top.push([g.CX + x, y + (i % 2 ? 6 : -4) * k, (34 + (i * 7) % 13) * k]);
      bot.push([g.CX + x + 23 * k, y + T, (22 + (i * 5) % 9) * k]);
    }
    const body = (add, dy, col) => {
      ctx.fillStyle = col; ctx.beginPath();
      for (const [a, b, r] of [...top, ...bot]) { ctx.moveTo(a + r + add, b + dy); ctx.arc(a, b + dy, r + add, 0, Math.PI * 2); }
      ctx.rect(g.CX - 30, y + dy - add, g.CW + 60, T + add * 2); ctx.fill();
    };
    body(3, 4 * k, INK); body(3, 0, INK); body(0, 0, P.cloud);
    ctx.fillStyle = P.cs; ctx.globalAlpha = 0.55;   // 底下一层淡淡的影子
    ctx.beginPath(); for (const [a, b, r] of bot) { ctx.moveTo(a + r * 0.8, b + 4 * k); ctx.arc(a, b + 4 * k, r * 0.8, 0, Math.PI * 2); } ctx.rect(g.CX - 30, y + T * 0.55, g.CW + 60, T * 0.45); ctx.fill();
    ctx.globalAlpha = 1;
  }
  function gate(g, gt, P) {
    const [, cy] = toScreen(g, 0, gt.y), k = g.H / 900;
    if (cy < -g.H * 0.9 || cy > g.H + 120) return;
    if (!gt.passed) {   // 云带：只有踩对才弹得过去
      for (const [a, b, c2] of gt.mist) {
        const [, my] = toScreen(g, 0, gt.y + RISE + 0.12 + b * (BAND - RISE - 0.18));
        ctx.globalAlpha = 0.3 + c2 * 0.25; ctx.fillStyle = '#fff';
        ctx.beginPath(); ctx.ellipse(g.CX + a * g.CW, my, (50 + c2 * 30) * k, (22 + c2 * 10) * k, 0, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    for (const o of gt.opts) answer(g, gt, o, P);
    cloudBand(g, cy, P);
  }
  function answer(g, gt, o, P) {   // 答案板：板子 + 立着的牌子
    const q = gt.q, k = g.H / 900;
    if (o.tried) o.fall = Math.min(1, o.fall + 1 / 40);
    const gone = q && q.done && !o.tried && o.o !== q.right;   // 看了答案：别的牌子淡掉
    const [x, y0] = toScreen(g, o.x, o.y), y = y0 + o.fall * 26 * k, w = o.w * g.CW;
    const right = q && q.done && o.o === q.right, sel = S.gate === gt && S.sel === o.o && openOpt(gt, o);
    ctx.save(); ctx.globalAlpha = gone ? 0.35 : 1;
    ctx.translate(x, y); ctx.rotate(o.tried ? (o.o % 2 ? -1 : 1) * 0.14 * o.fall : 0);
    if (right || sel) { ctx.fillStyle = right ? 'rgba(201,242,211,.9)' : 'rgba(255,246,168,.9)'; Play.roundRect(ctx, -w / 2 - 10 * k, -10 * k, w + 20 * k, 49 * k, 20 * k); ctx.fill(); }
    slab(-w / 2, 0, w, k, o.tried ? {top: '#f1eee8', side: '#d8d2c8', side2: '#bdb5a8'} : P);
    if (o.spring) spring(0, 0, k, Math.min(1, (clock - o.spring) * 6));
    const it = q && q.item, text = it && it.options ? it.options[o.o] : q && q.done ? '↑' : '…';
    const maxW = g.CW / gt.opts.length - 12 * k, lines = fit(text, maxW - 20 * k, k);   // 牌子不比自己那一格宽：字小一点，还放不下就分两行
    const lh = lines.size * 1.15 * k, bw = Math.min(maxW, Math.max(88 * k, lines.w + 24 * k)), bh = Math.max(62 * k, lines.rows.length * lh + 20 * k);
    const by = -46 * k - bh / 2 - (sel ? Math.abs(Math.sin(clock * 5)) * 5 * k : 0);
    ctx.strokeStyle = INK; ctx.lineWidth = 4 * k; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, by + bh); ctx.stroke();
    ctx.fillStyle = INK; Play.roundRect(ctx, -bw / 2, by + 4 * k, bw, bh, 16 * k); ctx.fill();
    ctx.fillStyle = o.tried ? '#efebe4' : right ? '#d9f7e0' : sel ? '#fff8d6' : '#fff'; Play.roundRect(ctx, -bw / 2, by, bw, bh, 16 * k); ctx.fill();
    ctx.lineWidth = 3 * k; ctx.strokeStyle = right ? '#2fa84f' : sel ? '#ff9f1a' : INK; ctx.stroke();
    ctx.font = `900 ${Math.round(lines.size * k)}px ${FONT}`;
    ctx.fillStyle = o.tried ? '#b8ae9e' : INK; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    lines.rows.forEach((t, i) => ctx.fillText(t, 0, by + bh / 2 + 2 * k + (i - (lines.rows.length - 1) / 2) * lh));
    if (o.tried) { ctx.strokeStyle = '#d26a5a'; ctx.lineWidth = 4 * k; ctx.beginPath(); ctx.moveTo(-bw / 2 + 12 * k, by + 12 * k); ctx.lineTo(bw / 2 - 12 * k, by + bh - 12 * k); ctx.stroke(); }
    ctx.restore();
    o.rect = {x0: x - bw / 2, x1: x + bw / 2, y0: y - 46 * k - bh, y1: y + 20 * k};   // 点答案牌用
  }
  function fit(text, maxW, k) {   // 答案牌上的字：34 号放不下就缩小，缩到 20 号还放不下就分两行（按「；」、空格或者对半分）
    const measure = (t, sz) => { ctx.font = `900 ${Math.round(sz * k)}px ${FONT}`; return ctx.measureText(t).width; };
    for (let sz = 34; sz >= 20; sz -= 2) if (measure(text, sz) <= maxW) return {rows: [text], size: sz, w: measure(text, sz)};
    const cut = text.search(/[；;，,、 （(]/), at = cut <= 0 ? Math.ceil(text.length / 2) : '（('.includes(text[cut]) ? cut : cut + 1;   // 括号放到第二行
    const rows = [text.slice(0, at).trim(), text.slice(at).trim()].filter(Boolean);
    let sz = 24;
    while (sz > 13 && Math.max(...rows.map(r => measure(r, sz))) > maxW) sz -= 1;
    return {rows, size: sz, w: Math.max(...rows.map(r => measure(r, sz)))};
  }
  function slab(x0, y, w, k, P) {   // 一块板：描边、厚度、高光（设计稿）
    const h = 19 * k, d = 10 * k;
    ctx.lineWidth = 3 * k; ctx.strokeStyle = INK;
    ctx.fillStyle = P.side2; Play.roundRect(ctx, x0, y, w, h + d, 12 * k); ctx.fill(); ctx.stroke();
    ctx.fillStyle = P.side; Play.roundRect(ctx, x0, y, w, h + 2 * k, 10 * k); ctx.fill(); ctx.stroke();
    ctx.fillStyle = P.top; ctx.globalAlpha *= 0.85; Play.roundRect(ctx, x0 + 6 * k, y + 3 * k, w - 12 * k, 7 * k, 3.5 * k); ctx.fill(); ctx.globalAlpha /= 0.85;
  }
  const MOVING = {top: '#eaf4ff', side: '#5aa9f0', side2: '#3a86d6'}, CRACKED = {top: '#f4ece0', side: '#c9b79c', side2: '#a8957a'};
  function plat(g, p, P) {
    const k = g.H / 900, w = width() * g.CW;
    let [x, y] = toScreen(g, platX(p), p.y);
    if (p.broke) { const t = clock - p.broke; if (t > 1) return; y += t * t * 900 * k; ctx.globalAlpha = 1 - t; }
    const blinkA = p.k === 'b' ? (blinkOn(p) ? 1 : 0.28) : 1;
    ctx.globalAlpha *= blinkA;
    for (const dx of [0, x - w / 2 < g.CX ? g.CW : x + w / 2 > g.CX + g.CW ? -g.CW : null]) {   // 跨过左右边：另一边也画半块
      if (dx === null) continue;
      const xx = x + dx, x0 = xx - w / 2;
      slab(x0, y, w, k, p.k === 'm' ? MOVING : p.k === 'x' ? CRACKED : P);
      if (p.k === 'n') {   // 两滴往下淌的蜜
        ctx.fillStyle = P.side2; ctx.strokeStyle = INK; ctx.lineWidth = 2.5 * k;
        for (const [f, h] of [[0.22, 9], [0.7, 6]]) { ctx.beginPath(); ctx.moveTo(x0 + w * f, y + 20 * k); ctx.quadraticCurveTo(x0 + w * f, y + (20 + h) * k, x0 + w * f + 5 * k, y + (20 + h) * k); ctx.quadraticCurveTo(x0 + w * f + 10 * k, y + (20 + h) * k, x0 + w * f + 10 * k, y + 20 * k); ctx.fill(); ctx.stroke(); }
      }
      if (p.k === 'm') {
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 3 * k; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        ctx.beginPath(); ctx.moveTo(x0 + 16 * k, y + 6 * k); ctx.lineTo(x0 + 10 * k, y + 10 * k); ctx.lineTo(x0 + 16 * k, y + 14 * k);
        ctx.moveTo(x0 + w - 16 * k, y + 6 * k); ctx.lineTo(x0 + w - 10 * k, y + 10 * k); ctx.lineTo(x0 + w - 16 * k, y + 14 * k); ctx.stroke();
      }
      if (p.k === 'x') {
        ctx.strokeStyle = INK; ctx.lineWidth = 2.5 * k; ctx.beginPath();
        ctx.moveTo(x0 + w * 0.3, y + 2 * k); ctx.lineTo(x0 + w * 0.3 + 8 * k, y + 11 * k); ctx.lineTo(x0 + w * 0.3 + 2 * k, y + 17 * k);
        ctx.moveTo(x0 + w * 0.68, y + 2 * k); ctx.lineTo(x0 + w * 0.68 - 6 * k, y + 10 * k); ctx.lineTo(x0 + w * 0.68 + 2 * k, y + 17 * k); ctx.stroke();
      }
      if (p.k === 'b' && !blinkOn(p)) { ctx.setLineDash([5 * k, 5 * k]); ctx.strokeStyle = '#fff'; ctx.lineWidth = 2.5 * k; Play.roundRect(ctx, x0 - 4 * k, y - 4 * k, w + 8 * k, 37 * k, 14 * k); ctx.stroke(); ctx.setLineDash([]); }
      if (p.k === 's') spring(xx + w * 0.2, y, k, p.sp ? Math.max(0, 1 - (clock - p.sp) * 3) : 0);
    }
    ctx.globalAlpha = 1;
  }
  function spring(x, y, k, up) {   // up：0 压着，1 弹开
    const hgt = (22 + 32 * up) * k, n = 4 + Math.round(up * 2);
    ctx.fillStyle = '#b8c2cc'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5 * k; Play.roundRect(ctx, x - 17 * k, y - 5 * k, 34 * k, 8 * k, 4 * k); ctx.fill(); ctx.stroke();
    const path = () => { ctx.beginPath(); ctx.moveTo(x - 12 * k, y); for (let i = 1; i <= n; i++) ctx.lineTo(x + (i % 2 ? 12 : -12) * k, y - hgt * i / n); };
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    path(); ctx.strokeStyle = INK; ctx.lineWidth = 6 * k; ctx.stroke();
    path(); ctx.strokeStyle = '#dfe7ee'; ctx.lineWidth = 2.5 * k; ctx.stroke();
    ctx.fillStyle = '#ff5a4f'; ctx.strokeStyle = INK; ctx.lineWidth = 3 * k; Play.roundRect(ctx, x - 19 * k, y - hgt - 11 * k, 38 * k, 12 * k, 6 * k); ctx.fill(); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.7)'; Play.roundRect(ctx, x - 13 * k, y - hgt - 9 * k, 16 * k, 3.5 * k, 2 * k); ctx.fill();
  }
  function honey(x, y, k) {
    ctx.save(); ctx.translate(x, y); ctx.scale(k * 1.1, k * 1.1);
    ctx.beginPath(); ctx.moveTo(0, -15); ctx.bezierCurveTo(6, -6, 11, 0, 11, 6); ctx.arc(0, 6, 11, 0, Math.PI); ctx.bezierCurveTo(-11, 0, -6, -6, 0, -15);
    ctx.fillStyle = '#ffc21a'; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.beginPath(); ctx.ellipse(-4, 3, 2.6, 4.5, 0, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
  function wind(g, w) {
    const [, y1] = toScreen(g, 0, w[0]), [, y0] = toScreen(g, 0, w[1]);
    if (y0 > g.H || y1 < 0) return;
    ctx.strokeStyle = 'rgba(255,255,255,.7)'; ctx.lineWidth = 4; ctx.lineCap = 'round';
    for (let i = 0; i < 6; i++) {
      const yy = y0 + (y1 - y0) * (i + 0.5) / 6, off = mod(clock * 160 * w[2] + i * 97, g.CW + 120) - 60;
      ctx.beginPath(); ctx.moveTo(g.CX + off, yy); ctx.quadraticCurveTo(g.CX + off + 30 * w[2], yy - 10, g.CX + off + 60 * w[2], yy); ctx.stroke();
    }
  }
  function speed(g, a) {   // 弹簧：速度线
    const r = rng(Math.floor(clock * 12));
    ctx.fillStyle = '#fff';
    for (let i = 0; i < 18; i++) { ctx.globalAlpha = a * (0.3 + r() * 0.5); ctx.fillRect(g.CX + r() * g.CW, r() * g.H, 2 + r() * 3, 60 + r() * 140); }
    ctx.globalAlpha = 1;
  }
  function me(g) {
    const s = S, [x, y] = toScreen(g, s.x, s.y), k = g.H / 900;
    const pose = s.mode === 'gate' ? 'sit' : s.mode === 'dead' ? 'sit' : s.mode === 'win' ? 'cheer' : s.vy > V_JUMP * 1.05 ? 'fly' : s.squash > 0.6 ? 'land' : 'up';
    const r = BEE * g.H;
    if (s.rescue && s.rescue.t > 1 && Math.floor(clock * 10) % 2) ctx.globalAlpha = 0.6;
    const o = {pose, role: myRole(), rot: s.spin > 0 ? (1 - s.spin) * Math.PI * 2 * (s.face || 1) : s.vx * 0.25, face: s.face};
    for (const dx of [0, -g.CW, g.CW]) if (!dx || Math.abs(x + dx - (g.CX + g.CW / 2)) < g.CW / 2 + r * 2) bee(x + dx, y - r * 0.85, k * 0.95, o);   // 穿屏：两边各露一半
    ctx.globalAlpha = 1;
    if (s.mode === 'gate') {   // 云上：头顶一个「?」
      const bx = s.x > 0.75 ? x - 78 * k : x + 34 * k, by = y - 74 * k + Math.sin(clock * 3) * 3;   // 靠右边时放到左边
      ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 3 * k; Play.roundRect(ctx, bx, by - 22 * k, 44 * k, 34 * k, 12 * k); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.font = `900 ${Math.round(22 * k)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('?', bx + 22 * k, by - 4 * k);
    }
  }
  function bee(x, y, s, o) {   // 小蜜蜂（设计稿的样子），o.pose：up 往上、land 落地压扁、fly 弹簧飞、sit 坐着、cheer 欢呼
    const [c1, c2] = ROLE[o.role] || ROLE.sun, pose = o.pose;
    let rx = 27, ry = 25;
    if (pose === 'up') { rx = 23; ry = 30; } if (pose === 'land') { rx = 31; ry = 19; } if (pose === 'fly') { rx = 24; ry = 29; }
    const look = pose === 'sit' ? -3 : pose === 'land' ? 1 : -2, flap = Math.sin(clock * 30) * 0.18;
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.rotate(o.rot || 0);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = INK;
    for (const d of [-1, 1]) {   // 翅膀
      ctx.save(); ctx.translate(16 * d, -ry + 2); ctx.rotate(d * (0.49 + flap));
      ctx.fillStyle = '#eaf7ff'; ctx.lineWidth = 3; ctx.beginPath(); ctx.ellipse(0, 0, 15, 20, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.restore();
    }
    ctx.lineWidth = 3.5;   // 触角
    for (const d of [-1, 1]) { ctx.beginPath(); ctx.moveTo(8 * d, -ry + 3); ctx.quadraticCurveTo(14 * d, -ry - 13, 22 * d, -ry - 17); ctx.stroke(); ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(22 * d, -ry - 17, 5, 0, Math.PI * 2); ctx.fill(); }
    ctx.lineWidth = 4;
    if (pose === 'fly' || pose === 'cheer') {
      for (const d of [-1, 1]) { ctx.beginPath(); ctx.moveTo(d * (rx - 3), -4); ctx.quadraticCurveTo(d * (rx + 11), -14, d * (rx + 11), -28); ctx.stroke(); ctx.fillStyle = c1; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(d * (rx + 11), -28, 4.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); ctx.lineWidth = 4; }
      for (const d of [-1, 1]) { ctx.beginPath(); ctx.moveTo(9 * d, ry - 6); ctx.lineTo(15 * d, ry + 6); ctx.stroke(); }
    } else if (pose !== 'sit') for (const d of [-1, 1]) { ctx.beginPath(); ctx.moveTo(8 * d, ry - 3); ctx.lineTo(10 * d, ry + 6); ctx.stroke(); }
    ctx.save(); ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); ctx.fillStyle = c1; ctx.fill(); ctx.clip();   // 身子、条纹
    ctx.fillStyle = INK; ctx.fillRect(-40, ry * 0.32, 80, ry * 0.28); ctx.fillRect(-40, ry * 0.78, 80, 40);
    ctx.fillStyle = 'rgba(255,255,255,.45)'; ctx.beginPath(); ctx.ellipse(-rx * 0.35, -ry * 0.55, rx * 0.35, ry * 0.22, 0, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha *= 0.18; ctx.fillStyle = c2; ctx.beginPath(); ctx.ellipse(0, ry * 0.2, rx, ry * 0.7, 0, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    ctx.lineWidth = 3.5; ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.save(); ctx.translate((o.face || 0) * 2, -ry * 0.18);   // 脸
    for (const d of [-1, 1]) {
      ctx.fillStyle = '#fff'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.ellipse(9.5 * d, 0, 7.5, 8.5, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(9.5 * d + 1, look + 1, 4.3, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(9.5 * d + 2.5, look - 0.5, 1.6, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = 'rgba(255,138,138,.7)'; ctx.beginPath(); ctx.ellipse(17 * d, 10, 5, 3, 0, 0, Math.PI * 2); ctx.fill();
    }
    ctx.lineWidth = 2.5;
    if (pose === 'fly' || pose === 'cheer') { ctx.fillStyle = '#c4413a'; ctx.beginPath(); ctx.moveTo(-7, 9); ctx.quadraticCurveTo(0, 19, 7, 9); ctx.closePath(); ctx.fill(); ctx.stroke(); }
    else { ctx.beginPath(); ctx.moveTo(pose === 'sit' ? -4 : -6, 10); ctx.quadraticCurveTo(0, pose === 'sit' ? 12 : 16, pose === 'sit' ? 4 : 6, 10); ctx.stroke(); }
    ctx.restore();
    ctx.fillStyle = INK; ctx.beginPath(); ctx.moveTo(-5, ry - 1); ctx.lineTo(0, ry + 9); ctx.lineTo(5, ry - 1); ctx.fill();
    ctx.restore();
  }
  function mate(g, p) {   // 一起玩的人：半透明，头上写名字；在屏幕上面就在顶上标「谁 ↑ 几米」
    const m = mates[p.id]; if (!m || m.y == null) return;
    const k = g.H / 900;
    if (m.y > S.cam + 1.02) {
      const t = `${p.name} ↑ ${Math.round((m.y - S.cam - 1) * M_PER_H) + 1} 米`;
      ctx.font = `900 ${Math.round(15 * k)}px ${FONT}`; const w = ctx.measureText(t).width + 24 * k, x = Play.clamp(g.CX + m.x * g.CW, g.CX + w / 2 + 6, g.CX + g.CW - w / 2 - 6);
      ctx.fillStyle = (ROLE[p.role] || ROLE.sky)[0]; ctx.strokeStyle = INK; ctx.lineWidth = 3; Play.roundRect(ctx, x - w / 2, 14, w, 30 * k, 15 * k); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(t, x, 14 + 15 * k); return;
    }
    if (m.y < S.cam - 0.1) return;
    const [x, y] = toScreen(g, m.x, m.y);
    ctx.globalAlpha = 0.5; bee(x, y - BEE * g.H * 0.85, k * 0.95, {pose: 'up', role: p.role}); ctx.globalAlpha = 0.75;
    ctx.font = `900 ${Math.round(14 * k)}px ${FONT}`; const w = ctx.measureText(p.name).width + 18 * k;
    ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5; Play.roundRect(ctx, x - w / 2, y - 100 * k, w, 24 * k, 12 * k); ctx.fill(); ctx.stroke();
    ctx.fillStyle = INK; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(p.name, x, y - 88 * k);
    ctx.globalAlpha = 1;
  }
  function heightBar(g) {   // 右边：爬到哪了、🚩 每 1/4、👻 上次的我、大家、🏁
    const H = S.lv.height; if (!H) return;
    const k = g.H / 900, side = g.W - g.CX - g.CW;
    const x = g.wide ? g.CX + g.CW + Math.min(side * 0.55, 150 * k) : g.W - 15, w = g.wide ? 26 * k : 12, y0 = g.wide ? 120 * k : 96, h = g.H - y0 - (g.wide ? 120 * k : 80);
    ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 3; Play.roundRect(ctx, x - w / 2, y0 - 4, w, h + 8, w / 2); ctx.fill(); ctx.stroke();
    const f = Math.min(1, Math.max(0, S.y) / H);
    ctx.fillStyle = '#ffc21a'; Play.roundRect(ctx, x - w / 2 + 4, y0 + h * (1 - f), w - 8, h * f, (w - 8) / 2); ctx.fill();
    for (const q of [0.25, 0.5, 0.75]) {
      const yy = y0 + h * (1 - q);
      ctx.fillStyle = 'rgba(58,48,38,.35)'; ctx.fillRect(x - w / 2, yy - 1, w, 2);
      if (g.wide) { ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x + w / 2 + 6, yy); ctx.lineTo(x + w / 2 + 6, yy - 22 * k); ctx.stroke();
        ctx.fillStyle = S.top / H >= q ? '#ff5a4f' : '#fff'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(x + w / 2 + 6, yy - 22 * k); ctx.lineTo(x + w / 2 + 22 * k, yy - 17 * k); ctx.lineTo(x + w / 2 + 6, yy - 11 * k); ctx.closePath(); ctx.fill(); ctx.stroke(); }
    }
    if (g.wide) {   // 🏁
      const fy = y0 - 30 * k;
      for (let i = 0; i < 4; i++) for (let j = 0; j < 2; j++) { ctx.fillStyle = (i + j) % 2 ? INK : '#fff'; ctx.fillRect(x - 14 * k + i * 7 * k, fy - 10 * k + j * 9 * k, 7 * k, 9 * k); }
      ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.strokeRect(x - 14 * k, fy - 10 * k, 28 * k, 18 * k);
      ctx.font = `900 ${Math.round(16 * k)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = INK;
      const left = Math.max(0, Math.round((H - Math.max(0, S.y)) * M_PER_H));
      const t = `离塔顶 ${left} 米`, tw = ctx.measureText(t).width + 28 * k, ty = y0 + h + 40 * k;
      ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 3; Play.roundRect(ctx, x - tw / 2, ty - 16 * k, tw, 32 * k, 16 * k); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.fillText(t, x, ty + 1);
    }
    const gh = ghost();
    if (gh != null) {
      const gy = y0 + h * (1 - gh);
      ctx.globalAlpha = 0.55; ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.arc(x - w / 2 - 16 * k, gy, 12 * k, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); ctx.setLineDash([]);
      ctx.font = `${Math.round(13 * k)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = INK; ctx.fillText('👻', x - w / 2 - 16 * k, gy + 1); ctx.globalAlpha = 1;
      if (!S.ghostTold && f > gh && f > 0.1) { S.ghostTold = true; sh.toast('超过上次的自己！👻', 'good'); }
    }
    for (const p of others()) {
      const m = mates[p.id]; if (!m) continue;
      const py = y0 + h * (1 - Math.min(1, Math.max(0, m.y || 0) / H));
      ctx.fillStyle = (ROLE[p.role] || ROLE.sky)[0]; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(x - w / 2 - 16 * k, py, 13 * k, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.font = `900 ${Math.round(12 * k)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText((m.done ? '🏁' : '') || p.name.slice(0, 1), x - w / 2 - 16 * k, py + 1);
    }
    bee(x, y0 + h * (1 - f), 0.42 * k * (g.wide ? 1.1 : 0.7), {pose: 'up', role: myRole()});
  }

  // ------------------------------------------------------------ 左边 / 上面：关卡、用时、蜂蜜、连跳、题板；题板的题
  function buildHud() {
    if (!hudEl) { hudEl = document.createElement('div'); sh.canvas.parentNode.insertBefore(hudEl, sh.hud.nextSibling); }
    hudEl.className = 'jp-layer' + (c && c.net ? ' mp' : '');
    const n = (S.lv.gates || []).length;
    hudEl.innerHTML = `<div class="jp-side">
        <div class="jp-card jp-where"><small>${esc((S.lv.world && S.lv.world.name) || '')} · 第 ${S.lv.n || 1} 关</small><b>${esc((S.lv.name || '').split(' · ')[1] || '')}</b></div>
        <div class="jp-card jp-clock"><span><small>用时</small><b class="jp-time">0:00</b></span><span class="jp-was"></span></div>
        <div class="jp-row"><div class="jp-card"><small>蜂蜜</small><b class="jp-honey">🍯 0</b></div><div class="jp-card"><small>连跳</small><b class="jp-combo">×0</b></div></div>
        <div class="jp-card jp-gates"><small>题板</small><span class="jp-stars">${'<i></i>'.repeat(n)}</span></div>
      </div>
      <div class="jp-q"><div class="jp-topic"></div><div class="jp-text"><span></span><button type="button" class="jp-say" aria-label="再听一遍">🔊</button></div><div class="jp-tip"></div></div>`;
    const $ = q => hudEl.querySelector(q);
    hud = {time: $('.jp-time'), was: $('.jp-was'), honey: $('.jp-honey'), combo: $('.jp-combo'), stars: [...hudEl.querySelectorAll('.jp-stars i')],
           q: $('.jp-q'), topic: $('.jp-topic'), text: $('.jp-text span'), tip: $('.jp-tip'), say: $('.jp-say'), last: ''};
    hud.say.onclick = () => { const g = S.gate; if (g && g.q && g.q.item && g.q.item.say && typeof Speak !== 'undefined') Speak.play(g.q.item.say, 'en', 'word'); };
    layout(); buildQ();
  }
  function layout() {   // 电脑横屏：左边一列卡片；窄屏：顶上一行
    if (!hudEl || !stage) return;
    const g = geo(), r = sh.canvas.getBoundingClientRect(), px = r.height / stage.H;
    hudEl.classList.toggle('wide', g.wide && g.CX * px >= 250);
    hudEl.style.setProperty('--col-l', `${Math.round(g.CX * px)}px`);
    hudEl.style.setProperty('--col-w', `${Math.round(g.CW * px)}px`);
  }
  function buildQ() {
    if (!hud) return;
    const g = S.gate, q = g && g.q, show = !!(q && q.item) && (S.mode === 'gate' || S.mode === 'wait' || S.mode === 'play');
    hud.q.classList.toggle('on', show);
    hud.stars.forEach((el, i) => { el.className = i < S.gatesOk ? 'ok' : ''; });
    if (!show) return;
    const it = q.item;
    hud.topic.textContent = it.topic || '题板';
    hud.text.textContent = q.done && q.right >= 0 && q.tried ? `${it.q}  →  ${it.options[q.right]}` : it.q;
    hud.say.hidden = !it.say;
    const how = touchy ? '点一块答案板跳上去' : '← → 选，空格跳上去（也能点牌子）';
    hud.tip.textContent = q.done ? '对的那块亮了，跳上去' : q.tried ? '错的塌了，再选一个' : `${how} · 不计时`;
    hud.q.classList.toggle('long', it.q.length > 24);
  }
  function hudTick() {
    if (!hud) return;
    const t = Math.floor(seconds()), gh = S.lv.best ? Math.floor(S.lv.best) : null, k = `${t}|${S.honeyN}|${S.combo}`;
    if (hud.last === k) return;
    hud.last = k;
    const mmss = v => `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`;
    hud.time.textContent = mmss(t);
    hud.was.innerHTML = gh != null ? `<small>👻 上次</small><b>${mmss(gh)}</b>` : '';
    hud.honey.textContent = `🍯 ${S.honeyN}`;
    hud.combo.textContent = `×${S.combo}`;
  }

  // ------------------------------------------------------------ 几个人一起：谁在哪、谁先到顶
  const others = () => c && c.peers ? c.peers : [];
  function arrived() {
    const me = {secs: +seconds().toFixed(1), ok: S.gatesOk};
    if (!others().length) return report('win');
    mates[c.me.id] = Object.assign(mates[c.me.id] || {}, {done: me, y: S.lv.height});
    c.net.send({t: 'jp', x: S.x, y: S.lv.height, done: me});
    const k = place(c.me.id);
    sh.toast(k === 1 ? '🥇 第一个到塔顶！看看大家到哪了' : `第 ${k} 个到塔顶！给还在跳的人加油吧`, 'good');
    allIn();
  }
  function place(id) { const d = mates[id] && mates[id].done; return d ? 1 + Object.values(mates).filter(m => m.done && m.done.secs < d.secs).length : 0; }
  function onMate(m) {
    const p = others().find(x => x.id === m.from); if (!p) return;
    const was = mates[m.from] && mates[m.from].done;
    mates[m.from] = Object.assign(mates[m.from] || {}, {name: p.name, role: p.role, x: m.x, y: m.y}, m.done ? {done: m.done} : {});
    if (m.done && !was) { sh.toast(`${place(m.from) === 1 ? '🥇 ' : ''}${p.name} 到塔顶啦！`, 'good'); allIn(); }
  }
  function allIn() {
    if (!c.host || !S.sent) return;
    const ids = [c.me.id, ...others().map(p => p.id)];
    if (!ids.every(id => mates[id] && mates[id].done)) return;
    const d = ids.map(id => mates[id].done);
    report('win', Math.max(...d.map(x => x.secs)), d.reduce((a, x) => a + x.ok, 0));
  }
  function ranking() {
    const rows = Object.entries(mates).filter(([, m]) => m.done).sort((a, b) => a[1].done.secs - b[1].done.secs);
    return rows.map(([id, m], i) => `${['🥇', '🥈', '🥉', '4️⃣'][i]} ${+id === c.me.id ? '我' : esc(m.name)} ${m.done.secs.toFixed(1)} 秒`).join(' · ');
  }
  function report(result, teamSecs, teamOk) {
    const s = S, secs = teamSecs ?? +seconds().toFixed(1);
    if (teamSecs != null) return c.finish(result, {seconds: secs, score: teamOk}, null);
    const title = result === 'win'
      ? `🐝 到塔顶啦！<small class="jp-sub">${secs.toFixed(1)} 秒 · 题板一次答对 ${s.gatesOk}/${(s.lv.gates || []).length} · 🍯 ${s.honeyN}${s.free >= 1 ? ` · 想题的 ${Math.round(s.free)} 秒没算` : ''}</small>`
      : `⏰ 时间到，爬到 ${Math.round(s.top * M_PER_H)} 米<small class="jp-sub">下次接着跳</small>`;
    c.finish(result, {seconds: secs, score: s.gatesOk}, title);
  }

  // ------------------------------------------------------------ 鼠标、手指
  function pointerSetup(cv) {
    cv.style.touchAction = 'none';
    const at = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * stage.W / Math.max(1, r.width), (e.clientY - r.top) * stage.H / Math.max(1, r.height)]; };
    const live = () => S && !Play.input.blocked && (S.mode === 'play' || S.mode === 'gate');
    const colX = x => { const g = geo(); return (x - g.CX) / g.CW; };
    cv.addEventListener('pointermove', e => {
      if (!live()) return;
      const [x] = at(e);
      if (e.pointerType === 'mouse' && !drag) {   // 电脑：小蜜蜂跟着鼠标左右走
        if (S.mode === 'play') S.aim = Play.clamp(colX(x), 0, 1);
        else if (S.mode === 'gate' && S.gate) { const o = S.gate.opts.find(o => o.rect && x >= o.rect.x0 && x <= o.rect.x1 && openOpt(S.gate, o)); if (o && o.o !== S.sel) { S.sel = o.o; Play.sfx.play('swing'); } }
        return;
      }
      if (!drag) return;
      if (Math.abs(e.clientX - drag.cx) > 8) drag.moved = true;
      if (drag.moved && S.mode === 'play') { drag.side = 0; S.aim = drag.ax + (x - drag.x) / geo().CW; }   // 手指：拖多少走多少
    });
    cv.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse' && S) S.aim = null; });
    cv.addEventListener('pointerdown', e => {
      Play.sfx.unlock();
      const [x] = at(e);
      drag = {cx: e.clientX, x, ax: S ? S.x : 0, moved: false, side: 0};
      cv.setPointerCapture && cv.setPointerCapture(e.pointerId);
      if (e.pointerType !== 'mouse' && S && S.mode === 'play') { S.aim = null; const g = geo(); drag.side = x < g.CX + S.x * g.CW ? -1 : 1; }   // 按住：往那边走
    });
    cv.addEventListener('pointerup', e => {   // 题板上：点答案牌就跳上去
      const d = drag; drag = null;
      if (e.pointerType !== 'mouse' && S && S.mode === 'play') S.aim = null;
      if (!d || d.moved || !live() || S.mode !== 'gate') return;
      const [x, y] = at(e), g = S.gate;
      const o = g && g.opts.find(o => o.rect && x >= o.rect.x0 - 8 && x <= o.rect.x1 + 8 && y >= o.rect.y0 - 8 && y <= o.rect.y1 + 8);
      if (o && openOpt(g, o)) { S.sel = o.o; S.x = S.x + (o.x - S.x) * 0.5; hop(); }
    });
    cv.addEventListener('pointercancel', () => { drag = null; });
    addEventListener('resize', layout);
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = Play.stage(sh.canvas, {height: 900, minW: 420, maxW: 2400});
    ctx = stage.ctx; stage.onResize = layout;
    S = fresh(null, 'sun');
    pointerSetup(sh.canvas);
    Play.loop(update, render).start();
  }
  function ready() {
    S = fresh(S && S.mode !== 'preview' ? S.lv : null, 'sun'); fx.parts = []; fx.texts = []; hud = null; mates = {};
    if (hudEl) hudEl.innerHTML = '';
  }
  function begin(cx) {
    c = cx; mates = {}; sendT = 0;
    if (cx.net) { cx.net.off('jp'); cx.net.on('jp', onMate); }
    S = fresh(cx.level && cx.level.plats ? cx.level : null, cx.me && cx.me.role);
    S.limit = Math.max(30, Math.min(600, (cx.match && !cx.match.unlimited && cx.match.seconds_left) || 600));
    S.mode = 'play';
    Play.input.clear();
    buildHud(); hudTick();
    sh.toast(others().length ? '同一座塔，看谁先到塔顶！' : S.lv.best ? `上次 ${S.lv.best.toFixed(1)} 秒，👻 在右边陪你跳` : '往上跳！踩弹簧飞得更高', 'good');
  }

  const rules = [
    '小蜜蜂一直在往上弹；<b>← →</b> 按住左右走，<b>鼠标</b>：小蜜蜂跟着鼠标走；<b>手指</b>：左右拖，或者按住小蜜蜂左边 / 右边',
    '从左边出去会从右边进来。踩到<b>弹簧</b>飞得特别高',
    '背景一直在往下沉，<b>掉出屏幕底边</b>就倒下，答对 1 道就起来',
    '<b>题板</b>：跳上软云就坐下来想题，上面每块板立着一个答案。<b>← →</b> 选、<b>空格</b>跳上去，也可以直接<b>点答案牌</b>。踩对了板子下面弹出<b>超级弹簧</b>，一下飞过云带；错了板子塌下去，回云上再选',
    '移动的板、一踩就碎的板、一闪一闪的板、风，一个世界多一样',
    '最快到 <b>🏁 塔顶</b>就赢；想题的前 8 秒不算时间，右边的 👻 是上次的自己',
    '几个人一起：同一座塔各跳各的，别人是半透明的、碰不到你，<b>谁先到塔顶谁第一</b>，大家都到才过关',
  ];

  PlayShell.init({
    quiz: 'revive', tempo: 126, rules, moreHint: '一起比谁先到塔顶',
    keys: '电脑：← → 或 A D 按住走，鼠标移到哪小蜜蜂跟到哪；题板上 ← → 选答案，空格 / ↑ / 回车跳上去，也能点答案牌',
    input: {left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], jump: ['ArrowUp', 'w', ' ', 'Enter']},
    revive: {verb: '接着跳', done: '🎉 接着跳！'},
    setup: boot, ready, begin,
    end() { if (S) S.mode = S.mode === 'win' ? 'win' : 'done'; if (hudEl) hudEl.innerHTML = ''; },
    verdict(res, result) {
      if (result !== 'win' || !others().length) return {};
      const k = place(c.me.id);
      return {title: `${k === 1 ? '🥇 我第一个到塔顶！' : `🎉 大家都到塔顶了，我第 ${k} 个`}<small class="jp-sub">${ranking()}</small>`};
    },
    onRevive() {   // 起来：站在一朵救援云上，镜头停 2 秒
      const s = S; if (!s) return;
      s.mode = 'play'; s.rescue = {y: s.cam + 0.3, t: 3.5}; s.y = s.rescue.y; s.vy = V_JUMP; s.vx = 0; s.aim = null; s.hold = WAIT_REVIVE; s.squash = 1;
      Play.input.clear();
    },
    pause(on) { if (on) { Play.input.clear(); drag = null; } },
  });
})();

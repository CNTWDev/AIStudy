/* 左右飞：小蜜蜂在两面墙之间来回飞，点一下（空格 / ↑ / 鼠标 / 手指点屏幕）往上扑，不点就往下掉；碰一下墙掉头 = 走一步，碰满就过关。
   关卡（app/arena/games/fly.py）：rows[i] 是碰完第 i 下以后对面墙上的刺（9 格，s 刺、. 空），整面 q 是题墙；
     slide / blink 是会上下滑的刺、一闪一闪的刺占几面墙，ball 是中间飘着的刺球多快，wind 是一阵阵的风多大。
   刺不是一下长满：碰完墙，对面的刺在你飞过去的路上慢慢冒出来（GROW 秒），刚碰过的那面缩回去，看得见口子在哪。
   题墙：快到题墙时小蜜蜂在墙边的泡泡里停住，题写在上面，对面墙长满藤、开 2～4 朵答案花；看好了点一下才出发。
     这一趟上下两排刺变成软云（碰了弹回来），飞得慢、重力小，屋里有个黄圈标出「照现在这样飞会撞到哪」。
     撞到哪朵花就是选了哪个（记一次作答）：对了花开、金色护盾 SHIELD 下（迎面的刺撞碎，碎一根 +1 蜂蜜）；
     错了花蔫、软软弹回去，再飞一趟再选；连错两次对的那朵亮起来、题上写出答案，照样过（没有护盾）。
   碰到刺倒下：外壳盖上题目，和所有游戏一样答对 1 道就起来（REVIVE_NEED），起来以后在屋子中间的泡泡里，刺缩回去，点一下（或 2.5 秒后）再飞。
   路上：口子附近的蜂蜜、「一口气」（连着碰墙没倒下）、花路（右边一格一格点亮，🌼 是题墙）。纪录比蜂蜜。
   单位：屋子高 = 1，屋子宽 RW；电脑、手机手感一样。设计：docs/左右飞-讨论稿（项目文件），设计稿 ui-2026-10-31-fly/。
   几个人一起（最多 4 个）：同一关各飞各的屋子，每隔一会儿告诉大家我碰到第几下、在哪（fy），宽屏右边小窗画出大家；
   碰满了告诉大家我的用时（fy done），谁先碰满谁第一；大家都碰满了由开房间的人报过关（蜂蜜、题墙一次撞对都加起来）。 */
(function () {
  const INK = (window.LOOK && window.LOOK.ink) || '#3a3026';
  const FONT = '"Baloo 2", "PingFang SC", system-ui, sans-serif';
  const RW = 0.68, TS = 0.045, SLOTS = 9, SL = (1 - 2 * TS) / SLOTS, DEP = 0.06, BR = 0.032;   // 屋宽、上下刺带、一格多高、墙刺多深、小蜜蜂多大
  const G = 1.6, FLAP = -0.62, VMAX = 1.1, GATE_SLOW = 0.6;   // 重力、扑一下的速度（扑高约 0.12）、最快掉多快；题墙那一趟横着慢多少
  const GROW = 0.5, SHIELD = 3, RESCUE_S = 2.5;
  const BODY = {sun: ['#ffcf2e', '#f0a800'], berry: ['#ff7a8a', '#e24a5f'], mint: ['#6fe0b0', '#2fb886'], sky: ['#7cc4ff', '#3a8fe0']};
  const WALL = {day: ['#ffc21a', '#e8a300'], candy: ['#7fdcc0', '#4fbf9f'], sunset: ['#ff9152', '#e06a2e'], night: ['#7aa7ff', '#4f7ee6'], lava: ['#6b5650', '#45352f']};
  const SPK = {day: ['#eef2f8', '#b9c4d4'], candy: ['#fff4fb', '#d8b2e6'], sunset: ['#fff0de', '#d99a70'], night: ['#dfe9ff', '#8ea6e6'], lava: ['#7a645c', '#45352f']};
  const PETAL = [['#ff9fc4', '#f06fa0'], ['#ffd06a', '#f5a623'], ['#9fd4ff', '#5aa9f0'], ['#c9b3ff', '#9b7bf0']];

  let sh = null, stage = null, ctx = null, c = null, hud = null, hudEl = null;
  const fx = new Play.FX();
  let S = null, clock = 0;
  const touchy = matchMedia('(pointer: coarse)').matches;
  let mates = {}, sendT = 0;   // 一起玩的人：id → {name, role, n, x, y, d, m, done: {secs, honey, ok}}

  // ------------------------------------------------------------ 关卡 → 一面面墙（滑的、闪的、蜂蜜按种子摆，每次一样）
  function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function build(lv) {
    const r = rng(lv.seed || 1);
    return lv.rows.map((s, i) => {
      const gate = s === 'q'.repeat(SLOTS), cells = gate ? [] : [...s].map(ch => ch === 's');
      let best = [0, 0];   // 最长的口子，蜂蜜放在它中间
      for (let j = 0; j < SLOTS;) { if (cells[j] === false) { let k = j; while (k < SLOTS && !cells[k]) k++; if (k - j > best[1]) best = [j, k - j]; j = k; } else j++; }
      return {i, gate, cells, slide: !gate && r() < (lv.slide || 0) ? 0.5 + r() * 0.6 : 0, blink: !gate && r() < (lv.blink || 0), phase: r() * 6,
              honey: !gate && r() < 0.8 ? TS + (best[0] + best[1] / 2) * SL : -1};
    });
  }
  const DEMO = {rows: ['s..s...ss', '.ss...s..', 's...ss..s', 'ss..s...s'], walls: 4, gates: 0, cross: 1.7, seed: 7};

  function fresh(lv, avatar) {
    lv = lv || DEMO;
    return {mode: 'preview', lv, rows: build(lv), avatar: avatar || 'sun', x: RW / 2, y: 0.45, vy: 0, dir: 1,
            n: 0, walls: {L: wall(), R: wall()}, gate: null, soft: 0, honey: 0, combo: 0, best: 0, shield: 0, gatesOk: 0, falls: 0,
            squash: 0, flapT: 0, trail: [], trailT: 0, item: null, rescueT: 0, ball: lv.ball ? {x: RW / 2, y: 0.5, vx: lv.ball} : null, ballOff: 0,
            limit: 600, winT: 0, sent: false, half: false, pick: null};
  }
  function wall() { return {row: null, k: 0, grow: 0, smashed: new Set(), honey: -1, took: false}; }
  const theme = () => (S.lv.world && S.lv.world.scene) || 'day';
  const side = d => d > 0 ? 'R' : 'L';
  const gateOn = () => S.gate && !S.gate.passed ? S.gate : null;   // 正在过的题墙（过了以后花还开一会儿，不算）
  const vx = () => RW / (S.lv.cross || 1.7) * (gateOn() ? GATE_SLOW : 1);
  const grav = () => G * (gateOn() ? 0.5 : 1);
  const flapV = () => FLAP * (gateOn() ? 0.72 : 1);
  const windNow = () => !S.lv.wind || gateOn() ? 0 : S.lv.wind * Math.max(-1, Math.min(1, Math.sin(clock * 0.5 + 1) * 2.2)) * (Math.abs(Math.sin(clock * 0.5 + 1)) > 0.35 ? 1 : 0);
  const myColor = () => (c && c.me && c.me.role) || 'sun';

  // ------------------------------------------------------------ 墙：长刺、缩刺
  function aim(i) {   // 碰完第 i 下：对面那面墙换成 rows[i]，正在长；刚碰过的那面缩回去
    const s = S, to = s.walls[side(s.dir)], back = s.walls[side(-s.dir)];
    back.grow = -1;
    const row = s.rows[i];
    if (!row) return;
    if (row.gate) return startGate(row, i);
    Object.assign(to, {row, k: 0, grow: 1, smashed: new Set(), honey: row.honey, took: false});
  }
  function spikeAt(w, j) {   // 这一格的刺：中心高度、多深（0 = 没有 / 缩着 / 闪掉了）
    const row = w.row;
    if (!row || !row.cells[j] || w.smashed.has(j)) return null;
    if (row.blink && ((clock + row.phase) % 2.6) > 1.7) return null;
    const off = row.slide ? Math.sin(clock * row.slide * 2 + row.phase) * SL * 0.42 : 0;
    return {y: TS + (j + 0.5) * SL + off, d: DEP * w.k};
  }
  function triHit(u, v, sp) {   // 小蜜蜂（离墙 u、高 v）碰到这根刺没有：圆和三角形
    if (sp.d < DEP * 0.6) return false;
    const h = SL * 0.46, r = BR * 0.78, pts = [[0, sp.y - h], [sp.d, sp.y], [0, sp.y + h]];
    if (u <= sp.d && Math.abs(v - sp.y) <= h * (1 - u / sp.d)) return true;
    for (let k = 0; k < 2; k++) {
      const [ax, ay] = pts[k], [bx, by] = pts[k + 1], dx = bx - ax, dy = by - ay;
      const t = Math.max(0, Math.min(1, ((u - ax) * dx + (v - ay) * dy) / (dx * dx + dy * dy)));
      if (Math.hypot(u - ax - t * dx, v - ay - t * dy) < r) return true;
    }
    return false;
  }

  // ------------------------------------------------------------ 题墙：在泡泡里停住，题来了点一下出发；撞到哪朵花就是哪个答案
  function startGate(row, i) {
    const s = S, to = side(s.dir);
    s.gate = {row, i, side: to, item: null, tried: new Set(), right: -1, done: false, busy: false, k: 1, bloom: -1, bloomT: 0};
    Object.assign(s.walls[to], {row: null, k: 0, grow: 0, honey: -1});
    s.mode = 'bubble'; s.vy = 0; s.soft = 1;
    s.pick = null;
    loadGate(s.gate);
    Play.sfx.play('pop');
  }
  async function loadGate(g) {
    try {
      g.item = await sh.gateAsk('fly' + g.i); g.k = Math.max(2, Math.min(4, (g.item.options || []).length));
      if (g.item.say && typeof Speak !== 'undefined') Speak.play(g.item.say, 'en', 'word');
    } catch (e) { g.done = true; g.right = 0; g.k = 1; sh.toast('题墙开着，直接撞过去吧', 'warn'); }
    buildQ();
  }
  const FLOWER_TOP = 0.3, FLOWER_BOT = 0.95;   // 答案花开在题墙的这一段（上面让给题）
  const flowerY = (g, o) => FLOWER_TOP + (o + 0.5) * (FLOWER_BOT - FLOWER_TOP) / g.k;
  const flowerAt = (g, y) => Math.max(0, Math.min(g.k - 1, Math.floor((y - FLOWER_TOP) / ((FLOWER_BOT - FLOWER_TOP) / g.k))));
  const openFlower = (g, o) => !g.tried.has(o) && (!g.done || o === g.right);
  async function choose(g, o) {   // 第一次撞到哪朵就是答案
    if (g.busy) return;
    g.busy = true; S.mode = 'wait'; S.vy = 0;
    const a = await sh.gateAnswer(g.item, o);
    g.busy = false;
    if (S.mode !== 'wait' || S.gate !== g) return;
    S.mode = 'play'; g.right = a.right;
    if (a.lost) { g.done = true; passGate(g, false); }   // 没答上（过期、断网）：当作看答案过，不算错
    else if (a.correct) {
      g.done = true; S.gatesOk++;
      passGate(g, true);
    } else {
      g.tried.add(o); Play.sfx.play('wrong'); fx.shake(4, 0.2);
      sh.toast('花蔫了，不是它，再飞一趟', 'warn');
      softBack();
    }
    buildQ();
  }
  function hitGate(g) {   // 撞到题墙
    const o = flowerAt(g, S.y);
    if (!g.item && !g.done) return softBack();   // 题还没来
    if (!openFlower(g, o)) { Play.sfx.play('pop'); return softBack(); }
    if (g.done) return passGate(g, false);
    if (g.right >= 0) {   // 第一次错的时候已经知道对的是哪朵了
      if (o === g.right) { g.done = true; Play.sfx.play('star'); buildQ(); return passGate(g, false); }
      g.tried.add(o); Play.sfx.play('pop');
      if (g.tried.size >= 2) { g.done = true; sh.toast('对的那朵亮起来了，撞它就过', 'warn'); } else sh.toast('还不是它，再飞一趟', 'warn');
      buildQ();
      return softBack();
    }
    choose(g, o);
  }
  function passGate(g, right) {
    const s = S, p = toPx(s.x, s.y);
    g.bloom = right ? g.right : -1; g.bloomT = 1; g.passed = true;
    if (right) {
      s.shield = SHIELD; s.honey += 3;
      Play.sfx.play('right'); Play.sfx.play('shield', 0.25);
      sh.toast(`🎉 撞对了！金色护盾 · ${SHIELD} 下`, 'good');
      fx.burst(p[0], p[1], {n: 34, colors: ['#ffc21a', '#fff', '#ff9fc4'], speed: 380, shape: 'star', size: 7});
      fx.ring(p[0], p[1], {color: '#ffc21a', width: 8});
      for (let k = 0; k < 3; k++) fx.text(p[0] - s.dir * (40 + k * 34), p[1] - 30 - k * 18, '+1 🍯', {color: '#e08a00', size: 22});
    } else sh.toast('过了！下次争取一次撞对', 'good');
    s.soft = 0;
    setTimeout(() => { if (S && S.gate === g) S.gate = null; buildQ(); }, 700);
    touch(true);
  }
  function softBack() {   // 软软弹回去（题墙那一趟，不算碰墙）
    S.dir = -S.dir; S.squash = 1; S.vy = Math.min(S.vy, 0) - 0.15;
  }
  function predict() {   // 黄圈：照现在这样（不再点）飞，撞到题墙的哪个高度
    let x = S.x, y = S.y, v = S.vy;
    const dt = 1 / 60, ax = vx(), gg = grav();
    for (let i = 0; i < 600; i++) {
      x += S.dir * ax * dt; v = Math.min(VMAX, v + gg * dt); y += v * dt;
      if (y > 1 - TS - BR) { y = 1 - TS - BR; v = -Math.abs(v) * 0.5; }
      if (y < TS + BR) { y = TS + BR; v = Math.abs(v) * 0.3; }
      if (x - BR <= 0 || x + BR >= RW) break;
    }
    return y;
  }

  // ------------------------------------------------------------ 小蜜蜂：扑、碰墙、倒下
  function flap() {
    const s = S;
    if (s.mode === 'ready' || s.mode === 'bubble' && (s.gate && (s.gate.item || s.gate.done)) || s.mode === 'rescue' && s.rescueT > 0.4) {
      if (s.mode === 'rescue') { s.walls[side(s.dir)].grow = 1; s.walls[side(-s.dir)].grow = -1; }
      s.mode = 'play';
    }
    if (s.mode !== 'play') return;
    s.vy = flapV(); s.flapT = 1;
    const p = toPx(s.x, s.y);
    fx.burst(p[0] - s.dir * 6, p[1] + 14, {n: 4, colors: ['#ffffff'], speed: 60, size: 6, grav: 40, dir: Math.PI / 2, spread: 1.2, life: 0.4});
    Play.sfx.play('tap');
  }
  function touch(viaGate) {   // 碰了一下墙（题墙撞过也算）：掉头，走一步
    const s = S;
    s.dir = -s.dir; s.n++; s.combo++; s.best = Math.max(s.best, s.combo); s.squash = 1;
    if (s.shield > 0 && !viaGate) s.shield--;
    if (!viaGate) Play.sfx.play('click');
    const p = toPx(s.x, s.y);
    if (!viaGate) fx.burst(p[0] - s.dir * 12, p[1], {n: 6, colors: ['#ffffff', '#ffe27a'], speed: 140, size: 4, grav: 0, dir: s.dir > 0 ? 0 : Math.PI, spread: 1.6, life: 0.35});
    if (s.n >= s.rows.length) return finishRun();
    if (!s.half && s.n >= s.rows.length / 2) { s.half = true; sh.toast('🌼 一半啦！', 'good'); }
    aim(s.n);
    hudTick();
  }
  function finishRun() {
    const s = S;
    s.mode = 'win'; s.vy = 0; buildQ();
    s.walls.L.grow = -1; s.walls.R.grow = -1;
    Play.sfx.play('win'); sh.toast('🌼 花路走完啦！', 'good');
  }
  function die(why) {
    const s = S, p = toPx(s.x, s.y);
    s.mode = 'dead'; s.falls++; s.combo = 0; s.vy = 0;
    Play.sfx.play('hit'); fx.shake(9, 0.35); fx.flash('rgba(255,80,90,.3)', 0.2);
    fx.burst(p[0], p[1], {n: 16, colors: ['#ffe27a', '#fff', '#ff9fc4'], speed: 240, shape: 'star', size: 6});
    if (c.quiz) c.quiz.item = null;
    c.die();
    hudTick();
  }
  function smash(w, j, sp, sd) {   // 护盾撞碎一根刺
    w.smashed.add(j); S.honey++;
    const p = toPx(sd === 'L' ? sp.d * 0.5 : RW - sp.d * 0.5, sp.y);
    Play.sfx.play('boom'); fx.shake(4, 0.15);
    fx.burst(p[0], p[1], {n: 14, colors: [SPK[theme()] ? SPK[theme()][0] : '#eef2f8', '#ffe27a', '#fff'], speed: 300, shape: 'confetti', size: 8});
    fx.text(p[0] + (sd === 'L' ? 30 : -30), p[1] - 10, '+1 🍯', {color: '#e08a00', size: 20});
  }
  function collide() {   // 刺、上下两排、刺球、蜂蜜
    const s = S;
    for (const sd of ['L', 'R']) {
      const w = s.walls[sd];
      if (!w.row || w.k < 0.6) continue;
      const u = sd === 'L' ? s.x : RW - s.x;
      if (u > DEP + BR) continue;
      for (let j = 0; j < SLOTS; j++) {
        const sp = spikeAt(w, j);
        if (!sp || !triHit(u, s.y, sp)) continue;
        if (s.shield > 0) smash(w, j, sp, sd); else return die();
      }
    }
    const top = s.y - BR < TS * 0.8, bot = s.y + BR > 1 - TS * 0.8;
    if (top || bot) {
      if (s.soft || s.shield > 0) {   // 软云 / 护盾：弹回来
        s.y = top ? TS * 0.8 + BR : 1 - TS * 0.8 - BR;
        s.vy = top ? Math.abs(s.vy) * 0.4 + 0.1 : -Math.max(0.55, Math.abs(s.vy) * 0.6);
        s.squash = 1; Play.sfx.play(s.soft ? 'pop' : 'shield');
      } else return die();
    }
    if (s.ball && s.ballOff <= 0 && !gateOn() && Math.hypot(s.x - s.ball.x, s.y - s.ball.y) < BR * 0.8 + 0.04) {
      if (s.shield > 0) { s.ballOff = 4; s.honey++; const p = toPx(s.ball.x, s.ball.y); Play.sfx.play('boom'); fx.burst(p[0], p[1], {n: 18, shape: 'confetti', speed: 300}); }
      else return die();
    }
    const w = s.walls[side(s.dir)];
    if (w.honey >= 0 && !w.took) {
      const hx = s.dir > 0 ? RW * 0.7 : RW * 0.3;
      if (Math.hypot(s.x - hx, s.y - w.honey) < BR + 0.035) {
        w.took = true; s.honey++;
        const p = toPx(hx, w.honey);
        Play.sfx.play('coin'); fx.burst(p[0], p[1], {n: 8, colors: ['#ffc21a', '#fff'], speed: 160});
        fx.text(p[0], p[1] - 12, '+1', {color: '#e08a00', size: 22});
        hudTick();
      }
    }
  }

  // ------------------------------------------------------------ 更新
  function update(dt) {
    clock += dt; fx.update(dt);
    const s = S;
    if (!s) return;
    for (const sd of ['L', 'R']) { const w = s.walls[sd]; if (w.grow) { w.k = Math.max(0, Math.min(1, w.k + w.grow * dt / (w.grow > 0 ? GROW : 0.3))); if (w.k === 0 && w.grow < 0) { w.row = null; w.grow = 0; } } }
    if (s.gate && s.gate.bloomT > 0) s.gate.bloomT = Math.max(0, s.gate.bloomT - dt);
    s.squash = Math.max(0, s.squash - dt * 5); s.flapT = Math.max(0, s.flapT - dt * 4);
    if (s.ball && s.mode !== 'dead') {
      s.ball.x += s.ball.vx * dt;
      if (s.ball.x < RW * 0.25 || s.ball.x > RW * 0.75) { s.ball.vx = -s.ball.vx; s.ball.x = Math.max(RW * 0.25, Math.min(RW * 0.75, s.ball.x)); }
      s.ball.y = 0.5 + Math.sin(clock * 0.7) * 0.18;
      s.ballOff = Math.max(0, s.ballOff - dt);
    }
    if (Play.input.take('flap')) flap();
    if (s.mode === 'preview' || s.mode === 'ready') {   // 原地扑着翅膀等
      s.y = 0.45 + Math.sin(clock * 3) * 0.02;
      if (s.mode === 'preview') { s.x = RW / 2 + Math.sin(clock * 0.9) * RW * 0.3; s.dir = Math.cos(clock * 0.9) > 0 ? 1 : -1; }
    } else if (s.mode === 'bubble' || s.mode === 'rescue') {
      s.vy = 0; s.y += (((s.mode === 'rescue' ? 0.5 : Math.max(0.3, Math.min(0.7, s.y)))) - s.y) * Math.min(1, dt * 3);
      if (s.mode === 'bubble') s.x += ((s.dir > 0 ? BR + 0.07 : RW - BR - 0.07) - s.x) * Math.min(1, dt * 4);
      if (s.mode === 'rescue' && (s.rescueT += dt) > RESCUE_S) flap();
    } else if (s.mode === 'play') {
      s.vy = Math.min(VMAX, s.vy + (grav() + windNow()) * dt);
      s.y += s.vy * dt;
      s.x += s.dir * vx() * dt;
      if ((s.trailT -= dt) <= 0) { s.trailT = 0.045; s.trail.push([s.x, s.y]); if (s.trail.length > 22) s.trail.shift(); }
      collide();
      if (s.mode === 'play' && (s.dir > 0 ? s.x + BR >= RW : s.x - BR <= 0)) {
        s.x = s.dir > 0 ? RW - BR : BR;
        const sd = side(s.dir);
        const g = gateOn();
        if (g && g.side === sd) hitGate(g);
        else if (g) softBack();   // 题墙那一趟：对面是空墙，不算碰墙
        else touch(false);
      }
    } else if (s.mode === 'wait') {
      s.y += Math.sin(clock * 30) * 0.0006;   // 贴在花上等判分
    } else if (s.mode === 'win') {
      s.winT += dt; s.x += (RW / 2 - s.x) * Math.min(1, dt * 3); s.y += (0.42 - s.y) * Math.min(1, dt * 3);
      if (Math.random() < 0.3) { const p = toPx(Math.random() * RW, 0.2); fx.burst(p[0], p[1], {n: 4, shape: 'confetti', speed: 240}); }
      if (!s.sent && s.winT > 1.4) { s.sent = true; arrived(); }
    }
    if (s.mode !== 'play' && s.mode !== 'preview') s.trail.length = Math.max(0, s.trail.length - 1);
    if (['play', 'bubble', 'wait', 'ready'].includes(s.mode) && c && c.elapsed() >= s.limit) { s.mode = 'done'; sh.toast('⏰ 时间到！', 'warn'); setTimeout(() => report('lose'), 900); }
    if (c && c.net && (sendT -= dt) <= 0 && s.mode !== 'win' && s.mode !== 'preview') {
      sendT = 0.2;
      c.net.send({t: 'fy', n: s.n, x: +s.x.toFixed(3), y: +s.y.toFixed(3), d: s.dir, m: s.mode === 'dead' ? 'dead' : s.mode === 'bubble' || s.mode === 'wait' ? 'gate' : 'fly', sh: s.shield > 0 ? 1 : 0});
    }
    hudTick();
  }

  // ------------------------------------------------------------ 画面
  function geo() {   // 屋子竖着放中间；宽屏右边是花路和大家的小窗，窄屏屋子占满、花路是右边细细一条
    const W = stage.W, H = stage.H, wide = W >= 760;
    const top = wide ? 24 : 72, bot = wide ? 24 : 16, wallW = 0.045;
    const U = Math.min(H - top - bot, (W - (wide ? 40 : 34)) / (RW + 2 * wallW));
    const boxW = U * (RW + 2 * wallW), x0 = wide ? (W - boxW) / 2 : (W - 18 - boxW) / 2;
    return {W, H, wide, U, WW: U * wallW, bx: x0, by: top + (H - top - bot - U) / 2, boxW, ox: x0 + U * wallW};
  }
  let GEO = null;
  const toPx = (x, y) => GEO ? [GEO.ox + x * GEO.U, GEO.by + y * GEO.U] : [0, 0];

  function render() {
    const s = S, g = GEO = geo(), th = theme();
    stage.begin();
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    Play.scene.sky(ctx, g.W, g.H, th);
    Play.scene.drawClouds(ctx, g.W, th, clock * 3);
    Play.scene.hills(ctx, g.W, g.H, g.H + 20, th, 0);
    ctx.fillStyle = 'rgba(255,255,255,.12)'; ctx.fillRect(0, 0, g.W, g.H);
    room(g, s, th, 1);
    if (g.wide) { track(g); minis(g); } else track(g);
    ctx.restore();
    fx.draw(ctx); fx.drawFlash(ctx, g.W, g.H);
  }
  function room(g, s, th, alpha) {   // 一间屋子（自己的）
    const {bx, by, boxW, U, WW} = g, H = U;
    ctx.save();
    ctx.globalAlpha = alpha;
    Play.roundRect(ctx, bx, by, boxW, H, 22); ctx.save(); ctx.clip();
    const t = Play.scene.themes[th] || Play.scene.themes.day;
    const gr = ctx.createLinearGradient(0, by, 0, by + H); gr.addColorStop(0, t.sky[0]); gr.addColorStop(1, t.sky[1]);
    ctx.fillStyle = gr; ctx.fillRect(bx, by, boxW, H);
    if (t.stars) { ctx.fillStyle = '#fff'; for (let i = 0; i < 40; i++) { ctx.globalAlpha = 0.3 + 0.5 * Math.abs(Math.sin(clock + i)); ctx.fillRect(bx + (i * 97.3) % boxW, by + (i * 53.1) % H, 2, 2); } ctx.globalAlpha = 1; }
    ctx.fillStyle = t.hill[0]; ctx.globalAlpha = 0.55;
    ctx.beginPath(); ctx.moveTo(bx, by + H); for (let x = 0; x <= boxW + 10; x += 10) ctx.lineTo(bx + x, by + H * 0.86 - Math.sin(x / 60) * 14 - Math.sin(x / 23) * 5); ctx.lineTo(bx + boxW, by + H); ctx.fill();
    ctx.globalAlpha = 1;
    for (const [cx, cy, k] of [[0.25, 0.3, 1], [0.72, 0.62, 0.8]]) Play.scene.cloud(ctx, bx + boxW * cx - 28 * k + Math.sin(clock * 0.2 + cy * 9) * 10, by + H * cy, 0.7 * k, 'rgba(255,255,255,.55)');
    windLines(g);
    trail(g);
    if (s.ball && s.ballOff <= 0) spikeBall(g, s.ball);
    honeyDrop(g);
    for (const sd of ['L', 'R']) wallDraw(g, sd);
    bands(g);
    if (s.gate) flowers(g, s.gate);
    if (gateOn() && s.mode === 'play' && side(s.dir) === gateOn().side) ring(g);
    bee(g, s);
    ctx.restore();
    ctx.lineWidth = 5; ctx.strokeStyle = INK; Play.roundRect(ctx, bx, by, boxW, H, 22); ctx.stroke();
    ctx.restore();
    if (s.mode === 'win') {
      ctx.font = `900 40px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.lineWidth = 8; ctx.strokeStyle = '#fff'; ctx.strokeText('🌼 花路走完啦！', bx + boxW / 2, by + H * 0.25); ctx.fillStyle = '#e08a00'; ctx.fillText('🌼 花路走完啦！', bx + boxW / 2, by + H * 0.25);
    }
    if (s.mode === 'ready' || s.mode === 'rescue' && s.rescueT > 0.4) hint(g, touchy ? '👆 点一下屏幕，往上扑' : '点一下 / 空格 / ↑，往上扑');
  }
  function hint(g, text) {
    ctx.font = `800 17px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const w = ctx.measureText(text).width + 28, x = g.bx + g.boxW / 2, y = g.by + g.U * 0.68;
    ctx.fillStyle = '#fff'; Play.roundRect(ctx, x - w / 2, y - 18, w, 36, 18); ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = INK; ctx.stroke();
    ctx.fillStyle = INK; ctx.fillText(text, x, y + 1);
  }
  function wallDraw(g, sd) {
    const s = S, th = theme(), {U, WW} = g, x = sd === 'L' ? g.bx : g.ox + RW * U, top = g.by, H = U;
    const vine = s.gate && s.gate.side === sd;
    const [c1, c2] = vine ? ['#8fd36a', '#5fae45'] : WALL[th] || WALL.day;
    ctx.save(); ctx.beginPath(); ctx.rect(x, top, WW, H); ctx.clip();
    ctx.fillStyle = c1; ctx.fillRect(x, top, WW, H);
    if (vine) {
      ctx.strokeStyle = '#4d9a38'; ctx.lineWidth = 3;
      for (let y = top + 6; y < top + H; y += U * 0.06) {
        ctx.beginPath(); ctx.moveTo(x + WW / 2, y); ctx.quadraticCurveTo(x + WW / 2 + WW * 0.4, y + U * 0.015, x + WW / 2, y + U * 0.03); ctx.quadraticCurveTo(x + WW / 2 - WW * 0.4, y + U * 0.045, x + WW / 2, y + U * 0.06); ctx.stroke();
        ctx.fillStyle = '#bdf09a'; ctx.beginPath(); ctx.ellipse(x + WW * 0.7, y + U * 0.02, WW * 0.22, WW * 0.12, -0.5, 0, Math.PI * 2); ctx.fill();
      }
    } else {
      const r = Math.max(5, WW / 3.2);
      ctx.lineWidth = 1.6; ctx.strokeStyle = c2;
      for (let row = 0; row * r * 1.5 < H + r; row++) for (let col = -1; col < 4; col++) {
        const cx = x + col * r * 1.73 + (row % 2 ? r * 0.86 : 0), cy = top + row * r * 1.5;
        ctx.beginPath(); ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r * 0.86, cy - r / 2); ctx.lineTo(cx + r * 0.86, cy + r / 2); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r * 0.86, cy + r / 2); ctx.lineTo(cx - r * 0.86, cy - r / 2); ctx.closePath();
        ctx.fillStyle = (row * 7 + col * 3) % 5 ? c1 : c2; ctx.fill(); ctx.stroke();
      }
      ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.fillRect(sd === 'L' ? x + WW - 9 : x + 3, top, 6, H);
    }
    ctx.restore();
    ctx.fillStyle = INK; ctx.fillRect(sd === 'L' ? x + WW - 3 : x, top, 3, H);
    const w = s.walls[sd];
    if (!w.row) return;
    const [f1, f2] = SPK[th] || SPK.day, face = sd === 'L' ? g.ox : g.ox + RW * U, sg = sd === 'L' ? 1 : -1;
    for (let j = 0; j < SLOTS; j++) {
      const sp = spikeAt(w, j);
      if (!sp || sp.d <= 0.001) {
        if (w.row.blink && w.row.cells[j] && !w.smashed.has(j) && w.k > 0.5) {   // 闪掉的刺：虚线影子，看得出它会回来
          const y = g.by + (TS + (j + 0.5) * SL) * U, h = SL * 0.46 * U;
          ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(58,48,38,.45)'; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(face, y - h); ctx.lineTo(face + sg * DEP * U, y); ctx.lineTo(face, y + h); ctx.stroke(); ctx.setLineDash([]);
        }
        continue;
      }
      const y = g.by + sp.y * U, h = SL * 0.46 * U, d = sp.d * U;
      ctx.beginPath(); ctx.moveTo(face, y - h); ctx.lineTo(face + sg * d, y); ctx.lineTo(face, y + h); ctx.closePath();
      ctx.fillStyle = f1; ctx.fill(); ctx.lineJoin = 'round'; ctx.lineWidth = 2.5; ctx.strokeStyle = INK; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(face + sg * d, y); ctx.lineTo(face, y + h); ctx.lineTo(face, y + h * 0.2); ctx.closePath(); ctx.fillStyle = f2; ctx.fill();
      if (th === 'lava') { ctx.fillStyle = '#ff8a3a'; ctx.beginPath(); ctx.arc(face + sg * d * 0.8, y, 3, 0, Math.PI * 2); ctx.fill(); }
      if (w.row.slide && w.k > 0.9) {   // 会滑的刺：上下两个小箭头
        ctx.strokeStyle = INK; ctx.lineWidth = 2; const ax = face + sg * (d + 12);
        ctx.beginPath(); ctx.moveTo(ax - 4, y - h * 0.6); ctx.lineTo(ax, y - h * 0.9); ctx.lineTo(ax + 4, y - h * 0.6); ctx.moveTo(ax - 4, y + h * 0.6); ctx.lineTo(ax, y + h * 0.9); ctx.lineTo(ax + 4, y + h * 0.6); ctx.stroke();
      }
    }
  }
  function bands(g) {   // 上下两排：刺，题墙那一趟变成软云
    const s = S, th = theme(), {U} = g, x0 = g.bx, w = g.boxW, [f1, f2] = SPK[th] || SPK.day;
    for (const topSide of [true, false]) {
      const y = topSide ? g.by : g.by + U, dy = (topSide ? 1 : -1) * TS * U;
      if (s.soft) {   // 软云
        const cy = y + dy * 0.85, rr = TS * U * 0.75;
        for (const [col, grow, off] of [[INK, 3, 0], ['#fff', 0, 0]]) {
          ctx.fillStyle = col; ctx.beginPath();
          for (let x = x0 - 10; x <= x0 + w + 10; x += rr * 1.3) { const k = Math.sin(x * 0.7 + clock * 1.5) * 2; ctx.moveTo(x + rr + grow, cy + off); ctx.arc(x, cy + off + k, rr + grow, 0, Math.PI * 2); }
          ctx.fill();
          ctx.fillRect(x0, topSide ? y : cy, w, Math.abs(cy - y) + (topSide ? 0 : 0));
        }
        continue;
      }
      ctx.fillStyle = INK; ctx.fillRect(x0, topSide ? y : y - 6, w, 6);
      const n = Math.ceil(w / (TS * U * 1.15)), bw = w / n;
      for (let i = 0; i < n; i++) {
        const x = x0 + i * bw;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + bw / 2, y + dy); ctx.lineTo(x + bw, y); ctx.closePath();
        ctx.fillStyle = f1; ctx.fill(); ctx.lineWidth = 2.5; ctx.lineJoin = 'round'; ctx.strokeStyle = INK; ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x + bw / 2, y + dy); ctx.lineTo(x + bw, y); ctx.lineTo(x + bw * 0.7, y); ctx.closePath(); ctx.fillStyle = f2; ctx.fill();
        if (th === 'lava') { ctx.fillStyle = '#ff8a3a'; ctx.beginPath(); ctx.arc(x + bw / 2, y + dy * 0.8, 2.6, 0, Math.PI * 2); ctx.fill(); }
      }
    }
  }
  function flowers(g, q) {   // 题墙上的答案花：半朵开在墙外，半朵在屋里
    const {U} = g, face = q.side === 'L' ? g.ox : g.ox + RW * U, sg = q.side === 'L' ? 1 : -1;
    const opts = q.item && q.item.options ? q.item.options : null, sel = S.mode === 'play' && !q.passed ? flowerAt(q, predict()) : -1;
    for (let o = 0; o < q.k; o++) {
      const R = Math.min(U * 0.062, (FLOWER_BOT - FLOWER_TOP) / q.k * U * 0.3), y = g.by + flowerY(q, o) * U;
      const dead = q.tried.has(o) || q.done && o !== q.right, glow = q.done && o === q.right, bloom = q.bloom === o && q.bloomT > 0;
      const [p1, p2] = dead ? ['#d9d3c9', '#bdb5a8'] : glow || bloom ? ['#ffd84a', '#f0a800'] : PETAL[o % 4];
      ctx.save(); ctx.translate(face + sg * R * 0.62, y);   // 花心往屋里挪一点，字整个看得见
      if (dead) ctx.rotate(sg * 0.25);
      if (glow || bloom || o === sel && !dead) {
        ctx.fillStyle = bloom ? 'rgba(255,226,122,.7)' : glow ? 'rgba(255,246,168,.85)' : 'rgba(255,255,255,.55)';
        ctx.beginPath(); ctx.arc(0, 0, R * (1.75 + (bloom ? (1 - q.bloomT) * 0.8 : 0)), 0, Math.PI * 2); ctx.fill();
        if (glow) { ctx.setLineDash([8, 6]); ctx.strokeStyle = '#ffc21a'; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(0, 0, R * 1.65, clock, clock + Math.PI * 2); ctx.stroke(); ctx.setLineDash([]); }
      }
      const pr = R * (dead ? 1.05 : 1.15 + (bloom ? 0.25 : 0));
      for (let k = 0; k < 8; k++) {
        ctx.save(); ctx.rotate(k * Math.PI / 4 + (dead ? 0 : Math.sin(clock * 2 + o) * 0.04));
        ctx.beginPath(); ctx.ellipse(0, -pr, R * 0.42, R * (dead ? 0.45 : 0.6), 0, 0, Math.PI * 2);
        ctx.fillStyle = k % 2 ? p1 : p2; ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = INK; ctx.stroke(); ctx.restore();
      }
      ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(0, 3, R, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = dead ? '#efebe4' : glow ? '#fff8d6' : '#fff'; ctx.beginPath(); ctx.arc(0, 0, R, 0, Math.PI * 2); ctx.fill();
      ctx.lineWidth = 3; ctx.strokeStyle = INK; ctx.stroke();
      const text = opts ? String(opts[o]) : q.done ? '✓' : '…';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = dead ? '#b8ae9e' : INK;
      let fs = R * (text.length > 3 ? 0.62 : 0.85);
      ctx.font = `900 ${Math.round(fs)}px ${FONT}`;
      while (ctx.measureText(text).width > R * 1.75 && fs > 9) { fs -= 1; ctx.font = `900 ${Math.round(fs)}px ${FONT}`; }
      ctx.save(); ctx.translate(sg * R * 0.18, 0); ctx.rotate(dead ? -sg * 0.25 : 0); ctx.fillText(text, 0, 1); ctx.restore();
      if (q.tried.has(o)) { ctx.strokeStyle = '#d26a5a'; ctx.lineWidth = 3.5; ctx.beginPath(); ctx.moveTo(-R * 0.55, -R * 0.4); ctx.lineTo(R * 0.55, R * 0.4); ctx.stroke(); }
      ctx.restore();
    }
  }
  function ring(g) {   // 黄圈：照现在这样飞会撞到哪
    const s = S, y = predict(), {U} = g, x = s.dir > 0 ? g.ox + RW * U - U * 0.11 : g.ox + U * 0.11, py = g.by + y * U;
    const pulse = 1 + Math.sin(clock * 8) * 0.08;
    ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.beginPath(); ctx.arc(x, py, 13 * pulse, 0, Math.PI * 2); ctx.fill();
    ctx.lineWidth = 4; ctx.strokeStyle = '#ffc21a'; ctx.stroke();
    ctx.fillStyle = '#ffc21a'; ctx.beginPath(); ctx.arc(x, py, 4.5, 0, Math.PI * 2); ctx.fill();
    ctx.setLineDash([2, 7]); ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(x + s.dir * 18, py); ctx.lineTo(x + s.dir * U * 0.06, py); ctx.stroke(); ctx.setLineDash([]); ctx.lineCap = 'butt';
  }
  function trail(g) {   // 刚才飞过的弧线：一串渐淡的小点
    const s = S, n = s.trail.length;
    ctx.fillStyle = s.shield > 0 ? '#ffe27a' : '#fff';
    s.trail.forEach(([x, y], i) => { if (i % 2) return; const t = i / n, [px, py] = toPx(x, y); ctx.globalAlpha = 0.2 + t * 0.6; ctx.beginPath(); ctx.arc(px, py, 2 + t * 3.5, 0, Math.PI * 2); ctx.fill(); });
    ctx.globalAlpha = 1;
  }
  function honeyDrop(g) {
    const s = S, w = s.walls[side(s.dir)];
    if (w.honey < 0 || w.took || s.mode === 'preview') return;
    const [x, y] = toPx(s.dir > 0 ? RW * 0.7 : RW * 0.3, w.honey);
    drop(x, y + Math.sin(clock * 4) * 3, g.U / 700);
  }
  function drop(x, y, k) {
    ctx.fillStyle = '#ffc21a'; ctx.beginPath(); ctx.moveTo(x, y - 15 * k); ctx.bezierCurveTo(x + 6 * k, y - 6 * k, x + 11 * k, y, x + 11 * k, y + 6 * k);
    ctx.arc(x, y + 6 * k, 11 * k, 0, Math.PI); ctx.bezierCurveTo(x - 11 * k, y, x - 6 * k, y - 6 * k, x, y - 15 * k); ctx.closePath();
    ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = INK; ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.beginPath(); ctx.ellipse(x - 4 * k, y + 3 * k, 2.6 * k, 4.5 * k, 0, 0, Math.PI * 2); ctx.fill();
  }
  function spikeBall(g, b) {
    const [x, y] = toPx(b.x, b.y), r = g.U * 0.03, [f1] = SPK.sunset;
    ctx.save(); ctx.translate(x, y); ctx.rotate(clock * 1.5);
    for (let j = 0; j < 10; j++) {
      const a = j * Math.PI / 5;
      ctx.beginPath(); ctx.moveTo(Math.cos(a) * r * 0.85, Math.sin(a) * r * 0.85); ctx.lineTo(Math.cos(a + 0.3) * r * 1.6, Math.sin(a + 0.3) * r * 1.6); ctx.lineTo(Math.cos(a + 0.6) * r * 0.85, Math.sin(a + 0.6) * r * 0.85); ctx.closePath();
      ctx.fillStyle = f1; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = INK; ctx.stroke();
    }
    ctx.fillStyle = '#ffb27a'; ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill(); ctx.lineWidth = 2.5; ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.7)'; ctx.beginPath(); ctx.arc(-r * 0.3, -r * 0.3, r * 0.25, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
  function windLines(g) {
    const w = windNow();
    if (!w) return;
    ctx.strokeStyle = 'rgba(255,210,168,.75)'; ctx.lineWidth = 3; ctx.lineCap = 'round';
    for (let i = 0; i < 7; i++) {
      const x = g.ox + ((i * 0.137) % 1) * RW * g.U, y = g.by + ((i * 0.31 + clock * Math.sign(w) * 0.5) % 1 + 1) % 1 * g.U;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.quadraticCurveTo(x + 10, y + Math.sign(w) * 12, x, y + Math.sign(w) * 26); ctx.stroke();
    }
    ctx.lineCap = 'butt';
  }
  function bubble(x, y, r) {
    ctx.fillStyle = 'rgba(255,255,255,.28)'; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    ctx.lineWidth = 4; ctx.strokeStyle = '#fff'; ctx.stroke(); ctx.lineWidth = 1.5; ctx.strokeStyle = 'rgba(58,48,38,.35)'; ctx.stroke();
    ctx.lineWidth = 5; ctx.strokeStyle = '#fff'; ctx.lineCap = 'round'; ctx.beginPath(); ctx.arc(x, y, r * 0.75, Math.PI * 1.15, Math.PI * 1.45); ctx.stroke(); ctx.lineCap = 'butt';
  }
  function bee(g, s) {
    const [x, y] = toPx(s.x, s.y), k = BR * g.U / 26;
    const dead = s.mode === 'dead', pose = dead || s.mode === 'bubble' || s.mode === 'rescue' || s.mode === 'wait' ? 'sit' : s.mode === 'win' ? 'cheer' : s.flapT > 0.2 ? 'fly' : 'up';
    if (s.shield > 0) {
      ctx.fillStyle = 'rgba(255,226,122,.35)'; ctx.beginPath(); ctx.arc(x, y, 44 * k, 0, Math.PI * 2); ctx.fill();
      ctx.lineWidth = 5; ctx.strokeStyle = '#ffc21a'; ctx.stroke();
      for (let i = 0; i < s.shield; i++) { const a = clock * 3 + i * Math.PI * 2 / s.shield; Play.starPath(ctx, x + Math.cos(a) * 50 * k, y + Math.sin(a) * 50 * k, 6 * k + 2, a); ctx.fillStyle = '#fff'; ctx.fill(); }
    }
    if (s.mode === 'play' && pose === 'up') {   // 速度线
      ctx.strokeStyle = 'rgba(255,255,255,.8)'; ctx.lineWidth = 3; ctx.lineCap = 'round';
      for (const [dy, l] of [[-8, 18], [6, 26], [20, 12]]) { ctx.beginPath(); ctx.moveTo(x - s.dir * 34 * k, y + dy * k); ctx.lineTo(x - s.dir * (34 + l) * k, y + dy * k); ctx.stroke(); }
      ctx.lineCap = 'butt';
    }
    const sq = s.squash * 0.25, rot = dead ? 0.35 : s.mode === 'play' ? s.dir * 0.2 + Math.max(-0.3, Math.min(0.4, s.vy * 0.35)) * s.dir : 0;
    drawBee(ctx, x, y, k, {col: myColor(), pose, rot, sx: 1 - sq, sy: 1 + sq * 0.6, dir: s.dir, dead});
    if (dead) for (let i = 0; i < 3; i++) { const a = clock * 4 + i * 2.1; Play.starPath(ctx, x + Math.cos(a) * 30 * k, y - 36 * k + Math.sin(a) * 8 * k, 6 * k + 1, a); ctx.fillStyle = '#ffe27a'; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = INK; ctx.stroke(); }
    if (s.mode === 'bubble' || s.mode === 'rescue') bubble(x, y, 46 * k);
    if (s.mode === 'bubble') {   // 头顶的小问号
      const bx = x + s.dir * 40 * k, by = y + 30 * k;
      ctx.fillStyle = '#fff'; Play.roundRect(ctx, bx - 14, by - 14, 28, 26, 9); ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = INK; ctx.stroke();
      ctx.fillStyle = INK; ctx.font = `900 17px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('?', bx, by);
    }
  }
  /* 小蜜蜂（设计稿的画法）：k 缩放（身体半径约 25k），pose up / fly / sit / cheer */
  function drawBee(cx, x, y, k, o) {
    const [c1, c2] = BODY[o.col] || BODY.sun, pose = o.pose || 'up';
    let rx = 24, ry = 28;
    if (pose === 'sit') { rx = 27; ry = 25; }
    cx.save(); cx.translate(x, y); cx.rotate(o.rot || 0); cx.scale(k * (o.sx || 1), k * (o.sy || 1));
    cx.globalAlpha *= o.alpha ?? 1;
    cx.lineJoin = 'round'; cx.lineCap = 'round'; cx.strokeStyle = INK;
    const flapA = pose === 'fly' ? -0.5 : pose === 'up' ? Math.sin(clock * 30) * 0.25 : 0;
    for (const d of [-1, 1]) {   // 翅膀
      cx.save(); cx.translate(d * 16, -ry + 2); cx.rotate(d * (0.5 + flapA));
      cx.beginPath(); cx.ellipse(0, -4, 15, 20, 0, 0, Math.PI * 2); cx.fillStyle = 'rgba(234,247,255,.95)'; cx.fill(); cx.lineWidth = 3; cx.stroke();
      cx.restore();
    }
    cx.lineWidth = 3.5;   // 触角
    for (const d of [-1, 1]) { cx.beginPath(); cx.moveTo(d * 8, -ry + 3); cx.quadraticCurveTo(d * 12, -ry - 12, d * 22, -ry - 17); cx.stroke(); cx.fillStyle = INK; cx.beginPath(); cx.arc(d * 22, -ry - 17, 5, 0, Math.PI * 2); cx.fill(); }
    cx.lineWidth = 4;
    if (pose === 'fly' || pose === 'cheer') {   // 手举起来
      for (const d of [-1, 1]) { cx.beginPath(); cx.moveTo(d * (rx - 3), -4); cx.quadraticCurveTo(d * (rx + 11), -14, d * (rx + 11), -28); cx.stroke(); cx.fillStyle = c1; cx.beginPath(); cx.arc(d * (rx + 11), -28, 4.5, 0, Math.PI * 2); cx.fill(); cx.lineWidth = 2.5; cx.stroke(); cx.lineWidth = 4; }
      for (const d of [-1, 1]) { cx.beginPath(); cx.moveTo(d * 9, ry - 6); cx.lineTo(d * 15, ry + 6); cx.stroke(); }
    } else if (pose === 'up') for (const d of [-1, 1]) { cx.beginPath(); cx.moveTo(d * 8, ry - 3); cx.lineTo(d * 10, ry + 6); cx.stroke(); }
    cx.save(); cx.beginPath(); cx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); cx.fillStyle = c1; cx.fill(); cx.clip();   // 身体 + 条纹
    cx.fillStyle = INK; cx.fillRect(-40, ry * 0.32, 80, ry * 0.28); cx.fillRect(-40, ry * 0.78, 80, 40);
    cx.fillStyle = 'rgba(255,255,255,.45)'; cx.beginPath(); cx.ellipse(-rx * 0.35, -ry * 0.55, rx * 0.35, ry * 0.22, 0, 0, Math.PI * 2); cx.fill();
    cx.globalAlpha *= 0.18; cx.fillStyle = c2; cx.beginPath(); cx.ellipse(0, ry * 0.2, rx, ry * 0.7, 0, 0, Math.PI * 2); cx.fill();
    cx.restore();
    cx.lineWidth = 3.5; cx.beginPath(); cx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); cx.stroke();
    cx.save(); cx.translate((o.dir || 0) * 2, -ry * 0.18);   // 脸：往飞的方向看一点
    const look = pose === 'sit' ? -3 : -2;
    for (const d of [-1, 1]) {
      cx.fillStyle = '#fff'; cx.beginPath(); cx.ellipse(d * 9.5, 0, 7.5, 8.5, 0, 0, Math.PI * 2); cx.fill(); cx.lineWidth = 2.5; cx.stroke();
      if (o.dead) { cx.beginPath(); cx.moveTo(d * 9.5 - 4, -4); cx.lineTo(d * 9.5 + 4, 4); cx.moveTo(d * 9.5 + 4, -4); cx.lineTo(d * 9.5 - 4, 4); cx.stroke(); }
      else { cx.fillStyle = INK; cx.beginPath(); cx.arc(d * 9.5 + 1 + (o.dir || 0) * 1.5, look + 1, 4.3, 0, Math.PI * 2); cx.fill(); cx.fillStyle = '#fff'; cx.beginPath(); cx.arc(d * 9.5 + 2.5 + (o.dir || 0) * 1.5, look - 0.5, 1.6, 0, Math.PI * 2); cx.fill(); }
      cx.fillStyle = 'rgba(255,138,138,.7)'; cx.beginPath(); cx.ellipse(d * 17, 10, 5, 3, 0, 0, Math.PI * 2); cx.fill();
    }
    cx.lineWidth = 2.5;
    if (pose === 'fly' || pose === 'cheer') { cx.beginPath(); cx.moveTo(-7, 9); cx.quadraticCurveTo(0, 19, 7, 9); cx.closePath(); cx.fillStyle = '#c4413a'; cx.fill(); cx.stroke(); }
    else if (o.dead) { cx.beginPath(); cx.arc(0, 13, 3.5, 0, Math.PI * 2); cx.stroke(); }
    else { cx.beginPath(); cx.moveTo(-6, 10); cx.quadraticCurveTo(0, 16, 6, 10); cx.stroke(); }
    cx.restore();
    cx.fillStyle = INK; cx.beginPath(); cx.moveTo(-5, ry - 1); cx.lineTo(0, ry + 9); cx.lineTo(5, ry - 1); cx.closePath(); cx.fill();   // 小刺
    cx.restore();
  }
  window.FlyArt = {bee: drawBee, state: () => S};   // 画小蜜蜂、看现在的状态（截图、调试用）

  function track(g) {   // 花路：碰一下墙点亮一格，🌼 是题墙，顶上 🏁
    const s = S, n = s.rows.length;
    if (!n || s.mode === 'preview') return;
    const wide = g.wide, x = wide ? g.bx + g.boxW + 46 : g.W - 12, y0 = g.by + (wide ? 34 : 30), h = g.U - (wide ? 52 : 40);
    const cell = h / n, r = Math.min(wide ? 10 : 6, cell * 0.46);
    ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
    Play.roundRect(ctx, x - r - 6, y0 - 6, (r + 6) * 2, h + 12, r + 6); ctx.fill(); ctx.stroke();
    for (let i = 0; i < n; i++) {
      const cy = y0 + h - (i + 0.5) * cell, on = i < s.n, gate = s.rows[i].gate;
      if (gate) {
        for (let p = 0; p < 5; p++) { ctx.save(); ctx.translate(x, cy); ctx.rotate(p * Math.PI * 2 / 5); ctx.beginPath(); ctx.ellipse(0, -r * 0.6, r * 0.38, r * 0.6, 0, 0, Math.PI * 2); ctx.fillStyle = on ? '#ffc21a' : '#ff9fc4'; ctx.fill(); ctx.lineWidth = 1.2; ctx.stroke(); ctx.restore(); }
        ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x, cy, r * 0.32, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      } else {
        ctx.beginPath(); for (let a = 0; a < 6; a++) ctx.lineTo(x + Math.sin(a * Math.PI / 3) * r * 0.85, cy - Math.cos(a * Math.PI / 3) * r * 0.85); ctx.closePath();
        ctx.fillStyle = on ? '#ffc21a' : '#f3efe7'; ctx.fill(); ctx.lineWidth = 1.6; ctx.stroke();
      }
    }
    ctx.font = `${wide ? 18 : 12}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = INK;
    ctx.fillText('🏁', x, y0 - (wide ? 20 : 15));
    for (const p of others()) {   // 一起玩的人：小圆点
      const m = mates[p.id]; if (!m) continue;
      const py = y0 + h - Math.min(n, m.n || 0) * cell;
      ctx.fillStyle = (BODY[p.role] || BODY.sun)[0]; ctx.strokeStyle = INK; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(wide ? x - r - 16 : x, py, wide ? 8 : 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();   // 名字在右边的小窗里
    }
    const my = y0 + h - Math.min(n, s.n) * cell;
    if (wide) drawBee(ctx, x + r + 22, my, 0.45, {col: myColor(), pose: 'up', dir: 0});
    else { ctx.fillStyle = (BODY[myColor()] || BODY.sun)[0]; ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(x, my, 6, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }
  }
  function minis(g) {   // 宽屏：右边一个个小窗，看大家飞到哪了
    const ps = others().filter(p => mates[p.id]);
    if (!ps.length) return;
    const k = Math.min(0.3, (g.H - 40) / ps.length / g.U - 0.06), mx = g.bx + g.boxW + 110;
    if (mx + g.boxW * k > g.W - 6) return;
    ps.forEach((p, i) => {
      const m = mates[p.id], y = 20 + i * (g.U * k + 40), mg = {wide: false, U: g.U * k, WW: g.WW * k, bx: mx, by: y + 24, boxW: g.boxW * k, ox: mx + g.WW * k};
      ctx.font = `800 14px ${FONT}`; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillStyle = INK;
      ctx.fillText(`${p.name} · ${m.n || 0} / ${S.rows.length}${m.done ? ' 🏁' : m.m === 'gate' ? ' · 在想题墙' : m.m === 'dead' ? ' · 在答题' : ''}`, mx, y + 10);
      mini(mg, m, p);
    });
  }
  function mini(mg, m, p) {   // 别人的屋子：墙、刺按他碰到第几下画，小蜜蜂画在他报的位置
    const th = theme(), {U} = mg, t = Play.scene.themes[th] || Play.scene.themes.day;
    ctx.save();
    Play.roundRect(ctx, mg.bx, mg.by, mg.boxW, U, 10); ctx.save(); ctx.clip();
    const gr = ctx.createLinearGradient(0, mg.by, 0, mg.by + U); gr.addColorStop(0, t.sky[0]); gr.addColorStop(1, t.sky[1]); ctx.fillStyle = gr; ctx.fillRect(mg.bx, mg.by, mg.boxW, U);
    const [c1] = WALL[th] || WALL.day, [f1] = SPK[th] || SPK.day;
    ctx.fillStyle = c1; ctx.fillRect(mg.bx, mg.by, mg.WW, U); ctx.fillRect(mg.ox + RW * U, mg.by, mg.WW, U);
    const row = S.rows[Math.min(S.rows.length - 1, m.n || 0)], face = (m.d || 1) > 0 ? mg.ox + RW * U : mg.ox, sg = (m.d || 1) > 0 ? -1 : 1;
    ctx.fillStyle = f1; ctx.strokeStyle = INK; ctx.lineWidth = 1.2;
    if (row && !row.gate && !m.done) for (let j = 0; j < SLOTS; j++) if (row.cells[j]) { const y = mg.by + (TS + (j + 0.5) * SL) * U, h = SL * 0.46 * U; ctx.beginPath(); ctx.moveTo(face, y - h); ctx.lineTo(face + sg * DEP * U, y); ctx.lineTo(face, y + h); ctx.closePath(); ctx.fill(); ctx.stroke(); }
    if (row && row.gate) for (let o = 0; o < 3; o++) { ctx.fillStyle = PETAL[o][0]; ctx.beginPath(); ctx.arc(face, mg.by + (0.35 + o * 0.25) * U, U * 0.05, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }
    ctx.fillStyle = f1; for (const top of [0, 1]) { const y = mg.by + top * U; ctx.fillRect(mg.bx, top ? y - TS * U : y, mg.boxW, TS * U); }
    drawBee(ctx, mg.ox + (m.x ?? RW / 2) * U, mg.by + (m.y ?? 0.5) * U, BR * U / 26, {col: p.role, pose: m.m === 'dead' || m.m === 'gate' ? 'sit' : 'up', dir: m.d, dead: m.m === 'dead'});
    if (m.sh) { ctx.strokeStyle = '#ffc21a'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(mg.ox + (m.x ?? 0) * U, mg.by + (m.y ?? 0) * U, BR * U * 1.6, 0, Math.PI * 2); ctx.stroke(); }
    if (m.m === 'dead') { ctx.fillStyle = 'rgba(58,48,38,.3)'; ctx.fillRect(mg.bx, mg.by, mg.boxW, U); }
    ctx.restore();
    ctx.lineWidth = 2.5; ctx.strokeStyle = INK; Play.roundRect(ctx, mg.bx, mg.by, mg.boxW, U, 10); ctx.stroke();
    ctx.restore();
  }

  // ------------------------------------------------------------ 上面一条：碰墙、蜂蜜、一口气；题墙的题在中间
  function buildHud() {
    if (!hudEl) { hudEl = document.createElement('div'); sh.canvas.parentNode.insertBefore(hudEl, sh.hud.nextSibling); }
    hudEl.className = 'fy-layer' + (c && c.net ? ' mp' : '');
    hudEl.innerHTML = `<div class="fy-hud"><span class="fy-side"><span class="fy-chip fy-n"></span><span class="fy-chip fy-honey"></span><span class="fy-chip fy-combo"></span><span class="fy-chip fy-shield"></span></span>
      <div class="fy-q"><div class="fy-topic"></div><div class="fy-text"><span></span><button type="button" class="fy-say" aria-label="再听一遍">🔊</button></div><div class="fy-tip"></div></div><span></span></div>`;
    const $ = q => hudEl.querySelector(q);
    hud = {n: $('.fy-n'), honey: $('.fy-honey'), combo: $('.fy-combo'), shield: $('.fy-shield'), q: $('.fy-q'), topic: $('.fy-topic'), text: $('.fy-text span'), tip: $('.fy-tip'), say: $('.fy-say'), last: ''};
    hud.say.onclick = e => { e.stopPropagation(); const g = S.gate; if (g && g.item && g.item.say && typeof Speak !== 'undefined') Speak.play(g.item.say, 'en', 'word'); };
    hud.say.style.pointerEvents = 'auto';
    buildQ();
  }
  function buildQ() {
    if (!hud) return;
    const g = gateOn(), show = !!(g && g.item) && S.mode !== 'win';
    hud.q.classList.toggle('on', show);
    if (!show) return;
    const it = g.item, tap = touchy ? '点一下屏幕' : '点一下 / 空格';
    hud.topic.textContent = `${it.topic || '题墙'} · 撞对的那朵花`;
    hud.text.textContent = g.done && g.right >= 0 && g.tried.size ? `${it.q}  →  ${it.options[g.right]}` : it.q;
    hud.say.hidden = !it.say;
    hud.tip.textContent = S.mode === 'bubble' ? `看好了再飞，不着急 · ${tap}出发` : g.done ? '对的那朵亮了，撞它就过' : g.tried.size ? '蔫了的不是它，再撞一朵' : '黄圈是会撞到的地方，点一下往上挪';
    hud.q.classList.toggle('long', it.q.length > 24);
  }
  function hudTick() {
    if (!hud) return;
    const s = S, k = `${s.n}|${s.honey}|${s.combo}|${s.shield}|${s.mode}|${s.y < 0.3}`;
    if (hud.last === k) return;
    hud.q.classList.toggle('peek', s.y < 0.3 && (s.mode === 'play' || s.mode === 'wait'));   // 小蜜蜂飞到题卡底下时题卡变淡
    const was = hud.last; hud.last = k;
    hud.n.textContent = `⬡ ${s.n} / ${s.rows.length}`;
    hud.honey.textContent = `🍯 ${s.honey}`;
    hud.combo.textContent = `一口气 ×${s.combo}`;
    hud.combo.classList.toggle('hot', s.combo >= 5);
    hud.shield.textContent = `🛡 ${s.shield}`; hud.shield.hidden = !s.shield;
    if (was && was.split('|')[4] !== s.mode) buildQ();
  }

  // ------------------------------------------------------------ 几个人一起：谁碰到第几下了、谁先碰满
  const others = () => c && c.peers ? c.peers : [];
  const seconds = () => c ? c.elapsed() : 0;
  function arrived() {   // 我碰满了：一个人玩直接过关；几个人一起就告诉大家，等大家都碰满
    const me = {secs: +seconds().toFixed(1), honey: S.honey, ok: S.gatesOk};
    if (!others().length) return report('win');
    mates[c.me.id] = Object.assign(mates[c.me.id] || {}, {done: me, n: S.rows.length});
    c.net.send({t: 'fy', n: S.rows.length, done: me});
    const k = place(c.me.id);
    sh.toast(k === 1 ? '🥇 第一个碰满！看看大家飞到哪了' : `第 ${k} 个碰满！给还在飞的人加油吧`, 'good');
    allIn();
  }
  function place(id) { const d = mates[id] && mates[id].done; return d ? 1 + Object.values(mates).filter(m => m.done && m.done.secs < d.secs).length : 0; }
  function onMate(m) {
    const p = others().find(x => x.id === m.from); if (!p) return;
    const was = mates[m.from] && mates[m.from].done;
    mates[m.from] = Object.assign(mates[m.from] || {}, {name: p.name, role: p.role, n: m.n, x: m.x, y: m.y, d: m.d, m: m.m, sh: m.sh}, m.done ? {done: m.done} : {});
    if (m.done && !was) { sh.toast(`${place(m.from) === 1 ? '🥇 ' : ''}${p.name} 碰满啦！`, 'good'); allIn(); }
  }
  function allIn() {   // 大家都碰满了：开房间的人报过关
    if (!c.host || !S.sent) return;
    const ids = [c.me.id, ...others().map(p => p.id)];
    if (!ids.every(id => mates[id] && mates[id].done)) return;
    const d = ids.map(id => mates[id].done);
    report('win', Math.max(...d.map(x => x.secs)), d.reduce((a, x) => a + x.honey, 0), d.reduce((a, x) => a + x.ok, 0));
  }
  function ranking() {
    const rows = Object.entries(mates).filter(([, m]) => m.done).sort((a, b) => a[1].done.secs - b[1].done.secs);
    return rows.map(([id, m], i) => `${['🥇', '🥈', '🥉', '4️⃣'][i]} ${+id === c.me.id ? '我' : esc(m.name)} 🍯${m.done.honey}`).join(' · ');
  }
  function report(result, teamSecs, teamHoney, teamOk) {
    const s = S;
    if (teamSecs != null) return c.finish(result, {seconds: teamSecs, score: teamHoney, gates: teamOk}, null);
    const best = s.lv.best, title = result === 'win'
      ? `🌼 花路走完啦！<small class="fy-sub">🍯 ${s.honey}${best != null ? (s.honey > best ? ` · 新纪录，多了 ${s.honey - best} 滴` : ` · 纪录 ${best}`) : ''} · 题墙一次撞对 ${s.gatesOk}/${s.lv.gates || 0} · 一口气最多 ×${s.best}</small>`
      : `⏰ 时间到，碰到第 ${s.n} 下<small class="fy-sub">下次接着飞</small>`;
    c.finish(result, {seconds: +seconds().toFixed(1), score: s.honey, gates: s.gatesOk}, title);
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = Play.stage(sh.canvas, {height: 720, minW: 340, maxW: 2000});
    ctx = stage.ctx;
    S = fresh(null, sh.data.avatar);
    sh.canvas.style.touchAction = 'none';
    sh.canvas.addEventListener('pointerdown', e => {   // 点屏幕任何地方 = 往上扑
      Play.sfx.unlock();
      if (!S || Play.input.blocked || e.button > 0) return;
      e.preventDefault(); flap();
    });
    const loop = Play.loop(update, render);
    loop.start();
  }
  function ready() {
    S = fresh(S && S.mode !== 'preview' ? S.lv : null, sh.data.avatar); fx.parts = []; fx.texts = []; hud = null; mates = {};
    if (hudEl) hudEl.innerHTML = '';
  }
  function begin(cx) {
    c = cx; mates = {}; sendT = 0;
    if (cx.net) { cx.net.off('fy'); cx.net.on('fy', onMate); }
    S = fresh(cx.level && cx.level.rows ? cx.level : null, (cx.match && cx.match.avatar) || sh.data.avatar);
    S.limit = Math.max(30, Math.min(600, (cx.match && !cx.match.unlimited && cx.match.seconds_left) || 600));
    S.mode = 'ready';
    aim(0);
    Play.input.clear();
    buildHud(); hudTick();
    sh.toast(others().length ? '各飞各的屋子，看谁先碰满！' : S.lv.best != null ? `纪录 🍯 ${S.lv.best}，这次多吃几滴！` : '点一下往上扑，从刺的口子钻过去！', 'good');
  }

  const rules = [
    '小蜜蜂在两面墙中间来回飞：<b>点一下</b>（空格 / ↑ / 鼠标 / 手指点屏幕）往上扑，不点就往下掉',
    '碰到墙就掉头，<b>碰一下墙走一步</b>，碰满就过关；对面墙上的刺会慢慢冒出来，留一个<b>口子</b>给你钻',
    '上下两排刺、墙上的刺别碰，碰到就倒下，<b>答对 1 道</b>就起来',
    '<b>题墙</b>：小蜜蜂先在泡泡里停住，看好了题再点一下出发；撞到哪朵<b>答案花</b>就是选了哪个。<b>黄圈</b>是照现在这样飞会撞到的地方',
    '撞对了得<b>金色护盾</b>（3 下）：迎面的刺直接撞碎；撞错了花会蔫，弹回来再选一次，不扣东西',
    '口子附近有<b>蜂蜜</b>，比的是这一关吃到多少滴；几个人一起各飞各的屋子，<b>谁先碰满谁第一</b>，大家都碰满才过关',
  ];

  PlayShell.init({
    quiz: 'revive', tempo: 126, rules, moreHint: '一起比谁先碰满',
    keys: '电脑：空格、↑、W、回车或者点鼠标都是往上扑；P 暂停',
    input: {flap: [' ', 'ArrowUp', 'w', 'Enter']},
    revive: {verb: '接着飞', done: '🎉 接着飞！'},
    setup: boot, ready, begin,
    end() { if (S) S.mode = S.mode === 'win' ? 'win' : 'done'; if (hudEl) hudEl.innerHTML = ''; },
    verdict(res, result) {
      if (result !== 'win' || !others().length) return {};
      const k = place(c.me.id);
      return {title: `${k === 1 ? '🥇 我第一个碰满！' : `🎉 大家都碰满了，我第 ${k} 个`}<small class="fy-sub">${ranking()}</small>`};
    },
    onRevive() {   // 起来：屋子中间的泡泡里，刺缩回去，点一下（或一会儿）再飞
      const s = S; if (!s) return;
      s.mode = 'rescue'; s.rescueT = 0; s.x = RW / 2; s.y = 0.5; s.vy = 0; s.trail = [];
      for (const sd of ['L', 'R']) if (s.walls[sd].row) { s.walls[sd].grow = -1; }
      const to = s.walls[side(s.dir)], row = s.rows[s.n];
      if (row && !row.gate) Object.assign(to, {row, k: 0, grow: 0, smashed: new Set()});
      Play.input.clear();
    },
    pause(on) { if (on) Play.input.clear(); },
  });
})();

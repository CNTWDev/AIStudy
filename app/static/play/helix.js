/* 向下跳：一根柱子上一层层圆盘，← → 转柱子（鼠标、手指左右拖也行），让一直在弹的球从洞里一层层掉下去，躲开红色的刺，最快掉到底。
   关卡（app/arena/games/helix.py）：rows 一层一串 12 个字母，n 普通、g 洞、s 刺、q 题门；spin / blink 是会自己转的层、一闪一闪的刺占多少。
   题门：球落到这一层上就停住、开门（之前掉不下去），门上按选项个数开 2～4 个洞，洞口立着答案牌；题写在上面。
     停下来慢慢选，不催：← → 一下换一个答案（转到它的洞上），↓ / 空格 / 回车掉下去；鼠标、手指直接点答案牌也行（转过去就掉）。
     第一次掉进哪个洞就是答案（记一次作答）：对了变火球；错了洞口有盖子把球弹回来，那块牌子变灰，再选；
     连错两次，对的那个洞亮起来、题上写出答案，照样掉下去过关（不变火球）。想题的前 GATE_FREE 秒不算进用时。
   连穿 3 层不落地也变火球：火球砸得碎下一层（普通格、刺都行，题门砸不碎）。
   掉到刺上倒下：外壳盖上题目，和所有游戏一样答对 1 道就起来（REVIVE_NEED），起来以后刺软一会儿。
   画面：2.5D 斜着往下看，圆盘是椭圆，球一直在正前方；镜头只往下走，球在上面三分之一，下面两层整层看得见；
     球正下方有个落点影子（跳过对着洞的层，画在真正会落到的那一层上），落到刺上就变红，来得及转开。
   几个人一起（最多 4 个）：同一座塔各跳各的（转动、题都是自己的），每隔一会儿告诉大家我掉到第几层（hx），
   右边的深度条上画出每个人；到底了告诉大家我的用时（hx done），谁先到底谁第一，在终点看着大家、发表情加油；
   大家都到底了由开房间的人报过关（用时取最后一个人的，题门一次答对的加起来）。 */
(function () {
  const INK = (window.LOOK && window.LOOK.ink) || '#2b2340';
  const FONT = '"Baloo 2", "PingFang SC", system-ui, sans-serif';
  const N = 12, SEC = Math.PI * 2 / N, BALL_A = Math.PI / 2;   // 一层几格、一格多大角度、球在哪个角度（正前方）
  const G = 11, BOUNCE = 3.1, VMAX = 8;                        // 重力、弹起来的速度、最快掉多快（层 / 秒）
  const BR = 0.13;                                             // 球的半径（层间距的几分之一）
  const FIRE_AT = 3, GATE_FREE = 8, SAFE_S = 1.5;
  const ROLE_COLOR = {sun: '#ffc93c', berry: '#ff5d7a', mint: '#3fbf8f', sky: '#4d96ff'};   // 球的颜色跟角色走（helix.py roles），几个人一起分得清
  const SPIN = 4.2, SPIN_MAX = 7.5, SPIN_ACC = 40;             // 按住键转多快（弧度 / 秒）：点一下转得准，按住半秒越转越快

  let sh = null, stage = null, ctx = null, c = null, hud = null;
  const fx = new Play.FX();
  let S = null, clock = 0, drag = null, shift = false, hudEl = null;
  const touchy = matchMedia('(pointer: coarse)').matches;
  let mates = {}, sendT = 0;   // 一起玩的人：id → {name, role, next, done: {secs, ok}}

  // ------------------------------------------------------------ 关卡 → 一层层圆盘（自己转的层、闪的刺、蜂蜜按种子摆，每次一样）
  function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function build(lv) {
    const r = rng(lv.seed || 1);
    return lv.rows.map((s, i) => {
      const gate = s === 'q'.repeat(N), cells = [...s].map(ch => gate ? 'n' : ch);
      const holes = cells.map((ch, j) => ch === 'g' ? j : -1).filter(j => j >= 0);
      return {i, gate, cells, ang: r() * Math.PI * 2, broken: false,
              spin: !gate && r() < (lv.spin || 0) ? (r() < 0.5 ? -1 : 1) * (0.35 + r() * 0.35) : 0,
              blink: !gate && cells.includes('s') && r() < (lv.blink || 0), phase: r() * 3,
              honey: !gate && holes.length && r() < 0.6 ? holes[Math.floor(r() * holes.length)] : -1,
              q: null};   // 题门：{item, holeOf[格]→第几个选项, tried, right, done, armed, free}
    });
  }
  const DEMO = {rows: ['nnnggnnnnsnn', 'nsnnnnggnnnn', 'qqqqqqqqqqqq', 'nnnnnsnggnnn', 'ngnnnsnnnnsn', 'nnsnngnnnnnn'], seed: 7};

  function fresh(lv, avatar) {
    lv = lv || DEMO;
    return {mode: 'preview', lv, rows: build(lv), avatar: avatar || 'mint', rot: 0, w: 0, hold: 0, snap: null, dropAfter: false,
            y: 0.45, vy: 0, next: 0, cam: 1 - BR - 0.6, lift: 0, streak: 0, fire: false, safe: 0, squash: 0,
            honey: 0, gatesOk: 0, gatesDone: 0, free: 0, falls: 0, limit: 600, winT: 0, sent: false, flags: 0, ghostTold: false};
  }
  const theme = () => (S.lv.world && S.lv.world.scene) || 'day';
  const mod = (a, m) => ((a % m) + m) % m;
  const hex6 = h => /^#[0-9a-f]{3}$/i.test(h) ? '#' + [...h.slice(1)].map(x => x + x).join('') : h;   // 主题里可能写 #fff
  const cellAt = (row, a) => Math.floor(mod(a - S.rot - row.ang, Math.PI * 2) / SEC) % N;   // 角度 a 上是第几格
  const ballCell = row => cellAt(row, BALL_A);
  const spikeOut = row => !row.blink || mod(clock + row.phase, 2.8) < 1.6;
  const openOpt = (q, o) => q.armed && o >= 0 && !q.tried.has(o) && (!q.done || o === q.right);   // 这个答案的洞还能掉进去

  // ------------------------------------------------------------ 题门：落到这一层上才开门；门上开几个洞看选项有几个
  async function loadGate(row) {
    if (row.q || !c) return;
    row.q = {item: null, holeOf: Array(N).fill(-1), tried: new Set(), right: -1, done: false, armed: false, free: 0, busy: false};
    try { openGate(row, await sh.gateAsk('helix' + row.i)); } catch (e) { row.q.done = true; row.q.item = null; sh.toast('题门开着，直接掉下去吧', 'warn'); openGate(row, null); }
  }
  function openGate(row, it) {
    const g = row.q, k = it && it.options ? Math.min(4, it.options.length) : 1;
    g.item = it;
    const off = mod(ballCell(row) - 2, N);   // 第一个洞开在球旁边两格，球正下方是实的：不会还没看题就掉下去
    for (let j = 0; j < k; j++) { const s = off + Math.floor(j * N / k); g.holeOf[mod(s, N)] = j; g.holeOf[mod(s + 1, N)] = j; }
    if (!it) g.right = 0;
    if (it && it.say && typeof Speak !== 'undefined' && g.armed) Speak.play(it.say, 'en', 'word');
    buildQ();
  }
  async function choose(row, j) {   // 第一次掉进哪个洞就是答案
    const g = row.q;
    if (g.busy) return;
    g.busy = true; S.mode = 'wait'; S.vy = 0;
    const a = await sh.gateAnswer(g.item, j);
    g.busy = false;
    if (S.mode !== 'wait') return;
    S.mode = 'play'; g.right = a.right;
    S.gatesDone++;
    if (a.lost) { g.done = true; pass(row); }   // 没答上（过期、断网）：当作看答案过，不算错
    else if (a.correct) {
      g.done = true; S.gatesOk++;
      Play.sfx.play('right'); fireOn('🎉 答对了，变火球！');
      pass(row);
    } else {
      g.tried.add(j); Play.sfx.play('wrong'); fx.shake(4, 0.2);
      sh.toast('不是它，再选一个', 'warn');
      bounce(row);
    }
    buildQ();
  }
  function wrongAgain(row, j) {   // 已经知道对的是哪个了（第一次答错时服务器告诉的）：再错一次就亮出来
    const g = row.q;
    g.tried.add(j); Play.sfx.play('pop');
    if (g.tried.size >= 2) { g.done = true; sh.toast('对的答案亮起来了，看看再掉下去', 'warn'); }
    else sh.toast('还不是它，再选一个', 'warn');
    bounce(row); buildQ();
  }
  function rest(row) {   // 落到题门上：球停住不弹了，开门，慢慢选
    if (!row.q) loadGate(row);
    S.mode = 'gate'; S.y = row.i + 1 - BR; S.vy = 0; S.squash = 1; S.streak = 0; S.w = 0;
    if (!row.q.armed) { row.q.armed = true; if (row.q.item && row.q.item.say && typeof Speak !== 'undefined') Speak.play(row.q.item.say, 'en', 'word'); buildQ(); }
    Play.sfx.play('click');
  }
  function aimAt(row, o) {   // 把第 o 个答案的洞（两格宽）转到球下面，就近转
    const j0 = [...Array(N).keys()].find(j => row.q.holeOf[j] === o && row.q.holeOf[mod(j - 1, N)] !== o);
    if (j0 == null) return;
    const base = S.snap ?? S.rot, want = BALL_A - row.ang - (j0 + 1) * SEC;
    S.snap = base + mod(want - base + Math.PI, Math.PI * 2) - Math.PI; S.w = 0;
    Play.sfx.play('swing');
  }
  function aimNearest(row) {   // 拖完松手：转到最近的那个答案
    const q = row.q; if (!q || !q.item && !q.done) return;
    let best = -1, d = 9;
    for (let j = 0; j < N; j++) {
      const o = q.holeOf[j];
      if (!openOpt(q, o) || q.holeOf[mod(j - 1, N)] === o) continue;
      const e = Math.abs(mod(BALL_A - row.ang - (j + 1) * SEC - S.rot + Math.PI, Math.PI * 2) - Math.PI);
      if (e < d) { d = e; best = o; }
    }
    if (best >= 0) aimAt(row, best);
  }
  function dropGate() {   // 题门上按 ↓ / 点了答案牌：球下面是哪个答案，就掉进哪个洞
    const s = S, row = s.rows[s.next], g = row && row.q;
    s.dropAfter = false;
    if (!g || !g.item && !g.done) return;   // 题还没来
    if (s.snap != null) { s.rot = s.snap; s.snap = null; }
    const opt = g.holeOf[ballCell(row)];
    if (!openOpt(g, opt)) { Play.sfx.play('pop'); sh.toast(touchy ? '先点一个答案牌' : '先用 ← → 选一个答案', 'warn'); return; }
    s.mode = 'play'; s.vy = 1.5;
    if (g.done) return pass(row);
    if (g.right >= 0) return opt === g.right ? (g.done = true, Play.sfx.play('star'), buildQ(), pass(row)) : wrongAgain(row, opt);
    choose(row, opt);
  }

  // ------------------------------------------------------------ 球
  function bounce(row) {
    S.y = row.i + 1 - BR; S.vy = -BOUNCE; S.squash = 1;
    S.streak = 0;
    Play.sfx.play('click');
  }
  function pass(row) {
    S.next = row.i + 1; S.streak++;
    if (row.honey >= 0 && ballCell(row) === row.honey) { row.honey = -1; S.honey++; Play.sfx.play('coin'); fx.burst(S.cx, S.by, {n: 8, colors: ['#ffb21e', '#fff'], speed: 160}); }
    if (!S.fire && S.streak >= FIRE_AT) fireOn('🔥 连穿 3 层，变火球！');
    if (S.next < S.rows.length && S.rows[S.next].gate) loadGate(S.rows[S.next]);
    buildQ(); checkpoint();
  }
  function fireOn(why) { S.fire = true; Play.sfx.play('power'); sh.toast(why, 'good'); }
  function smash(row) {
    row.broken = true; S.fire = false; S.streak = 0; S.vy *= 0.6;
    Play.sfx.play('boom'); fx.shake(7, 0.25);
    const t = Play.scene.themes[theme()] || Play.scene.themes.day;
    fx.burst(S.cx, S.by + 20, {n: 30, colors: [t.hill[0], t.hill[1], '#ff4d5a', '#fff'], speed: 380, shape: 'confetti'});
    pass(row);
  }
  function hit(row) {   // 球的底碰到这一层：看下面是什么
    const j = ballCell(row), ch = row.cells[j];
    if (row.broken) return pass(row);
    if (row.gate) {   // 题门：停在门上（选错弹回来也是落回这里），选好了 dropGate 掉下去；看过答案以后正对着对的洞就直接过
      const g = row.q;
      if (S.fire) { S.fire = false; sh.toast('题门砸不碎，看题吧', 'warn'); }
      if (g && g.armed && g.done && g.holeOf[j] === g.right) return pass(row);
      return rest(row);
    }
    if (ch === 'g') return pass(row);
    if (S.fire) return smash(row);
    if (ch === 's' && spikeOut(row) && S.safe <= 0) return die(row);
    bounce(row);
  }
  function die(row) {
    S.mode = 'dead'; S.falls++; S.streak = 0; S.y = row.i + 1 - BR; S.vy = 0;
    Play.sfx.play('hit'); fx.shake(9, 0.35); fx.flash('rgba(255,80,90,.35)', 0.2);
    fx.burst(S.cx, S.by, {n: 18, colors: ['#ff4d5a', '#fff', '#ffd23f'], speed: 280});
    if (c.quiz) c.quiz.item = null;
    c.die();
  }
  function checkpoint() {   // 每 1/4 一面旗；和 👻 上次的我比一比
    const k = Math.floor(S.next / S.rows.length * 4);
    if (k <= S.flags || k >= 4) return;
    S.flags = k;
    const g = ghost();
    const ahead = g != null && S.next / S.rows.length > g;
    sh.toast(`🚩 到 ${['', '1/4', '一半', '3/4'][k]} 啦${g == null ? '' : ahead ? '，比上次的我快！' : '，👻 上次的我在前面'}`, 'good');
    Play.sfx.play('star');
  }
  const seconds = () => Math.max(0, (c ? c.elapsed() : 0) - S.free);
  const myColor = () => (c && c.me && ROLE_COLOR[c.me.role]) || Art.avatar(S.avatar).main;
  const ghost = () => S.lv.best ? Math.min(1, seconds() / S.lv.best) : null;   // 上次的我：按纪录的用时匀速往下

  // ------------------------------------------------------------ 更新
  function update(dt) {
    clock += dt; fx.update(dt);
    const s = S;
    if (!s) return;
    for (const r of s.rows) if (r.spin && s.mode !== 'dead') r.ang += r.spin * dt;
    if (s.mode === 'preview') { s.rot += dt * 0.4; bob(dt); return; }
    turn(dt);
    s.safe = Math.max(0, s.safe - dt); s.squash = Math.max(0, s.squash - dt * 5);
    if (s.mode === 'play') {
      s.vy = Math.min(VMAX, s.vy + G * dt);
      let ny = s.y + s.vy * dt;
      while (s.mode === 'play' && s.vy > 0 && s.next < s.rows.length && ny + BR >= s.next + 1) {
        const row = s.rows[s.next], before = s.next;
        s.y = row.i + 1 - BR;
        hit(row);
        if (s.next === before) { ny = s.y; break; }   // 弹起来了 / 停在门上等判分 / 倒下了
      }
      if (s.mode === 'play') s.y = ny;
      if (s.next >= s.rows.length && s.y + BR >= s.rows.length + 1) {   // 落到终点
        s.y = s.rows.length + 1 - BR; s.mode = 'win'; s.vy = 0; buildQ();
        Play.sfx.play('win'); sh.toast('🏁 到底啦！', 'good');
      }
    } else if (s.mode === 'gate') {
      s.y = s.next + 1 - BR;
    } else if (s.mode === 'wait') {
      s.y = s.next + 1 - BR + Math.sin(clock * 30) * 0.01;   // 在洞口的盖子上晃，等判分
    } else if (s.mode === 'win') {
      s.winT += dt;
      if (Math.random() < 0.3) fx.burst(Math.random() * stage.W, stage.H * 0.3, {n: 4, shape: 'confetti', speed: 240});
      if (!s.sent && s.winT > 1.4) { s.sent = true; arrived(); }
    }
    if (s.mode === 'play' || s.mode === 'gate') {
      const gt = gateHere();
      if (gt && gt.armed && !gt.done && gt.right < 0 && gt.free < GATE_FREE) { const d = Math.min(dt, GATE_FREE - gt.free); gt.free += d; s.free += d; }
      if (c && c.elapsed() >= s.limit) { s.mode = 'done'; sh.toast('⏰ 时间到！', 'warn'); setTimeout(() => report('lose'), 900); }
    }
    s.lift += ((s.mode === 'gate' || s.mode === 'wait' ? 1 : 0) - s.lift) * Math.min(1, dt * 5);   // 停在题门上：画面往下让一点，后面的答案牌不被题挡住
    const floor = Math.min(s.next, s.rows.length) + 1 - BR;   // 镜头跟着球脚下那一层走（不跟着弹上弹下），只往下走
    s.cam = Math.max(s.cam, s.cam + (Math.max(s.y, floor) - 0.6 - s.cam) * Math.min(1, dt * 6));
    if (c && c.net && (sendT -= dt) <= 0 && s.mode !== 'win') { sendT = 0.25; c.net.send({t: 'hx', n: s.next, f: s.fire ? 1 : 0}); }
    hudTick();
  }
  function bob(dt) {   // 预览：球原地弹
    S.vy = Math.min(VMAX, S.vy + G * dt); S.y += S.vy * dt;
    if (S.y + BR >= 1) { S.y = 1 - BR; S.vy = -BOUNCE; S.squash = 1; }
    S.squash = Math.max(0, S.squash - dt * 5);
  }
  const gateHere = () => { const r = S.rows[S.next]; return r && r.gate ? r.q : null; };

  function turn(dt) {   // 转柱子：按住键越转越快；Shift + 方向 / 滚轮：转到下一个洞（动画）；拖：跟着手指
    // 题门上（gate）：← → 一下换一个答案，↓ / 空格 / 回车掉下去；点了答案牌就等转到了再掉（dropAfter）
    const s = S, inp = Play.input, gate = s.mode === 'gate', live = s.mode === 'play' || s.mode === 'wait' || gate;
    if (!live || inp.blocked) { s.w = 0; s.hold = 0; inp.take('drop'); return; }
    const l = inp.take('left'), r = inp.take('right'), down = inp.take('drop');
    if ((shift || gate) && (l || r)) { s.dropAfter = false; snapNext(l ? -1 : 1); }
    const want = shift || gate ? 0 : (inp.keys.left ? 1 : 0) - (inp.keys.right ? 1 : 0);
    if (want) {
      s.snap = null; s.hold += dt;
      const top = SPIN + (SPIN_MAX - SPIN) * Math.min(1, s.hold / 0.5);
      s.w = Play.clamp(s.w + Math.sign(want * top - s.w) * SPIN_ACC * dt, -top, top);
    } else { s.hold = 0; s.w *= Math.max(0, 1 - dt * 14); }
    s.rot += s.w * dt;
    if (s.snap != null) {
      const d = s.snap - s.rot;
      if (Math.abs(d) < 0.004) { s.rot = s.snap; s.snap = null; } else s.rot += d * Math.min(1, dt * 16);
    }
    if (gate && (down || s.dropAfter && s.snap == null)) dropGate();
  }
  function snapNext(dir) {   // 下面那一层，往 dir 那边下一个能掉下去的洞（不算球现在所在的这个），转到它正中间
    const s = S, row = s.rows[s.next];
    if (!row) return;
    const open = j => row.broken || (row.gate ? row.q && openOpt(row.q, row.q.holeOf[j]) : row.cells[j] === 'g');
    const base = s.snap ?? s.rot, a = mod(BALL_A - base - row.ang, Math.PI * 2), cur = Math.floor(a / SEC) % N;
    let best = null;
    for (let j = 0; j < N; j++) {   // 每个洞（连着的几格）从第一格算起
      if (!open(j) || open(mod(j - 1, N))) continue;
      let n = 1;
      while (n < N && open(mod(j + n, N))) n++;
      if (mod(cur - j, N) < n) continue;   // 球就在这个洞上：跳过它，去下一个
      const mid = (j + n / 2) * SEC, d = dir > 0 ? mod(mid - a, Math.PI * 2) : mod(a - mid, Math.PI * 2);
      if (best == null || d < best) best = d;
    }
    if (best == null) return;
    s.snap = base - dir * best; s.w = 0;
    Play.sfx.play('swing');
  }

  // ------------------------------------------------------------ 画面
  function geo() {   // 层距 PX 小、塔窄一点：球落脚的那层在 1/3 高（镜头 y - 0.6），下面两层连前沿都在画面里、在底下的提示条上面，看得见刺和洞
    const W = stage.W, H = stage.H, side = W >= 640;
    const R = Math.min(side ? 170 : W * 0.36, H * 0.28), CX = side ? W / 2 - 10 : W / 2 - 6;
    return {W, H, R, RP: R * 0.2, K: 0.34, CX, PX: H * 0.22, TOP: H * (0.17 + 0.1 * (S ? S.lift : 0)), TH: Math.max(7, R * 0.08)};
  }
  const sy = (g, y) => g.TOP + (y - S.cam) * g.PX;
  const P = (g, a, y, r) => [g.CX + r * Math.cos(a), y + r * g.K * Math.sin(a)];

  function render() {
    const s = S, g = geo(), th = Play.scene.themes[theme()] || Play.scene.themes.day;
    stage.begin();
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    Play.scene.sky(ctx, g.W, g.H, theme());
    Play.scene.drawClouds(ctx, g.W, theme(), clock * 3);
    const first = Math.max(0, Math.floor(s.cam - 1.2)), last = Math.min(s.rows.length, Math.ceil(s.cam + g.H / g.PX + 0.5));
    // 终点台（最后一层下面）
    const fy = sy(g, s.rows.length + 1);
    if (fy < g.H + 80) {
      pillar(g, fy, g.H + 20);
      ctx.fillStyle = '#ffd23f'; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.ellipse(g.CX, fy + g.TH, g.R * 1.05, g.R * 1.05 * g.K, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#ffe98a'; ctx.beginPath(); ctx.ellipse(g.CX, fy, g.R * 1.05, g.R * 1.05 * g.K, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.font = `900 ${Math.round(g.R * 0.2)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = INK; ctx.fillText('🏁 终点', g.CX, fy + 2);
    }
    let ballDrawn = false;
    const land = s.mode === 'play' ? landRow() : null;
    for (let i = last - 1; i >= first; i--) {
      const row = s.rows[i], y = sy(g, i + 1), above = i > 0 ? sy(g, i) : -40;
      if (!ballDrawn && i < s.next) { ball(g); ballDrawn = true; }   // 球在这一层下面
      if (!row.broken) { half(g, row, y, th, false); }
      pillar(g, Math.min(above, y), y);
      if (!row.broken) { half(g, row, y, th, true); if (row === land) shadow(g, row, y); if (row.gate) labels(g, row, y); }
      if (i === first && first > 0) pillar(g, -40, sy(g, i));
    }
    if (first === 0 && s.rows.length) pillar(g, -40, sy(g, 1));
    if (!ballDrawn) ball(g);
    depthBar(g);
    if (s.mode === 'win') {
      ctx.font = `900 46px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.lineWidth = 8; ctx.strokeStyle = '#fff'; ctx.strokeText('🏁 到底啦！', g.CX, g.H * 0.4); ctx.fillStyle = '#ff6a00'; ctx.fillText('🏁 到底啦！', g.CX, g.H * 0.4);
    }
    ctx.restore();
    fx.draw(ctx); fx.drawFlash(ctx, g.W, g.H);
  }
  function landRow() {   // 球直直往下会落在哪一层：对着洞（或砸碎了）的层跳过去，最多看三层
    for (let i = S.next; i < Math.min(S.rows.length, S.next + 3); i++) {
      const row = S.rows[i];
      if (!row.broken && (row.gate || row.cells[ballCell(row)] !== 'g')) return row;
    }
    return null;
  }
  function shadow(g, row, y) {   // 落点影子：落到刺上就变成警告（黄圈 + !，一跳一跳）
    const bad = !row.gate && row.cells[ballCell(row)] === 's' && spikeOut(row) && S.safe <= 0 && !S.fire;
    const [x, py] = P(g, BALL_A, y, g.R * 0.62), r = ballR(g) * (bad ? 1.25 + Math.sin(clock * 14) * 0.12 : 1.05);
    ctx.fillStyle = bad ? 'rgba(43,35,64,.55)' : 'rgba(43,35,64,.25)';
    ctx.beginPath(); ctx.ellipse(x, py, r, r * 0.45, 0, 0, Math.PI * 2); ctx.fill();
    if (bad) {   // 刺本来就是红的：影子加一圈亮黄、上面一个 !，一眼看得出「要落到刺上了」
      ctx.strokeStyle = '#ffd23f'; ctx.lineWidth = 3.5; ctx.stroke();
      ctx.font = `900 ${Math.round(r * 1.1)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      ctx.lineWidth = 4; ctx.strokeStyle = INK; ctx.strokeText('!', x, py - r * 0.4); ctx.fillStyle = '#ffd23f'; ctx.fillText('!', x, py - r * 0.4);
    }
  }
  function pillar(g, y0, y1) {
    if (y1 <= y0) return;
    ctx.fillStyle = '#fff6e0'; ctx.fillRect(g.CX - g.RP, y0, g.RP * 2, y1 - y0);
    ctx.fillStyle = 'rgba(0,0,0,.07)'; ctx.fillRect(g.CX + g.RP * 0.35, y0, g.RP * 0.65, y1 - y0);
    ctx.strokeStyle = INK; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(g.CX - g.RP, y0); ctx.lineTo(g.CX - g.RP, y1); ctx.moveTo(g.CX + g.RP, y0); ctx.lineTo(g.CX + g.RP, y1); ctx.stroke();
  }
  function cellColor(row, j, th) {
    const ch = row.cells[j], gq = row.q;
    if (row.gate) {
      const o = gq ? gq.holeOf[j] : -1;
      if (o >= 0 && gq.armed && !gq.tried.has(o) && (!gq.done || o === gq.right)) return null;   // 开着的洞
      if (o >= 0) return '#cfc8dd';                                                              // 盖着的洞（还没开门 / 选错了）
      return j % 2 ? '#ffffff' : '#f6f0ff';
    }
    if (ch === 'g') return null;
    if (ch === 's') return spikeOut(row) && S.safe <= 0 ? '#ff4d5a' : '#ffb3ba';
    return j % 2 ? th.hill[0] : th.hill[1];
  }
  function half(g, row, y, th, front) {   // 半个圆盘：后半圈先画（被柱子挡住），前半圈后画（带厚度）
    ctx.save(); ctx.beginPath(); front ? ctx.rect(-50, y, g.W + 100, g.H + 100) : ctx.rect(-50, -50, g.W + 100, y + 50); ctx.clip();
    const a0 = S.rot + row.ang;
    for (let j = 0; j < N; j++) {
      const col = cellColor(row, j, th);
      if (!col) continue;
      const s0 = a0 + j * SEC, s1 = s0 + SEC;
      if (front) {   // 厚度
        const pts = []; for (let k = 0; k <= 8; k++) { const a = s0 + SEC * k / 8; if (Math.sin(a) > -0.05) pts.push(a); }
        if (pts.length > 1) {
          ctx.beginPath();
          for (const a of pts) ctx.lineTo(...P(g, a, y, g.R));
          for (const a of pts.slice().reverse()) ctx.lineTo(...P(g, a, y + g.TH, g.R));
          ctx.closePath(); ctx.fillStyle = Art.shade(hex6(col), -0.25); ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.stroke();
        }
      }
      ctx.beginPath();
      for (let k = 0; k <= 8; k++) ctx.lineTo(...P(g, s0 + SEC * k / 8, y, g.R));
      for (let k = 8; k >= 0; k--) ctx.lineTo(...P(g, s0 + SEC * k / 8, y, g.RP));
      ctx.closePath(); ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.stroke();
      if (row.cells[j] === 's' && !row.gate && spikeOut(row) && S.safe <= 0) spikes(g, (s0 + s1) / 2, y);
      if (row.gate && row.q && row.q.holeOf[j] >= 0 && row.q.tried.has(row.q.holeOf[j]) && j % 2 === 0) {}   // 盖子的 ✕ 画在牌子上
    }
    if (front && row.honey >= 0) {
      const [x, hy] = P(g, a0 + (row.honey + 0.5) * SEC, y, g.R * 0.62);
      if (Math.sin(a0 + (row.honey + 0.5) * SEC) > 0) drop(x, hy - 6 + Math.sin(clock * 4) * 3, g.R / 190);
    }
    ctx.restore();
  }
  function spikes(g, a, y) {
    for (const d of [-0.14, 0, 0.14]) {
      const [x, py] = P(g, a + d, y, g.R * 0.66), k = g.R / 190;
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.moveTo(x - 6 * k, py); ctx.lineTo(x, py - 16 * k); ctx.lineTo(x + 6 * k, py); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = INK; ctx.lineWidth = 1.6; ctx.stroke();
    }
  }
  function drop(x, y, k) {
    ctx.fillStyle = '#ffa31a'; ctx.beginPath(); ctx.moveTo(x, y - 12 * k); ctx.quadraticCurveTo(x + 10 * k, y + 2 * k, x, y + 9 * k); ctx.quadraticCurveTo(x - 10 * k, y + 2 * k, x, y - 12 * k);
    ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.stroke();
  }
  function labels(g, row, y) {   // 题门洞口的答案牌：跟着柱子转，后面的小一点、淡一点
    const q = row.q;
    if (!q || !q.item || !q.item.options) {
      if (q) { ctx.font = `900 ${Math.round(g.R * 0.13)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = INK; ctx.fillText('…', g.CX, y + g.R * g.K * 0.6); }
      return;
    }
    const a0 = S.rot + row.ang, seen = new Set(), sel = S.mode === 'gate' && S.rows[S.next] === row ? q.holeOf[ballCell(row)] : -1;
    const items = [];
    q.rects = [];   // 点答案牌用（画面坐标，后面的先放，点的时候从前面的找起）
    for (let j = 0; j < N; j++) {
      const o = q.holeOf[j]; if (o < 0 || seen.has(o)) continue;
      seen.add(o);
      const a = a0 + (j + 1) * SEC;
      items.push({o, a, depth: Math.sin(a)});
    }
    items.sort((p, r) => p.depth - r.depth);
    for (const {o, a, depth} of items) {   // 牌子立在洞口外沿：前面的挂在圆盘边下面（球挡不住），后面的立在边上
      const [x, ry] = P(g, a, y, g.R * (depth < 0 ? 1.08 + 0.3 * depth : 1.08)), k = (0.78 + 0.22 * (depth + 1) / 2) * Math.max(0.8, g.R / 150);
      const text = q.item.options[o], tried = q.tried.has(o), right = q.done && o === q.right, on = o === sel && !tried;
      ctx.globalAlpha = depth < -0.3 ? 0.8 : 1;
      ctx.font = `900 ${Math.round(24 * k)}px ${FONT}`;
      const w = Math.max(54 * k, ctx.measureText(text).width + 22 * k), h = 38 * k, cy = ry + (depth > 0 ? depth * (g.TH + h * 0.6) : 0);   // 后面的往里收到圆盘上，不钻到题牌底下
      const x0 = x - w / 2, y0 = cy - h / 2;
      ctx.fillStyle = tried ? '#ddd6e8' : right ? '#c9f2d3' : on ? '#fff1b8' : '#fff';
      Play.roundRect(ctx, x0, y0, w, h, 11 * k); ctx.fill();
      ctx.strokeStyle = right ? '#2fa84f' : on ? '#ff9f1a' : INK; ctx.lineWidth = right || on ? 4 : 2.5; ctx.stroke();
      q.rects.push({o, x0, y0, x1: x0 + w, y1: y0 + h});
      ctx.fillStyle = tried ? '#9a90ad' : INK; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(text, x, cy + 1);
      if (tried) { ctx.strokeStyle = '#e0475b'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x0 + 6, y0 + 6); ctx.lineTo(x0 + w - 6, y0 + h - 6); ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
  }
  const ballR = g => Math.max(BR * g.PX, g.R * 0.1);
  function ball(g) {
    const s = S, r = ballR(g), x = g.CX, y = sy(g, s.y) + g.R * 0.62 * g.K, main = myColor();
    s.cx = x; s.by = y;
    const sq = s.mode === 'gate' ? 0.05 * Math.sin(clock * 5) : s.squash * 0.28, rx = r * (1 + sq), ry = r * (1 - sq);   // 停在题门上：轻轻喘气
    if (s.vy > 2 || s.fire) {   // 拖尾
      for (let k = 1; k <= 5; k++) { ctx.globalAlpha = 0.12 * (6 - k); ctx.fillStyle = s.fire ? '#ff7a00' : main; ctx.beginPath(); ctx.arc(x, y - k * r * 0.7, r * (1 - k * 0.1), 0, Math.PI * 2); ctx.fill(); }
      ctx.globalAlpha = 1;
    }
    if (s.safe > 0 && Math.floor(clock * 10) % 2) ctx.globalAlpha = 0.5;
    const cy = y + ry - r;
    if (s.fire) { ctx.fillStyle = '#ffd23f'; ctx.beginPath(); ctx.arc(x, cy, r * 1.45 + Math.sin(clock * 30) * 2, 0, Math.PI * 2); ctx.fill(); }
    ctx.fillStyle = s.fire ? '#ff6a00' : main; ctx.beginPath(); ctx.ellipse(x, cy, rx, ry, 0, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.55)'; ctx.beginPath(); ctx.ellipse(x - rx * 0.38, cy - ry * 0.42, rx * 0.26, ry * 0.16, -0.6, 0, Math.PI * 2); ctx.fill();
    const dead = s.mode === 'dead';
    ctx.fillStyle = INK; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
    for (const d of [-1, 1]) {
      if (dead) { ctx.beginPath(); ctx.moveTo(x + d * rx * 0.34 - 4, cy - 5); ctx.lineTo(x + d * rx * 0.34 + 4, cy + 3); ctx.moveTo(x + d * rx * 0.34 + 4, cy - 5); ctx.lineTo(x + d * rx * 0.34 - 4, cy + 3); ctx.stroke(); }
      else { ctx.beginPath(); ctx.arc(x + d * rx * 0.34, cy - ry * 0.05, r * 0.13, 0, Math.PI * 2); ctx.fill(); }
    }
    ctx.fillStyle = 'rgba(255,120,140,.45)'; for (const d of [-1, 1]) { ctx.beginPath(); ctx.arc(x + d * rx * 0.58, cy + ry * 0.22, r * 0.14, 0, Math.PI * 2); ctx.fill(); }
    ctx.globalAlpha = 1;
  }
  function depthBar(g) {   // 右边：掉到哪了、🚩 每 1/4、👻 上次的我、🏁
    const n = S.rows.length;
    if (!n || S.mode === 'preview') return;
    const x = g.W - (g.W >= 640 ? 46 : 20), y0 = 78, h = g.H - y0 - 34, w = g.W >= 640 ? 18 : 12;
    ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5; Play.roundRect(ctx, x - w / 2, y0, w, h, w / 2); ctx.fill(); ctx.stroke();
    const k = Math.min(1, S.next / n);
    ctx.fillStyle = '#ffd23f'; Play.roundRect(ctx, x - w / 2, y0, w, Math.max(w, h * k), w / 2); ctx.fill(); ctx.stroke();
    ctx.font = `${g.W >= 640 ? 15 : 11}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (const f of [0.25, 0.5, 0.75]) { ctx.fillStyle = INK; ctx.fillRect(x - w / 2 - 3, y0 + h * f, w + 6, 2); ctx.fillText('🚩', x + w + 4, y0 + h * f - 6); }
    ctx.fillText('🏁', x, y0 + h + 16);
    const gh = ghost();
    if (gh != null) {
      ctx.globalAlpha = 0.55; ctx.fillStyle = '#9a8fd6'; ctx.beginPath(); ctx.arc(x, y0 + h * gh, w * 0.55, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1;
      if (g.W >= 640) { ctx.textAlign = 'right'; ctx.fillStyle = INK; ctx.fillText('👻 上次的我', x - w - 6, y0 + h * gh); }
      if (!S.ghostTold && k > gh && k > 0.1) { S.ghostTold = true; sh.toast('超过上次的自己！', 'good'); }
    }
    let side = 0;
    for (const p of others()) {   // 一起玩的人：一个个小球，旁边写名字（窄屏写第一个字）
      const m = mates[p.id]; if (!m) continue;
      const py = y0 + h * Math.min(1, (m.n || 0) / n), px = x;
      ctx.fillStyle = m.fire ? '#ff6a00' : ROLE_COLOR[p.role] || Art.avatar(p.avatar).main; ctx.strokeStyle = INK; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, w * 0.55, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.textAlign = 'right';
      ctx.fillText(g.W >= 640 ? `${m.done ? '🏁 ' : ''}${p.name}` : p.name.slice(0, 1), x - w - 6, py + (side++ % 2 ? 9 : -3));
    }
    ctx.fillStyle = S.fire ? '#ff6a00' : myColor(); ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(x, y0 + h * k, w * 0.7, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }

  // ------------------------------------------------------------ 上面一条：用时、蜂蜜、题门的题、连穿 / 火球
  function buildHud() {
    if (!hudEl) { hudEl = document.createElement('div'); sh.canvas.parentNode.insertBefore(hudEl, sh.hud.nextSibling); }   // 自己一层：联网时外壳的状态条用 sh.hud
    hudEl.className = 'hx-layer' + (c && c.net ? ' mp' : '');
    hudEl.innerHTML = `<div class="hx-hud"><span class="hx-side"><span class="hx-chip hx-time"></span><span class="hx-chip hx-honey"></span></span>
      <div class="hx-q"><div class="hx-topic"></div><div class="hx-text"><span></span><button type="button" class="hx-say" aria-label="再听一遍">🔊</button></div><div class="hx-tip"></div></div>
      <span class="hx-side r"><span class="hx-chip hx-fire"></span></span></div>`;
    const $ = q => hudEl.querySelector(q);
    hud = {time: $('.hx-time'), honey: $('.hx-honey'), fire: $('.hx-fire'), q: $('.hx-q'), topic: $('.hx-topic'), text: $('.hx-text span'), tip: $('.hx-tip'), say: $('.hx-say'), last: ''};
    hud.say.onclick = () => { const g = gateHere(); if (g && g.item && g.item.say && typeof Speak !== 'undefined') Speak.play(g.item.say, 'en', 'word'); };
    hud.say.style.pointerEvents = 'auto';
    buildQ();
  }
  function buildQ() {
    if (!hud) return;
    const g = gateHere() || (S.rows[S.next - 1] && S.rows[S.next - 1].gate && S.mode === 'wait' ? S.rows[S.next - 1].q : null);
    const show = !!(g && g.item) && S.mode !== 'win';
    hud.q.classList.toggle('on', show);
    if (!show) return;
    const it = g.item;
    hud.topic.textContent = it.topic || '题门';
    hud.text.textContent = g.done && g.right >= 0 && g.tried.size ? `${it.q}  →  ${it.options[g.right]}` : it.q;
    hud.say.hidden = !it.say;
    const how = touchy ? '点一下答案牌就掉进去' : '← → 选答案，↓ 或空格掉下去（也能点答案牌）';
    hud.tip.textContent = !g.armed ? '落到门上就停下来，慢慢想' : g.done ? (touchy ? '对的答案亮了，点它掉下去' : '对的答案亮了，选它掉下去') : g.tried.size ? '选错的盖上了，再选一个' : how;
    hud.q.classList.toggle('long', it.q.length > 24);
  }
  function hudTick() {
    if (!hud) return;
    const t = Math.floor(seconds()), k = `${t}|${S.honey}|${S.streak}|${S.fire}`;
    if (hud.last === k) return;
    hud.last = k;
    hud.time.textContent = `⏱ ${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
    hud.honey.textContent = `🍯 ${S.honey}`;
    hud.fire.textContent = S.fire ? '🔥 火球！' : `🔥 连穿 ${S.streak}`;
    hud.fire.classList.toggle('hot', S.fire || S.streak >= 2);
  }

  // ------------------------------------------------------------ 几个人一起：谁掉到哪了、谁先到底
  const others = () => c && c.peers ? c.peers : [];
  function arrived() {   // 我到底了：一个人玩直接过关；几个人一起就告诉大家，等大家都到
    const me = {secs: +seconds().toFixed(1), ok: S.gatesOk};
    if (!others().length) return report('win');
    mates[c.me.id] = Object.assign(mates[c.me.id] || {}, {done: me, n: S.rows.length});
    c.net.send({t: 'hx', n: S.rows.length, done: me});
    const k = place(c.me.id);
    sh.toast(k === 1 ? '🥇 第一个到底！看看大家到哪了' : `第 ${k} 个到底！给还在跳的人加油吧`, 'good');
    allIn();
  }
  function place(id) { const d = mates[id] && mates[id].done; return d ? 1 + Object.values(mates).filter(m => m.done && m.done.secs < d.secs).length : 0; }
  function onMate(m) {
    const p = others().find(x => x.id === m.from); if (!p) return;
    const was = mates[m.from] && mates[m.from].done;
    mates[m.from] = Object.assign(mates[m.from] || {}, {name: p.name, role: p.role, avatar: p.avatar, n: m.n, fire: !!m.f}, m.done ? {done: m.done} : {});
    if (m.done && !was) { sh.toast(`${place(m.from) === 1 ? '🥇 ' : ''}${p.name} 到底啦！`, 'good'); allIn(); }
  }
  function allIn() {   // 大家都到底了：开房间的人报过关
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
    if (teamSecs != null) return c.finish(result, {seconds: secs, score: teamOk}, null);   // 标题各自在 verdict 里写（每个人看到自己第几）
    const title = result === 'win'
      ? `🌀 到底啦！<small class="hx-sub">${secs.toFixed(1)} 秒 · 题门一次答对 ${s.gatesOk}/${s.lv.gates || 0} · 🍯 ${s.honey}${s.free >= 1 ? ` · 想题的 ${Math.round(s.free)} 秒没算` : ''}</small>`
      : `⏰ 时间到，掉到第 ${s.next} 层<small class="hx-sub">下次接着跳</small>`;
    c.finish(result, {seconds: secs, score: s.gatesOk}, title);
  }

  // ------------------------------------------------------------ 鼠标、手指：左右拖着转（拖多少转多少）；滚轮一格一格转；Shift 键
  function pointerSetup(cv) {
    cv.style.touchAction = 'none';
    const live = () => S && !Play.input.blocked && (S.mode === 'play' || S.mode === 'wait' || S.mode === 'gate');
    cv.addEventListener('pointerdown', e => { drag = {x: e.clientX, x0: e.clientX, y0: e.clientY, moved: false}; cv.setPointerCapture && cv.setPointerCapture(e.pointerId); Play.sfx.unlock(); });
    cv.addEventListener('pointermove', e => {
      if (!drag) return;
      if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > 8) drag.moved = true;
      if (!live()) { drag.x = e.clientX; return; }
      const r = cv.getBoundingClientRect(), dx = (e.clientX - drag.x) * stage.W / Math.max(1, r.width);
      drag.x = e.clientX; S.snap = null; S.dropAfter = false;
      S.rot -= dx / (geo().R * 0.62);
    });
    cv.addEventListener('pointerup', e => {   // 题门上：点答案牌 = 转过去掉下去；拖完松手 = 停到最近的答案上
      const d = drag; drag = null;
      if (!d || !live() || S.mode !== 'gate') return;
      const row = S.rows[S.next];
      if (d.moved) return aimNearest(row);
      const r = cv.getBoundingClientRect(), x = (e.clientX - r.left) * stage.W / Math.max(1, r.width), y = (e.clientY - r.top) * stage.H / Math.max(1, r.height);
      const hit = (row.q.rects || []).slice().reverse().find(b => x >= b.x0 - 6 && x <= b.x1 + 6 && y >= b.y0 - 6 && y <= b.y1 + 6);
      if (hit && openOpt(row.q, hit.o)) { aimAt(row, hit.o); S.dropAfter = true; }
    });
    cv.addEventListener('pointercancel', () => { drag = null; });
    cv.addEventListener('wheel', e => {
      if (!live()) return;
      e.preventDefault();
      S.snap = (S.snap ?? S.rot) + Math.sign(e.deltaY || e.deltaX) * SEC * 0.5;
    }, {passive: false});
    addEventListener('keydown', e => { if (e.key === 'Shift') shift = true; });
    addEventListener('keyup', e => { if (e.key === 'Shift') shift = false; });
    addEventListener('blur', () => { shift = false; });
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = Play.stage(sh.canvas, {height: 450, minW: 300, maxW: 1400});
    ctx = stage.ctx;
    S = fresh(null, sh.data.avatar);
    pointerSetup(sh.canvas);
    const loop = Play.loop(update, render);
    loop.start();
  }
  function ready() {
    S = fresh(S && S.mode !== 'preview' ? S.lv : null, sh.data.avatar); fx.parts = []; fx.texts = []; hud = null; mates = {};
    if (hudEl) hudEl.innerHTML = '';
  }
  function begin(cx) {
    c = cx; mates = {}; sendT = 0;
    if (cx.net) { cx.net.off('hx'); cx.net.on('hx', onMate); }
    S = fresh(cx.level && cx.level.rows ? cx.level : null, (cx.match && cx.match.avatar) || sh.data.avatar);
    S.limit = Math.max(30, Math.min(600, (cx.match && !cx.match.unlimited && cx.match.seconds_left) || 600));
    S.mode = 'play';
    Play.input.clear();
    buildHud(); hudTick();
    sh.toast(others().length ? '同一座塔，看谁先到底！' : S.lv.best ? `上次 ${S.lv.best.toFixed(1)} 秒，👻 在右边陪你跳` : '转柱子，从洞里掉下去！', 'good');
  }

  const rules = [
    '球一直在弹；<b>← →</b> 按住转柱子（按久了越转越快），<b>鼠标 / 手指左右拖</b>也能转，让洞转到球下面，球就掉下去',
    '球下面的<b>小影子</b>是它要落的地方，影子<b>变红</b>就是要落到刺上了，快转开',
    '<b>红色的刺</b>别碰，碰到就倒下，答对 1 道就起来',
    '<b>题门</b>：球落到门上就停住，上面写着题，每个洞立着一个答案。<b>← →</b> 一下换一个，<b>↓ / 空格</b>掉下去；也可以直接<b>点答案牌</b>。答对<b>变火球</b>，选错了会弹回来，再选',
    '连穿 3 层不落地也会变<b>火球</b>，火球能砸碎下一层（连刺一起）',
    '最快掉到 <b>🏁 底</b>就赢；想题的前 8 秒不算时间，右边的 👻 是上次的自己',
    '几个人一起：同一座塔各跳各的，右边看得到大家掉到哪了，<b>谁先到底谁第一</b>，大家都到底才过关',
  ];

  PlayShell.init({
    quiz: 'revive', tempo: 132, rules, moreHint: '一起比谁先到底',
    keys: '电脑：← → 或 A D 按住转（越按越快），Shift + ← → 一下转到下一个洞；题门上 ← → 选答案，↓ / 空格 / 回车掉下去；鼠标拖、滚轮也能转',
    input: {left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], drop: ['ArrowDown', 's', ' ', 'Enter']},
    revive: {verb: '接着跳', done: '🎉 接着跳！'},
    setup: boot, ready, begin,
    end() { if (S) S.mode = S.mode === 'win' ? 'win' : 'done'; if (hudEl) hudEl.innerHTML = ''; },
    verdict(res, result) {   // 几个人一起：每个人看到自己第几个到底
      if (result !== 'win' || !others().length) return {};
      const k = place(c.me.id);
      return {title: `${k === 1 ? '🥇 我第一个到底！' : `🎉 大家都到底了，我第 ${k} 个`}<small class="hx-sub">${ranking()}</small>`};
    },
    onRevive() {   // 起来：回到这一层上面，刺软一会儿
      const s = S; if (!s) return;
      s.mode = 'play'; s.safe = SAFE_S; s.y = s.next + 1 - BR - 0.5; s.vy = 0; s.fire = false;
      Play.input.clear();
    },
    pause(on) { if (on) { Play.input.clear(); drag = null; } },
  });
})();

/* 一笔连花路：小蜜蜂从起点出发，一笔走满整个花园，按顺序经过每朵花，最后飞回蜂巢。
   关卡（app/arena/games/link.py）：一张图（方格 sq / 六边形 hex，篱笆 walls、小桥 bridges、魔法花 warps）和一条走得满的路 path。
   题就是玩法：开局要一道「排序题」（sh.gateAsk，shape = sequence），花上写 seq.tokens（字母、算式），
     按 path 均匀摆在路上：第一朵是起点（小蜜蜂在上面），最后一朵是蜂巢。连的顺序就是答案。前 2 关教玩法，花上写 1、2、3。
   操作：按住小蜜蜂（或线上任何一格）拖着走；往回拖就撤销；点线上哪一格就退回到那一格。电脑：方向键走、退格撤销。
   连错了（连到不是下一朵的花）：花晃一晃变红，线退回上一朵对的花；同一步错两次，下一朵花自己亮起来。
   最后一朵（蜂巢）要等所有格子都走到了才能连。走满了把这一关先后连过的花交上去（sh.gateSend），一步没错才算一次连对。
   卡住了点「帮帮我」：外壳盖上题目（和倒下复活同一条规矩：答对 1 道），答对了线退回到对的那一段，后面 3 格亮起来。
   过关仪式里（finale）：花从起点到蜂巢一路依次开放，小蜜蜂在蜂巢上欢呼。
   几个人一起（最多 4 个）：同一个花园各连各的，每隔一会儿告诉大家我的线（lk），宽屏右边小窗画出大家；谁先走满谁第一，
   大家都走满了由开房间的人报过关。设计：docs/一笔连花路-调研与建议（项目文件），设计稿 ui-2026-11-03-zip/。 */
(function () {
  const INK = (window.LOOK && window.LOOK.ink) || '#3a3026';
  const FONT = '"Baloo 2", "PingFang SC", system-ui, sans-serif';
  const BODY = {sun: ['#ffcf2e', '#f0a800'], berry: ['#ff7a8a', '#e24a5f'], mint: ['#6fe0b0', '#2fb886'], sky: ['#7cc4ff', '#3a8fe0']};
  const BOARD = {   // 每个世界花园的颜色：格子两种绿、走过的蜜色、框、篱笆
    day: {a: '#d3efb4', b: '#c4e7a0', lit: '#ffeaa6', lit2: '#ffe08a', frame: '#fff6df', fence: '#e9c98a', tuft: '#9fcf7c'},
    candy: {a: '#ffe3ef', b: '#ffd3e6', lit: '#fff0b8', lit2: '#ffe59a', frame: '#fffafc', fence: '#f6a9c9', tuft: '#f2a7c8'},
    night: {a: '#3d3f8f', b: '#363784', lit: '#6f64c9', lit2: '#7c70d6', frame: '#2a2b6e', fence: '#7a7fd6', tuft: '#5a5cb0'},
    sunset: {a: '#ffe6a3', b: '#ffdb85', lit: '#fff3cf', lit2: '#fff0c0', frame: '#fff7e0', fence: '#e8a300', tuft: '#e8b54a'},
    lava: {a: '#ffd9c2', b: '#ffcdb0', lit: '#fff0c0', lit2: '#ffe59a', frame: '#fff4ea', fence: '#d98a6a', tuft: '#e0a080'},
  };
  const PETALS = ['#ff7aa8', '#ffffff', '#b89cff', '#ff9e5e', '#7cc4ff'];
  const HINT_N = 3, HINT_S = 6;

  let sh = null, stage = null, ctx = null, c = null, hudEl = null, hud = null;
  const fx = new Play.FX();
  let S = null, clock = 0, GEO = null;
  let mates = {}, sendT = 0, dirty = false;
  const touchy = matchMedia('(pointer: coarse)').matches;
  const esc = s => String(s).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));

  // ------------------------------------------------------------ 关卡 → 图（和 link.py 的 graph 一样）
  function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function hexNbs(r, q, rows, cols) {
    const d = r % 2 ? [[-1, 0], [-1, 1], [1, 0], [1, 1]] : [[-1, -1], [-1, 0], [1, -1], [1, 0]];
    return [[0, -1], [0, 1], ...d].map(([a, b]) => [r + a, q + b]).filter(([a, b]) => a >= 0 && a < rows && b >= 0 && b < cols);
  }
  function graph(lv) {
    const {rows, cols} = lv, kind = lv.kind || 'sq', n = rows * cols;
    const walls = new Set((lv.walls || []).map(([a, b]) => Math.min(a, b) + ',' + Math.max(a, b)));
    const bridges = new Set(lv.bridges || []);
    const adj = new Map();
    for (let i = 0; i < n; i++) adj.set(i, new Set());
    for (const b of bridges) adj.set(n + b, new Set());
    const slot = (i, v) => bridges.has(i) && v ? n + i : i;
    for (let r = 0; r < rows; r++) for (let q = 0; q < cols; q++) {
      const a = r * cols + q;
      const nbs = kind === 'hex' ? hexNbs(r, q, rows, cols) : [[0, 1], [1, 0], [0, -1], [-1, 0]].map(([x, y]) => [r + x, q + y]).filter(([x, y]) => x >= 0 && x < rows && y >= 0 && y < cols);
      for (const [rr, qq] of nbs) {
        const b = rr * cols + qq;
        if (walls.has(Math.min(a, b) + ',' + Math.max(a, b))) continue;
        const v = kind !== 'hex' && rr !== r;
        adj.get(slot(a, v)).add(slot(b, v));
      }
    }
    for (const [a, b] of lv.warps || []) { adj.get(a).add(b); adj.get(b).add(a); }
    return adj;
  }

  const DEMO = {kind: 'sq', rows: 4, cols: 4, walls: [], bridges: [], warps: [], tut: true, seed: 3,
                path: [0, 1, 5, 4, 8, 12, 13, 9, 10, 14, 15, 11, 7, 6, 2, 3]};

  // 几个人一起时服务器给每个角色一个花园（lv.seats，link.py 的 variant）：同一关转过、翻过或倒着走，一样难、路不一样
  const seatOf = (lv, role) => (lv && lv.seats && lv.seats[role]) ? {...lv, ...lv.seats[role]} : lv;
  function fresh(base, avatar, role) {
    base = base && base.path ? base : DEMO;
    const lv = seatOf(base, role), n = lv.rows * lv.cols, adj = graph(lv);
    return {mode: 'preview', base, lv, n, adj, total: adj.size, avatar: avatar || 'sun', theme: (lv.world && lv.world.scene) || 'day',
            item: null, tokens: [], vals: null, flowers: new Map(), hive: -1,
            path: [lv.path[0]], step: 1, tries: [], wrong: {}, glow: null, hint: null, shake: null, warn: '',
            helps: 0, gatesOk: 0, bloomT: -1, limit: 600, sent: false, beeT: 0, beeFrom: null};
  }
  const cellOf = v => v % S.n;
  const isBridgeSlot = v => v >= S.n;

  // 花摆在 path 上：k 朵，起点、终点固定（服务器保证两头在普通格子上），中间均匀摆、按种子错开一点。
  // 中间的花只摆在普通格子上（不在小桥、魔法花上），一格一朵：先挑出能摆的位置，再从里面按顺序挑，不会叠在一起
  function spots(lv, k) {
    const n = lv.rows * lv.cols, L = lv.path.length, r = rng(lv.seed || 1);
    const bad = new Set([...(lv.bridges || []), ...(lv.warps || []).flat()]);
    const ok = [];
    for (let j = 1; j < L - 1; j++) if (lv.path[j] < n && !bad.has(lv.path[j])) ok.push(j);
    k = Math.max(2, Math.min(k, ok.length + 2));
    const at = [0];
    for (let i = 1, q = -1; i < k - 1; i++) {
      const want = i * (L - 1) / (k - 1) + (r() - 0.5) * Math.min(2, (L - 1) / (k - 1) * 0.6);
      let p = ok.findIndex(j => j >= want);
      if (p < 0) p = ok.length - 1;
      q = Math.max(q + 1, Math.min(ok.length - (k - 1 - i), p));
      at.push(ok[q]);
    }
    at.push(L - 1);
    return at;
  }
  const room = lv => spots(lv, Infinity).length;   // 这一关最多摆几朵花
  function plant(tokens) {
    const s = S, at = spots(s.lv, tokens.length);
    s.flowers = new Map(at.map((j, i) => [s.lv.path[j], i]));
    s.hive = s.lv.path[s.lv.path.length - 1];
    s.tokens = tokens;
    s.tries = [tokens[0]];
    s.step = 1;
  }
  async function loadSeq() {
    const s = S, L = s.lv.path.length;
    if (s.lv.tut) { plant(Array.from({length: Math.max(3, Math.min(6, Math.round(L / 4)))}, (_, i) => String(i + 1))); s.mode = 'play'; return paintHud(); }
    s.mode = 'load';
    try {
      const it = await sh.gateAsk('link' + (s.lv.n || 0));
      if (S !== s) return;
      if (!it.seq || !it.seq.tokens || it.seq.tokens.length < 2) throw new Error('no seq');
      s.item = it; s.vals = it.seq.vals || null;
      plant(it.seq.tokens.slice(0, room(s.lv)));
      if (it.say && typeof Speak !== 'undefined') Speak.play(it.say, 'en', 'word');
    } catch (e) {
      if (S !== s) return;
      s.item = null; s.lost = true;
      plant(Array.from({length: 5}, (_, i) => String(i + 1)));
      sh.toast('题没拿到，这一关按 1、2、3 连吧', 'warn');
    }
    s.mode = 'play';
    paintHud();
  }

  // ------------------------------------------------------------ 连线
  const head = () => S.path[S.path.length - 1];
  const expected = () => S.tokens[S.step];
  const lastFlowerAt = () => { for (let i = S.path.length - 1; i >= 0; i--) if (S.flowers.has(S.path[i])) return i; return 0; };
  function recount() {   // 线变短了以后：已经连到第几朵花
    S.step = S.path.filter(v => S.flowers.has(v)).length;
  }
  function truncate(i) {   // 线退回到第 i 个点（含）
    if (i < 0 || i >= S.path.length - 1) return;
    S.path.length = i + 1; recount(); dirty = true;
    Play.sfx.play('tap');
  }
  function nodeAtCell(cell) {   // 这一格上，从线头能走过去的点（小桥分横竖两个点）
    const s = S, h = head(), cands = [cell, s.n + cell].filter(v => s.adj.has(v));
    return cands.find(v => s.adj.get(h).has(v) && !s.path.includes(v)) ?? null;
  }
  function onCell(cell) {
    const s = S;
    if (s.mode !== 'play' || cell == null || (c && c.reviving)) return;
    if (cellOf(head()) === cell) return;
    const prev = s.path.length >= 2 ? s.path[s.path.length - 2] : -1;
    if (prev >= 0 && cellOf(prev) === cell) return truncate(s.path.length - 2);   // 往回拖：撤销一格
    const v = nodeAtCell(cell);
    if (v != null) return extend(v);
    const at = s.path.findIndex(x => cellOf(x) === cell);
    if (at >= 0) truncate(at);   // 点到线上更早的一格：退回到那里
  }
  function extend(v) {
    const s = S, fi = s.flowers.get(v);
    if (fi != null) {
      const tok = s.tokens[fi], want = expected(), last = v === s.hive;
      if (tok === want && (!last || s.step === s.tokens.length - 1)) {
        if (last && s.path.length + 1 < s.total) return soft(v, `还有 ${s.total - s.path.length - 1} 格没走到，走满了再回蜂巢`);
      } else if (tok === want && last) return soft(v, '蜂巢要最后回，先去别的花');
      else return wrong(v, tok);
    }
    s.path.push(v); dirty = true;
    if (s.hint && s.hint.cells[0] === v) s.hint.cells.shift();
    if (fi != null) {
      s.tries.push(s.tokens[fi]); s.step++; s.glow = null;
      Play.sfx.play('ding', 0, Math.min(8, s.step)); if (window.Juice) Juice.buzz('right');
      const [x, y] = center(v); fx.burst(x, y, {n: 12, colors: ['#ffd23f', '#ff9fc4', '#fff'], speed: 160, life: 0.6, size: 4});
      paintHud();
    } else Play.sfx.play('tick');
    if (s.path.length === s.total) arrived();
  }
  function soft(v, why) { S.shake = {v, t: 0.35, soft: true}; sh.toast(why, 'warn'); Play.sfx.play('nope'); }
  function wrong(v, tok) {
    const s = S;
    s.tries.push(tok);
    s.wrong[s.step] = (s.wrong[s.step] || 0) + 1;
    s.shake = {v, t: 0.5}; fx.shake(3, 0.2);
    Play.sfx.play('wrong');
    truncate(lastFlowerAt());
    if (s.wrong[s.step] >= 2) {   // 同一步错两次：下一朵自己亮起来
      const want = expected();
      s.glow = [...s.flowers].find(([u, i]) => s.tokens[i] === want && !s.path.includes(u) && (u !== s.hive || s.step === s.tokens.length - 1))?.[0] ?? null;
      sh.toast(s.lv.tut ? `下一朵是 ${want}，它亮了` : '下一朵亮起来了，连它', 'warn');
    } else sh.toast(s.lv.tut ? `还不是 ${tok}，按顺序找 ${want}` : `还不是 ${tok}，再想想${s.item && s.item.say ? ' 🔊' : ''}`, 'warn');
    paintHud();
  }
  function help() {   // 帮帮我：外壳盖上题目，答对 1 道（onRevive）
    if (!S || S.mode !== 'play' || !c || c.reviving) return;
    S.helps++;
    c.die();
  }
  function showHint() {   // 线退回到和走得满的那条路一样的一段，后面 3 格亮起来
    const s = S, sol = s.lv.path;
    let p = 0;
    while (p < s.path.length && s.path[p] === sol[p]) p++;
    if (p < s.path.length) { s.path.length = Math.max(1, p); recount(); dirty = true; }
    s.hint = {cells: sol.slice(s.path.length, s.path.length + HINT_N), t: HINT_S};
    sh.toast(p < s.path.length ? '线退回来一点，跟着亮的 3 格走' : '跟着亮的 3 格走', 'good');
    paintHud();
  }

  // ------------------------------------------------------------ 输入：拖、点、方向键
  function cellAtPx(px, py) {
    const g = GEO; if (!g) return null;
    let best = null, bd = Infinity;
    for (let i = 0; i < S.n; i++) {
      const [x, y] = cellCenter(i), d = (x - px) ** 2 + (y - py) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    return bd <= (g.cs * 0.62) ** 2 ? best : null;
  }
  let drag = null;
  function toLogic(e) { const r = sh.canvas.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * stage.W, (e.clientY - r.top) / r.height * stage.H]; }
  function pdown(e) {
    Play.sfx.unlock();
    if (!S || S.mode !== 'play' || Play.input.blocked || e.button > 0) return;
    e.preventDefault();
    const [x, y] = toLogic(e), cell = cellAtPx(x, y);
    if (cell == null) return;
    sh.canvas.setPointerCapture && sh.canvas.setPointerCapture(e.pointerId);
    drag = {x, y};
    const at = S.path.findIndex(v => cellOf(v) === cell);
    if (at >= 0) truncate(at); else onCell(cell);
  }
  function pmove(e) {
    if (!drag || !S || S.mode !== 'play') return;
    const [x, y] = toLogic(e), d = Math.hypot(x - drag.x, y - drag.y), st = Math.max(1, Math.ceil(d / (GEO.cs / 4)));
    for (let i = 1; i <= st; i++) onCell(cellAtPx(drag.x + (x - drag.x) * i / st, drag.y + (y - drag.y) * i / st));   // 拖得快也一格一格走
    drag = {x, y};
  }
  function pup() { drag = null; }
  function keyStep(dx, dy) {   // 方向键：往这个方向最近的相邻格
    const s = S, h = head(), [hx, hy] = center(h);
    let best = null, bs = 0.5;
    const prev = s.path.length >= 2 ? s.path[s.path.length - 2] : -1;
    for (const v of [...s.adj.get(h), ...(prev >= 0 ? [prev] : [])]) {
      if (s.path.includes(v) && v !== prev) continue;
      const [x, y] = center(v), L = Math.hypot(x - hx, y - hy) || 1, dot = ((x - hx) * dx + (y - hy) * dy) / L;
      if (dot > bs && L < GEO.cs * 1.6) { bs = dot; best = v; }
    }
    if (best == null) return;
    if (best === prev) truncate(s.path.length - 2); else extend(best);
  }

  // ------------------------------------------------------------ 每帧
  function update(dt) {
    clock += dt; fx.update(dt);
    const s = S; if (!s) return;
    if (s.shake && (s.shake.t -= dt) <= 0) s.shake = null;
    if (s.hint && (s.hint.t -= dt) <= 0) s.hint = null;
    if (s.bloomT >= 0) s.bloomT += dt;
    if (s.mode === 'play' && !Play.input.blocked) {
      if (Play.input.take('left')) keyStep(-1, 0);
      if (Play.input.take('right')) keyStep(1, 0);
      if (Play.input.take('up')) keyStep(0, -1);
      if (Play.input.take('down')) keyStep(0, 1);
      if (Play.input.take('undo') && s.path.length > 1) truncate(s.path.length - 2);
      if (Play.input.take('help')) help();
    }
    if (s.mode === 'play' && c && c.elapsed() >= s.limit) { s.mode = 'done'; sh.toast('⏰ 时间到！', 'warn'); setTimeout(() => report('lose'), 900); }
    if (c && c.net && dirty && (sendT -= dt) <= 0 && s.mode === 'play') { sendT = 0.3; dirty = false; c.net.send({t: 'lk', p: s.path}); }
    if (hud && s.mode !== 'preview') hudTick();
  }

  // ------------------------------------------------------------ 画面
  function geo() {   // 花园放中间；宽屏右边是大家的小窗；上面让给题卡
    const W = stage.W, H = stage.H, wide = W >= 860, lv = S.lv, hex = lv.kind === 'hex';
    const top = wide ? 206 : 196, bot = wide ? 64 : 70, side = wide && others().length ? 250 : 0;
    const aw = W - 40 - side, ah = H - top - bot;
    // 六边形：竖着尖头朝上，一格宽 cs、外接圆半径 cs/√3，上下一行差 0.866cs，整块高要算上尖头
    const cs = hex ? Math.min(aw / (lv.cols + 0.5), ah / ((lv.rows - 1) * 0.866 + 1.155)) : Math.min(aw / lv.cols, ah / lv.rows);
    const bw = hex ? cs * (lv.cols + 0.5) : cs * lv.cols, bh = hex ? cs * ((lv.rows - 1) * 0.866 + 1.155) : cs * lv.rows;
    const pad = Math.max(10, cs * 0.18);
    return {W, H, wide, cs, hex, bw, bh, pad, side, x0: (W - side - bw) / 2, y0: top + (ah - bh) / 2};
  }
  function cellCenter(i) {
    const g = GEO, cols = S.lv.cols, r = Math.floor(i / cols), q = i % cols;
    if (g.hex) return [g.x0 + (q + 0.5 + (r % 2 ? 0.5 : 0)) * g.cs, g.y0 + g.cs * 0.577 + r * g.cs * 0.866];
    return [g.x0 + (q + 0.5) * g.cs, g.y0 + (r + 0.5) * g.cs];
  }
  const center = v => cellCenter(cellOf(v));

  function render() {
    const s = S, g = GEO = geo(), P = BOARD[s.theme] || BOARD.day;
    stage.begin();
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    Play.scene.sky(ctx, g.W, g.H, s.theme);
    Play.scene.drawClouds(ctx, g.W, s.theme, clock * 3);
    Play.scene.hills(ctx, g.W, g.H, g.H + 20, s.theme, 0);
    board(g, s, P);
    if (g.side) minis(g, P);
    ctx.restore();
    fx.draw(ctx); fx.drawFlash(ctx, g.W, g.H);
  }
  function fit(t, size, maxW) {   // 字号从 size 往下缩，直到宽度放得下
    let f = Math.round(size);
    for (; f > 8; f--) { ctx.font = `900 ${f}px ${FONT}`; if (ctx.measureText(t).width <= maxW) break; }
  }
  function hexPath(x, y, R) {
    ctx.beginPath();
    for (let k = 0; k < 6; k++) { const a = (60 * k - 30) * Math.PI / 180; ctx[k ? 'lineTo' : 'moveTo'](x + R * Math.cos(a), y + R * Math.sin(a)); }
    ctx.closePath();
  }
  function flower(x, y, k, col) {
    ctx.save(); ctx.translate(x, y); ctx.scale(k, k); ctx.lineWidth = 1.6; ctx.strokeStyle = INK;
    for (let p = 0; p < 5; p++) { const a = p * 1.2566; ctx.fillStyle = col; ctx.beginPath(); ctx.arc(Math.cos(a) * 5.5, Math.sin(a) * 5.5, 4.6, 0, 7); ctx.fill(); ctx.stroke(); }
    ctx.fillStyle = '#ffc21a'; ctx.beginPath(); ctx.arc(0, 0, 3.6, 0, 7); ctx.fill(); ctx.stroke();
    ctx.restore();
  }
  function board(g, s, P) {
    const cs = g.cs, pad = g.pad, done = new Set(s.path.map(cellOf));
    const bloomN = s.bloomT >= 0 ? Math.floor(s.bloomT / 1.0 * s.path.length) : -1;   // 过关：花顺着线一路开
    // 框
    ctx.fillStyle = INK; Play.roundRect(ctx, g.x0 - pad, g.y0 - pad + 7, g.bw + pad * 2, g.bh + pad * 2, 26); ctx.fill();
    ctx.fillStyle = P.frame; ctx.strokeStyle = INK; ctx.lineWidth = 4; Play.roundRect(ctx, g.x0 - pad, g.y0 - pad, g.bw + pad * 2, g.bh + pad * 2, 26); ctx.fill(); ctx.stroke();
    // 格子
    const bridges = new Set(s.lv.bridges || []);
    for (let i = 0; i < s.n; i++) {
      const [x, y] = cellCenter(i), r = Math.floor(i / s.lv.cols), q = i % s.lv.cols, lit = done.has(i) || (bloomN >= 0);
      ctx.fillStyle = lit ? ((r + q) % 2 ? P.lit : P.lit2) : ((r + q) % 2 ? P.a : P.b);
      if (g.hex) { hexPath(x, y, cs * 0.577); ctx.fill(); ctx.strokeStyle = 'rgba(58,48,38,.25)'; ctx.lineWidth = 1.5; ctx.stroke(); }
      else { Play.roundRect(ctx, x - cs / 2 + 3, y - cs / 2 + 3, cs - 6, cs - 6, cs * 0.18); ctx.fill(); }
      if (bridges.has(i)) {   // 小溪 + 小桥
        ctx.fillStyle = '#8fd3ff'; ctx.fillRect(x - cs * 0.2, y - cs / 2 + 3, cs * 0.4, cs - 6);
      } else if (!lit && (r * 7 + q * 3) % 5 === 0) {
        ctx.strokeStyle = P.tuft; ctx.lineWidth = 2.4; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(x - cs * 0.3, y + cs * 0.28); ctx.lineTo(x - cs * 0.27, y + cs * 0.18); ctx.lineTo(x - cs * 0.24, y + cs * 0.28); ctx.stroke();
      }
      const bi = bloomN >= 0 ? s.path.findIndex(v => cellOf(v) === i) : -1;
      if ((done.has(i) && bloomN < 0) || (bloomN >= 0 && bi <= bloomN)) {
        const pop = bloomN >= 0 && bi === bloomN ? 1.5 : 1;
        flower(x - cs * 0.3, y - cs * 0.3, cs / 110 * pop, PETALS[(r * 3 + q) % 5]);
        flower(x + cs * 0.3, y + cs * 0.3, cs / 140 * pop, PETALS[(r + q * 2 + 1) % 5]);
      }
    }
    // 提示的 3 格、亮起来的下一朵
    if (s.hint) s.hint.cells.forEach((v, k) => {
      const [x, y] = center(v), a = 0.6 + 0.4 * Math.sin(clock * 6);
      ctx.save(); ctx.globalAlpha = a; ctx.strokeStyle = '#ffb800'; ctx.lineWidth = 4; ctx.setLineDash([9, 6]);
      if (g.hex) hexPath(x, y, cs * 0.45); else Play.roundRect(ctx, x - cs / 2 + 6, y - cs / 2 + 6, cs - 12, cs - 12, cs * 0.2);
      ctx.fillStyle = 'rgba(255,246,194,.8)'; ctx.fill(); ctx.stroke(); ctx.restore();
      ctx.fillStyle = '#ffb800'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(x, y, cs * 0.15, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.font = `900 ${Math.round(cs * 0.18)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(String(k + 1), x, y + 1);
    });
    // 篱笆
    for (const [a, b] of s.lv.walls || []) {
      const [ax, ay] = cellCenter(a), [bx, by] = cellCenter(b), mx = (ax + bx) / 2, my = (ay + by) / 2, h = ay === by;
      const x1 = h ? mx : mx - cs / 2 + 5, x2 = h ? mx : mx + cs / 2 - 5, y1 = h ? my - cs / 2 + 5 : my, y2 = h ? my + cs / 2 - 5 : my;
      ctx.lineCap = 'round'; ctx.strokeStyle = INK; ctx.lineWidth = Math.max(8, cs * 0.12); ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      ctx.strokeStyle = P.fence; ctx.lineWidth = Math.max(4, cs * 0.06); ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
    // 魔法花之间的虚线
    for (const [a, b] of s.lv.warps || []) {
      const [ax, ay] = cellCenter(a), [bx, by] = cellCenter(b);
      ctx.save(); ctx.strokeStyle = '#b89cff'; ctx.lineWidth = 3; ctx.setLineDash([4, 8]); ctx.lineDashOffset = -clock * 20;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.quadraticCurveTo((ax + bx) / 2 - (by - ay) * 0.25, (ay + by) / 2 + (bx - ax) * 0.25, bx, by); ctx.stroke(); ctx.restore();
    }
    // 线：先画从小桥底下竖着过的那几段，再画桥，再画别的
    const line = s.path;
    const segs = [];
    for (let i = 1; i < line.length; i++) segs.push([line[i - 1], line[i]]);
    const under = ([a, b]) => isBridgeSlot(a) || isBridgeSlot(b);
    const warpSeg = ([a, b]) => (s.lv.warps || []).some(([x, y]) => (x === a && y === b) || (x === b && y === a));
    const drawSegs = list => {
      const w = cs * 0.3;
      for (const [col, lw] of [[INK, w + 7], [s.theme === 'night' ? '#ffd84a' : '#ffc21a', w]]) {
        ctx.strokeStyle = col; ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
        for (const sg of list) {
          const [ax, ay] = center(sg[0]), [bx, by] = center(sg[1]);
          ctx.save(); if (warpSeg(sg)) { ctx.setLineDash([2, lw * 0.9]); ctx.globalAlpha = 0.85; }
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke(); ctx.restore();
        }
      }
    };
    drawSegs(segs.filter(under));
    for (const b of s.lv.bridges || []) {   // 木桥：横着的那一次从桥上走
      const [x, y] = cellCenter(b);
      ctx.fillStyle = '#e9c98a'; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      Play.roundRect(ctx, x - cs * 0.46, y - cs * 0.17, cs * 0.92, cs * 0.34, 6); ctx.fill(); ctx.stroke();
      ctx.strokeStyle = '#c9a060'; ctx.lineWidth = 2; for (let k = -2; k <= 2; k++) { ctx.beginPath(); ctx.moveTo(x + k * cs * 0.16, y - cs * 0.17); ctx.lineTo(x + k * cs * 0.16, y + cs * 0.17); ctx.stroke(); }
    }
    drawSegs(segs.filter(sg => !under(sg)));
    for (const [a, b] of s.lv.warps || []) for (const v of [a, b]) { const [x, y] = cellCenter(v); flower(x, y, cs / 46, '#b89cff'); }
    // 花（节点）
    for (const [v, i] of s.flowers) {
      const [x0, y0] = center(v), reached = s.path.includes(v) || s.bloomT >= 0, sk = s.shake && s.shake.v === v ? s.shake : null;
      const x = x0 + (sk ? Math.sin(clock * 60) * 5 : 0), y = y0, R = cs * 0.36, tok = s.tokens[i];
      if (v === s.hive) {   // 蜂巢
        const k = cs / 88;
        ctx.save(); ctx.translate(x, y - cs * 0.06); ctx.scale(k, k); ctx.strokeStyle = INK; ctx.lineWidth = 3.5;
        ctx.fillStyle = reached ? '#ffd84a' : '#ffc21a';
        ctx.beginPath(); ctx.moveTo(0, -30); ctx.bezierCurveTo(18, -30, 28, -18, 28, -2); ctx.bezierCurveTo(28, 14, 18, 22, 0, 22); ctx.bezierCurveTo(-18, 22, -28, 14, -28, -2); ctx.bezierCurveTo(-28, -18, -18, -30, 0, -30); ctx.fill(); ctx.stroke();
        ctx.strokeStyle = '#e09a00'; ctx.lineWidth = 3; for (const yy of [-10, 4]) { ctx.beginPath(); ctx.moveTo(-26, yy); ctx.lineTo(26, yy); ctx.stroke(); }
        ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(0, 14, 8, Math.PI, 0); ctx.lineTo(8, 22); ctx.lineTo(-8, 22); ctx.fill();
        ctx.restore();
        tag(x, y + cs * 0.24, tok, cs, s.glow === v ? '#fff6c2' : '#fff', sk);
        continue;
      }
      if (s.glow === v) { ctx.save(); ctx.globalAlpha = 0.5 + 0.4 * Math.sin(clock * 7); ctx.fillStyle = '#ffd84a'; ctx.beginPath(); ctx.arc(x, y, R + 9, 0, 7); ctx.fill(); ctx.restore(); }
      ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(x, y + 4, R, 0, 7); ctx.fill();
      ctx.fillStyle = sk && !sk.soft ? '#ffe1dc' : reached ? '#ffd84a' : '#fff';
      ctx.strokeStyle = sk && !sk.soft ? '#c2382d' : INK; ctx.lineWidth = 3.5; ctx.beginPath(); ctx.arc(x, y, R, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      fit(tok, cs * (tok.length > 2 ? 0.22 : 0.3), R * 1.7);   // 算式长：字缩到花心里放得下
      ctx.fillText(tok, x, y + 1);
      if (reached && s.vals && s.vals[i] != null) {   // 连到了：亮出得数
        const t = String(s.vals[i]), w = Math.max(cs * 0.4, t.length * cs * 0.11 + cs * 0.16);
        ctx.fillStyle = '#1f9d55'; ctx.strokeStyle = INK; ctx.lineWidth = 2; Play.roundRect(ctx, x - w / 2, y - R - cs * 0.2, w, cs * 0.22, cs * 0.11); ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#fff'; ctx.font = `900 ${Math.round(cs * 0.16)}px ${FONT}`; ctx.fillText(t, x, y - R - cs * 0.09);
      }
      if (i === 0 && s.path.length === 1 && s.mode !== 'preview') {
        ctx.fillStyle = INK; Play.roundRect(ctx, x - cs * 0.26, y + R - 6, cs * 0.52, cs * 0.22, cs * 0.11); ctx.fill();
        ctx.fillStyle = '#fff'; ctx.font = `700 ${Math.round(cs * 0.14)}px ${FONT}`; ctx.fillText('起点', x, y + R + cs * 0.05);
      }
    }
    // 小蜜蜂：在线头上；过关时飞回蜂巢欢呼
    const hv = s.bloomT >= 0 || s.mode === 'win' ? s.hive : head(), [bx, by] = center(hv);
    drawBee(ctx, bx + cs * 0.38, by - cs * 0.44 + Math.sin(clock * 4) * 2, cs / 150, {pose: s.mode === 'win' ? 'cheer' : 'fly', col: myColor(), rot: 0.14});
  }
  function tag(x, y, t, cs, bg, sk) {
    const w = Math.max(cs * 0.6, String(t).length * cs * 0.13 + cs * 0.2);
    ctx.fillStyle = bg; ctx.strokeStyle = sk && !sk.soft ? '#c2382d' : INK; ctx.lineWidth = 2.5;
    Play.roundRect(ctx, x - w / 2, y - cs * 0.13, w, cs * 0.26, cs * 0.13); ctx.fill(); ctx.stroke();
    ctx.fillStyle = INK; ctx.font = `900 ${Math.round(cs * 0.17)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(t, x, y + 1);
  }
  // 宽屏右边：大家的小花园。每人的花园是同一关换了个样子（seatOf），小窗按他自己的花园画；
  // 只亮他走过的格子、标出他的小蜜蜂在哪，不画线——看得出谁连到哪了，抄不了路
  function minis(g, P) {
    const ps = others().filter(p => mates[p.id] && mates[p.id].p);
    const x = g.W - g.side + 10, w = g.side - 30;
    ps.forEach((p, k) => {
      const m = mates[p.id], y = 120 + k * (w + 44), lv = seatOf(S.base, p.role), n = lv.rows * lv.cols;
      const cs = w / Math.max(lv.cols, lv.rows), done = new Set(m.p.map(v => v % n));
      ctx.fillStyle = INK; Play.roundRect(ctx, x - 6, y - 6 + 5, w + 12, w + 12, 14); ctx.fill();
      ctx.fillStyle = P.frame; ctx.strokeStyle = INK; ctx.lineWidth = 3; Play.roundRect(ctx, x - 6, y - 6, w + 12, w + 12, 14); ctx.fill(); ctx.stroke();
      const cc = i => [x + (i % lv.cols + 0.5 + (lv.kind === 'hex' && Math.floor(i / lv.cols) % 2 ? 0.5 : 0)) * cs * (lv.kind === 'hex' ? 0.92 : 1), y + (Math.floor(i / lv.cols) + 0.5) * cs];
      const col = (BODY[p.role] || BODY.sun)[0];
      for (let i = 0; i < n; i++) { const [cx, cy] = cc(i); ctx.fillStyle = done.has(i) ? P.lit2 : (i % 2 ? P.a : P.b); ctx.fillRect(cx - cs / 2 + 1.5, cy - cs / 2 + 1.5, cs - 3, cs - 3); }
      if (m.p.length && !m.done) {   // 他的小蜜蜂在哪
        const [hx, hy] = cc(m.p[m.p.length - 1] % n);
        ctx.fillStyle = col; ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(hx, hy, cs * 0.3, 0, 7); ctx.fill(); ctx.stroke();
      }
      const pct = Math.round(m.p.length / S.total * 100);
      ctx.fillStyle = INK; ctx.font = `800 15px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      ctx.fillText(`${m.done ? '🏁 ' : ''}${p.name} · ${pct}%`, x + w / 2, y + w + 26);
    });
  }
  /* 小蜜蜂（和左右飞同一个画法）：k 缩放，pose fly / cheer */
  function drawBee(cx, x, y, k, o) {
    const [c1, c2] = BODY[o.col] || BODY.sun, pose = o.pose || 'fly', rx = 24, ry = 28;
    cx.save(); cx.translate(x, y); cx.rotate(o.rot || 0); cx.scale(k, k);
    cx.lineJoin = 'round'; cx.lineCap = 'round'; cx.strokeStyle = INK;
    for (const d of [-1, 1]) {
      cx.save(); cx.translate(d * 16, -ry + 2); cx.rotate(d * (0.5 + Math.sin(clock * 30) * 0.25));
      cx.beginPath(); cx.ellipse(0, -4, 15, 20, 0, 0, Math.PI * 2); cx.fillStyle = 'rgba(234,247,255,.95)'; cx.fill(); cx.lineWidth = 3; cx.stroke();
      cx.restore();
    }
    cx.lineWidth = 3.5;
    for (const d of [-1, 1]) { cx.beginPath(); cx.moveTo(d * 8, -ry + 3); cx.quadraticCurveTo(d * 12, -ry - 12, d * 22, -ry - 17); cx.stroke(); cx.fillStyle = INK; cx.beginPath(); cx.arc(d * 22, -ry - 17, 5, 0, Math.PI * 2); cx.fill(); }
    cx.lineWidth = 4;
    for (const d of [-1, 1]) { cx.beginPath(); cx.moveTo(d * (rx - 3), -4); cx.quadraticCurveTo(d * (rx + 11), pose === 'cheer' ? -14 : -4, d * (rx + 11), pose === 'cheer' ? -28 : 6); cx.stroke(); cx.fillStyle = c1; cx.beginPath(); cx.arc(d * (rx + 11), pose === 'cheer' ? -28 : 6, 4.5, 0, Math.PI * 2); cx.fill(); cx.lineWidth = 2.5; cx.stroke(); cx.lineWidth = 4; }
    cx.save(); cx.beginPath(); cx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); cx.fillStyle = c1; cx.fill(); cx.clip();
    cx.fillStyle = INK; cx.fillRect(-40, ry * 0.32, 80, ry * 0.28); cx.fillRect(-40, ry * 0.78, 80, 40);
    cx.fillStyle = 'rgba(255,255,255,.45)'; cx.beginPath(); cx.ellipse(-rx * 0.35, -ry * 0.55, rx * 0.35, ry * 0.22, 0, 0, Math.PI * 2); cx.fill();
    cx.globalAlpha *= 0.18; cx.fillStyle = c2; cx.beginPath(); cx.ellipse(0, ry * 0.2, rx, ry * 0.7, 0, 0, Math.PI * 2); cx.fill();
    cx.restore();
    cx.lineWidth = 3.5; cx.beginPath(); cx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2); cx.stroke();
    cx.save(); cx.translate(0, -ry * 0.18);
    for (const d of [-1, 1]) {
      cx.fillStyle = '#fff'; cx.beginPath(); cx.ellipse(d * 9.5, 0, 7.5, 8.5, 0, 0, Math.PI * 2); cx.fill(); cx.lineWidth = 2.5; cx.stroke();
      cx.fillStyle = INK; cx.beginPath(); cx.arc(d * 9.5 + 1, -1, 4.3, 0, Math.PI * 2); cx.fill(); cx.fillStyle = '#fff'; cx.beginPath(); cx.arc(d * 9.5 + 2.5, -2.5, 1.6, 0, Math.PI * 2); cx.fill();
      cx.fillStyle = 'rgba(255,138,138,.7)'; cx.beginPath(); cx.ellipse(d * 17, 10, 5, 3, 0, 0, Math.PI * 2); cx.fill();
    }
    cx.lineWidth = 2.5; cx.beginPath(); cx.moveTo(-7, 9); cx.quadraticCurveTo(0, 19, 7, 9); cx.closePath(); cx.fillStyle = '#c4413a'; cx.fill(); cx.stroke();
    cx.restore();
    cx.fillStyle = INK; cx.beginPath(); cx.moveTo(-5, ry - 1); cx.lineTo(0, ry + 9); cx.lineTo(5, ry - 1); cx.closePath(); cx.fill();
    cx.restore();
  }
  const myColor = () => (c && c.me && c.me.role) || S.avatar || 'sun';
  window.LinkArt = {state: () => S, px: v => {   // 截图、调试用：点 v 在页面上的位置
    const r = sh.canvas.getBoundingClientRect(), [x, y] = center(v);
    return [r.left + x / stage.W * r.width, r.top + y / stage.H * r.height];
  }};

  // ------------------------------------------------------------ 上面的题卡、下面的按钮
  function buildHud() {
    if (!hudEl) { hudEl = document.createElement('div'); sh.canvas.parentNode.insertBefore(hudEl, sh.hud.nextSibling); }
    hudEl.className = 'lk-layer' + (c && c.net ? ' mp' : '');
    hudEl.innerHTML = `<div class="lk-top"><span class="lk-side"><span class="lk-chip lk-time"></span><span class="lk-chip lk-cells"></span></span>
      <div class="lk-q"><div class="lk-topic"></div><div class="lk-text"><span></span><button type="button" class="lk-say" aria-label="再听一遍">🔊</button></div><div class="lk-got"></div></div><span></span></div>
      <div class="lk-bot"><span class="lk-tip"></span><button type="button" class="lk-help">🆘 帮帮我</button></div>`;
    const $ = q => hudEl.querySelector(q);
    hud = {time: $('.lk-time'), cells: $('.lk-cells'), q: $('.lk-q'), topic: $('.lk-topic'), text: $('.lk-text span'), got: $('.lk-got'), say: $('.lk-say'), tip: $('.lk-tip'), help: $('.lk-help'), last: ''};
    hud.say.onclick = e => { e.stopPropagation(); if (S.item && S.item.say && typeof Speak !== 'undefined') Speak.play(S.item.say, 'en', 'word'); };
    hud.help.onclick = e => { e.stopPropagation(); help(); };
    hud.tip.textContent = touchy ? '👆 按住小蜜蜂拖着走 · 往回拖就撤销' : '🖱 按住小蜜蜂拖着走 · 往回拖撤销 · 方向键也能走';
    paintHud();
  }
  function paintHud() {
    if (!hud || !S) return;
    const s = S, it = s.item, seq = it && it.seq;
    hud.q.classList.toggle('on', !!s.tokens.length);
    hud.topic.textContent = s.lv.tut ? '学会怎么玩' : `${(it && it.topic) || '这一关'} · 这一关的顺序`;
    hud.text.textContent = s.lv.tut || !it ? '按 1、2、3 的顺序连' : seq.zh ? `拼出「${seq.zh}」` : seq.rule || it.q;
    hud.say.hidden = !(it && it.say);
    if (seq && seq.zh) {   // 拼单词：空格随连随填
      hud.got.innerHTML = s.tokens.map((t, i) => i < s.step ? `<span class="on">${esc(t)}</span>` : '<span>_</span>').join(' ');
    } else if (s.vals) {
      hud.got.innerHTML = `<span class="on">${s.vals.slice(0, s.step).map(esc).join(' &lt; ')}</span>${s.step < s.tokens.length ? ' &lt; ?' : ''}`;
    } else hud.got.innerHTML = s.tokens.map((t, i) => `<span${i < s.step ? ' class="on"' : ''}>${esc(t)}</span>`).join(' → ');
    hud.help.disabled = s.mode !== 'play';
  }
  function hudTick() {
    const s = S, k = `${Math.floor(c ? c.elapsed() : 0)}|${s.path.length}`;
    if (hud.last === k) return;
    hud.last = k;
    const t = Math.floor(c ? c.elapsed() : 0);
    hud.time.textContent = `⏱ ${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
    hud.cells.textContent = `🌼 ${s.path.length} / ${s.total} 格`;
    hud.help.disabled = s.mode !== 'play';
  }

  // ------------------------------------------------------------ 走满了：交答案、几个人一起比谁先
  const others = () => c && c.peers ? c.peers : [];
  const seconds = () => c ? c.elapsed() : 0;
  async function arrived() {
    const s = S;
    s.mode = 'win'; s.glow = null; s.hint = null;
    Play.sfx.play('chord', 0, 3);
    if (!s.lv.tut && s.item && !s.lost) {
      const a = await sh.gateSend(s.item, s.tries.join('|'));
      s.gatesOk = a.lost || a.correct ? 1 : 0;   // 没交上（断网）当作看答案过，不算错
    } else s.gatesOk = 1;
    if (S !== s) return;
    const me = {secs: +seconds().toFixed(1), ok: s.gatesOk};
    if (!others().length) return report('win');
    mates[c.me.id] = Object.assign(mates[c.me.id] || {}, {done: me, p: s.path.slice()});
    c.net.send({t: 'lk', p: s.path, done: me});
    const k = place(c.me.id);
    sh.toast(k === 1 ? '🥇 第一个走满！看看大家连到哪了' : `第 ${k} 个走满！给还在连的人加油吧`, 'good');
    allIn();
  }
  function place(id) { const d = mates[id] && mates[id].done; return d ? 1 + Object.values(mates).filter(m => m.done && m.done.secs < d.secs).length : 0; }
  function onMate(m) {
    const p = others().find(x => x.id === m.from); if (!p) return;
    const was = mates[m.from] && mates[m.from].done;
    mates[m.from] = Object.assign(mates[m.from] || {}, {name: p.name, role: p.role, p: Array.isArray(m.p) ? m.p.slice(0, 200) : []}, m.done ? {done: m.done} : {});
    if (m.done && !was) { sh.toast(`${place(m.from) === 1 ? '🥇 ' : ''}${p.name} 走满啦！`, 'good'); allIn(); }
  }
  function allIn() {
    if (!c.host || S.mode !== 'win') return;
    const ids = [c.me.id, ...others().map(p => p.id)];
    if (!ids.every(id => mates[id] && mates[id].done)) return;
    const d = ids.map(id => mates[id].done);
    report('win', Math.max(...d.map(x => x.secs)), d.reduce((a, x) => a + x.ok, 0));
  }
  function ranking() {
    const rows = Object.entries(mates).filter(([, m]) => m.done).sort((a, b) => a[1].done.secs - b[1].done.secs);
    return rows.map(([id, m], i) => `${['🥇', '🥈', '🥉', '4️⃣'][i]} ${+id === c.me.id ? '我' : esc(m.name)} ${m.done.secs}s`).join(' · ');
  }
  function report(result, teamSecs, teamOk) {
    const s = S;
    if (s.sent) return;
    s.sent = true;
    if (teamSecs != null) return c.finish(result, {seconds: teamSecs, gates: teamOk}, null);
    const secs = +seconds().toFixed(1), best = s.lv.best;
    const order = s.vals ? s.vals.join(' → ') : s.tokens.join(s.item && s.item.seq && s.item.seq.zh ? '' : ' → ');
    const title = result === 'win'
      ? `🌼 花园开满啦！<small class="lk-sub">⏱ ${secs} 秒${best != null ? (secs < best ? ` · 新纪录，快了 ${(best - secs).toFixed(1)} 秒` : ` · 纪录 ${best} 秒`) : ''} · ${s.lv.tut ? '学会啦' : s.gatesOk ? '每一步都一次连对' : '这次有连错的，下次一次连对'}${s.lv.tut ? '' : ` · ${esc(order)}`}</small>`
      : `⏰ 时间到，走了 ${s.path.length} / ${s.total} 格<small class="lk-sub">下次接着连</small>`;
    c.finish(result, {seconds: secs, gates: s.gatesOk, far: s.path.length / s.total}, title);
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = Play.stage(sh.canvas, {height: 760, minW: 360, maxW: 2000});
    ctx = stage.ctx;
    S = fresh(null, sh.data.avatar); plant(['1', '2', '3', '4']);
    const cv = sh.canvas;
    cv.style.touchAction = 'none';
    cv.addEventListener('pointerdown', pdown);
    cv.addEventListener('pointermove', pmove);
    cv.addEventListener('pointerup', pup); cv.addEventListener('pointercancel', pup);
    Play.loop(update, render).start();
  }
  function ready() {
    S = fresh(S && S.mode !== 'preview' ? S.base : null, sh.data.avatar); plant(['1', '2', '3', '4']);
    fx.parts = []; fx.texts = []; hud = null; mates = {};
    if (hudEl) hudEl.innerHTML = '';
  }
  function begin(cx) {
    c = cx; mates = {}; sendT = 0; dirty = true;
    if (cx.net) { cx.net.off('lk'); cx.net.on('lk', onMate); }
    S = fresh(cx.level && cx.level.path ? cx.level : null, (cx.match && cx.match.avatar) || sh.data.avatar, cx.me && cx.me.role);
    S.limit = Math.max(30, Math.min(600, (cx.match && !cx.match.unlimited && cx.match.seconds_left) || 600));
    Play.input.clear();
    buildHud();
    loadSeq();
    sh.toast(others().length ? '每人一个花园，一样大、一样难，看谁先走满！' : S.lv.tut ? '从小蜜蜂出发，按 1、2、3 连，走满每一格！' : '想好顺序，从小蜜蜂出发！', 'good');
  }

  const rules = [
    '按住<b>小蜜蜂</b>拖着走，一笔走满花园的<b>每一格</b>，最后回到<b>蜂巢</b>',
    '花上写的是要学的东西：<b>连的顺序就是答案</b>（按得数从小到大、按字母拼出单词……）',
    '连错了花会晃一晃，线退回上一朵对的花；<b>同一步错两次，下一朵自己亮起来</b>',
    '往回拖就撤销；点线上哪一格，就退回到那一格。电脑上方向键也能走，退格撤销',
    '卡住了点「<b>帮帮我</b>」：答对 1 道题，后面 3 格亮起来',
    '篱笆过不去；小桥那一格横着、竖着能各走一次；两朵<b>魔法花</b>之间能直接连过去',
    '几个人一起：<b>每人一个花园</b>（同一关转一转、翻一翻，一样难），<b>谁先走满谁第一</b>，大家都走满才过关',
  ];

  PlayShell.init({
    quiz: 'revive', tempo: 104, rules, moreHint: '一起比谁先走满',
    keys: '电脑：鼠标拖，或者方向键走；退格 / Z 撤销；H 帮帮我；P 暂停',
    input: {left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], up: ['ArrowUp', 'w'], down: ['ArrowDown', 's'], undo: ['Backspace', 'z'], help: ['h']},
    revive: {verb: '点亮后面 3 格', done: '✨ 点亮啦！'},
    noQuiz: '这一关没用「帮帮我」，自己连出来的！',
    setup: boot, ready, begin,
    end() { if (S) S.mode = S.mode === 'win' ? 'win' : 'done'; drag = null; if (hudEl) hudEl.innerHTML = ''; hud = null; },
    finale() { if (S) { S.bloomT = 0; S.mode = 'win'; } },   // 过关仪式：花从起点到蜂巢一路开
    verdict(res, result) {
      if (result !== 'win' || !others().length) return {};
      const k = place(c.me.id);
      return {title: `${k === 1 ? '🥇 我第一个走满！' : `🎉 大家都走满了，我第 ${k} 个`}<small class="lk-sub">${ranking()}</small>`};
    },
    onRevive() { if (S) showHint(); },
    pause(on) { if (on) { Play.input.clear(); drag = null; } },
  });
})();

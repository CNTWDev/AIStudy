/* 翻花田：花田的草皮下面睡着小刺猬。翻开草皮看数字（周围睡着几只），推出刺猬在哪就插一朵小花，安全的格子全翻开就过关。
   关卡（app/arena/games/mines.py）：方格 sq / 六边形 hex，糖石 holes（不是格子）、刺猬 mines {格子: 几只}（大刺猬算 2 只）、
     地道 tunnels（两头也算挨着）、第一下 start、题格 qs、蜂蜜 honey。每关都用求解器确认过：不用猜。
   操作：点 = 翻开（插花模式下是插花）；电脑右键插花，手机长按插花，下面两个大按钮切「翻开 / 插花」；
     点一个数字，它周围插的花和数字一样多，剩下的一起翻开。大刺猬的世界里插花是 1 朵、2 朵、拔掉轮着来。
   第一下只能点小蜜蜂标着的那一格：一定安全，一下翻开一大片（波浪一圈圈推出去，草皮蹦起来，音一圈圈升高）。
   题格（金色花苞，下面一定安全）：点它出一道题（sh.gateAsk，shape = choices），题卡盖在花田上，想多久都行。
     答对：探照花开，周围安全的格子翻开、刺猬自动插上金色小花，再给蜂蜜；答错再选；连错两次亮出答案，点它照样开（不算星）。
     翻开一大片的波浪不会替你翻开题格，题格要自己点；全部打开才过关。
   翻到刺猬 = 把它吵醒（不爆炸、不清盘）：外壳盖上题目，答对 1 道（共用的复活规矩）它又睡着，这一格插上小花，接着玩。
   过关仪式里（finale）：刺猬一只只醒来挥手，身边开出小花。
   几个人一起（最多 4 个）：每人一块自己的花田（lv.seats），每隔一会儿告诉大家我翻了多少、在干什么（mn），
     宽屏右边小窗只画百分比和状态，不画格子；谁先翻完谁第一，大家都翻完由开房间的人报过关。
   设计：docs/扫雷-讨论稿（项目文件），设计稿 ui-2026-11-11-mines/。 */
(function () {
  const INK = (window.LOOK && window.LOOK.ink) || '#3a3026';
  const FONT = '"Baloo 2", "PingFang SC", system-ui, sans-serif';
  const BODY = {sun: ['#ffcf2e', '#f0a800'], berry: ['#ff7a8a', '#e24a5f'], mint: ['#6fe0b0', '#2fb886'], sky: ['#7cc4ff', '#3a8fe0']};
  const TILE = {   // 每个世界的草皮、泥土、框、草叶
    day: {g1: '#a6e37f', g2: '#78c656', soil: '#fff2d2', soil2: '#f3dfae', frame: '#fff7e0', blade: '#5fae43'},
    candy: {g1: '#ffc6e2', g2: '#f59ac6', soil: '#fff6fb', soil2: '#f7d7e8', frame: '#fff4fa', blade: '#e276ad'},
    sunset: {g1: '#ffc08a', g2: '#f08e56', soil: '#fff1e2', soil2: '#f2d2b4', frame: '#fff3e6', blade: '#d4703a'},
    night: {g1: '#8fb2ff', g2: '#5d7fe6', soil: '#e9efff', soil2: '#c9d4f5', frame: '#dfe6ff', blade: '#4a67c9'},
    lava: {g1: '#a08a80', g2: '#6e5a52', soil: '#ffe9d2', soil2: '#efc9a6', frame: '#ffe6cf', blade: '#4a3a34'},
  };
  const NUMC = ['', '#2f7fd6', '#2b9a4e', '#e2463e', '#7a4fd0', '#b5651d', '#1fa2a2', '#3a3026', '#777'];
  const PETALS = ['#ff7aa8', '#b89cff', '#ff9e5e', '#7cc4ff', '#ffffff'];
  const WAVE = 0.035, POP = 0.32, LONG = 420;   // 波浪一圈晚多少秒、草皮蹦起来多久、长按多久算插花

  let sh = null, stage = null, ctx = null, c = null, hudEl = null, hud = null;
  const fx = new Play.FX();
  let S = null, clock = 0, GEO = null;
  let mates = {}, sendT = 0, dirty = false;
  const touchy = matchMedia('(pointer: coarse)').matches;
  const esc = s => String(s).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));

  // ------------------------------------------------------------ 关卡 → 图（和 mines.py 的 graph 一样）
  function hexNbs(r, q, rows, cols) {
    const d = r % 2 ? [[-1, 0], [-1, 1], [1, 0], [1, 1]] : [[-1, -1], [-1, 0], [1, -1], [1, 0]];
    return [[0, -1], [0, 1], ...d].map(([a, b]) => [r + a, q + b]).filter(([a, b]) => a >= 0 && a < rows && b >= 0 && b < cols);
  }
  function graph(lv) {
    const {rows, cols} = lv, hex = lv.kind === 'hex', holes = new Set(lv.holes || []), adj = new Map();
    for (let i = 0; i < rows * cols; i++) {
      if (holes.has(i)) continue;
      const r = Math.floor(i / cols), q = i % cols, out = [];
      const nbs = hex ? hexNbs(r, q, rows, cols) : [];
      if (!hex) for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) if ((a || b) && r + a >= 0 && r + a < rows && q + b >= 0 && q + b < cols) nbs.push([r + a, q + b]);
      for (const [rr, qq] of nbs) { const j = rr * cols + qq; if (!holes.has(j)) out.push(j); }
      adj.set(i, out);
    }
    for (const [a, b] of lv.tunnels || []) if (!adj.get(a).includes(b)) { adj.get(a).push(b); adj.get(b).push(a); }
    return adj;
  }
  const DEMO = {kind: 'sq', rows: 7, cols: 7, holes: [], tunnels: [], mines: {'0': 1, '6': 1, '15': 1, '40': 1, '47': 1}, start: 24, qs: [3], honey: [30], seed: 5};
  const seatOf = (lv, role) => (lv && lv.seats && lv.seats[role]) ? {...lv, ...lv.seats[role]} : lv;

  function fresh(base, avatar, role) {
    base = base && base.mines ? base : DEMO;
    const lv = seatOf(base, role), adj = graph(lv), mines = new Map(Object.entries(lv.mines || {}).map(([k, v]) => [+k, v]));
    const num = new Map(); for (const [i, nb] of adj) num.set(i, nb.reduce((a, j) => a + (mines.get(j) || 0), 0));
    const qs = new Map((lv.qs || []).map(i => [i, {st: 'bud', item: null, tried: new Set(), right: -1, miss: 0, busy: false}]));
    const safe = [...adj.keys()].filter(i => !mines.has(i));
    return {mode: 'preview', base, lv, adj, mines, num, qs, safe, total: [...mines.values()].reduce((a, b) => a + b, 0),
            avatar: avatar || 'sun', theme: (lv.world && lv.world.scene) || 'day', big: !!lv.big,
            open: new Set(), at: new Map(), popped: new Set(), flags: new Map(), gold: new Set(), caught: new Set(), woke: -1,
            honey: new Set(lv.honey || []), got: 0, started: false, flagMode: false, asking: null, beams: [], rings: [],
            qMiss: 0, wakes: 0, bloomT: -1, limit: 600, sent: false, winAt: -1, shake: null, last: lv.start, half: 0};
  }
  const theme = () => TILE[S.theme] || TILE.day;
  const covered = i => !S.open.has(i) && !S.caught.has(i) && S.woke !== i;
  const flowersOn = i => S.gold.has(i) || S.caught.has(i) ? (S.mines.get(i) || 1) : (S.flags.get(i) || 0);
  const leftHogs = () => S.total - [...S.adj.keys()].reduce((a, i) => a + flowersOn(i), 0);
  const openedSafe = () => S.safe.filter(i => S.open.has(i)).length;
  const pct = () => Math.round(openedSafe() / Math.max(1, S.safe.length) * 100);

  // ------------------------------------------------------------ 翻开：从 i0 起一圈圈推出去（数字 0 的格子接着往外翻；题格、插了花的不翻）
  function reveal(i0, from) {
    const s = S, seen = new Set([i0]), todo = [[i0, 0]], fresh = [];
    let far = 0;
    while (todo.length) {
      const [i, d] = todo.shift();
      if (s.open.has(i) || s.mines.has(i) || s.qs.has(i) && i !== i0 || (s.flags.get(i) && i !== i0)) continue;
      s.open.add(i); s.at.set(i, clock + d * WAVE); fresh.push(i); far = Math.max(far, d);
      if (s.num.get(i) === 0) for (const j of s.adj.get(i)) if (!seen.has(j)) { seen.add(j); todo.push([j, d + 1]); }
    }
    if (!fresh.length) return 0;
    for (let d = 0; d <= Math.min(far, 10); d++) s.rings.push({t: clock + d * WAVE, n: d + (from || 0)});
    if (fresh.length >= 8) {
      const [x, y] = center(i0);
      fx.ring(x, y, {color: '#ffffff', width: 6, grow: 700, life: 0.6});
      setTimeout(() => { if (S === s) { sh.toast(`一口气翻开 ${fresh.length} 格！`, 'good'); if (window.Juice) Juice.buzz('right'); } }, far * WAVE * 1000 + 200);
    }
    dirty = true;
    checkWin(far);
    return fresh.length;
  }
  function pop(i) {   // 这一格的草皮蹦起来了：草屑、蜂蜜
    const s = S, [x, y] = center(i), cs = GEO ? GEO.cs : 40;
    s.popped.add(i);
    fx.burst(x, y - cs * 0.1, {n: 5, colors: [theme().g1, theme().g2, '#c99a5e'], speed: 170, life: 0.5, size: Math.max(2.5, cs * 0.06), grav: 700});
    if (s.honey.has(i)) {
      s.honey.delete(i); s.got++;
      fx.text(x, y - cs * 0.2, '+1 🍯', {color: '#e08a00', size: Math.max(18, cs * 0.42)});
      Play.sfx.play('coin');
    }
  }
  function checkWin(far) {
    const s = S;
    if (s.mode !== 'play' || s.safe.some(i => !s.open.has(i))) return;
    s.mode = 'clearing'; s.winAt = clock + (far || 0) * WAVE + POP + 0.35;
  }

  // ------------------------------------------------------------ 点一格
  function tap(i, flag) {
    const s = S;
    if (s.mode !== 'play' || i == null || (c && c.reviving) || s.asking) return;
    if (!s.started) {
      if (i !== s.lv.start) { soft(i, '先点小蜜蜂标着的那一格，一下翻开一大片'); return; }
      s.started = true; s.last = i; Play.sfx.play('pop');
      return reveal(i);
    }
    if (flag || s.flagMode) return plant(i);
    if (s.open.has(i)) return chord(i);
    if (s.qs.has(i)) return askQ(i);
    if (!covered(i)) return;
    if (s.flags.get(i) || s.gold.has(i)) return soft(i, '插着花呢：觉得不是刺猬，先拔掉花再翻');
    s.last = i;
    if (s.mines.has(i)) return wake(i);
    Play.sfx.play('tap');
    reveal(i);
  }
  function plant(i) {
    const s = S;
    if (!covered(i) || s.gold.has(i)) return;
    if (s.qs.has(i)) return soft(i, '金色花苞下面一定没有刺猬，点它答题');
    const n = s.flags.get(i) || 0, nx = n >= (s.big ? 2 : 1) ? 0 : n + 1;
    if (nx) s.flags.set(i, nx); else s.flags.delete(i);
    const [x, y] = center(i);
    if (nx) { fx.burst(x, y - 6, {n: 7, colors: PETALS, speed: 120, life: 0.45, size: 3.5, grav: 300}); Play.sfx.play('pop'); if (window.Juice) Juice.buzz('tap'); }
    else Play.sfx.play('tick');
    s.grow = {i, t: 0};
    paintHud();
  }
  function chord(i) {   // 点数字：周围插的花和它一样多，剩下的一起翻开
    const s = S, nb = s.adj.get(i), n = s.num.get(i);
    if (!n) return;
    const have = nb.reduce((a, j) => a + flowersOn(j), 0);
    const todo = nb.filter(j => covered(j) && !flowersOn(j) && !s.qs.has(j));
    if (!todo.length) return;
    if (have !== n) { s.shake = {i, t: 0.3, soft: true}; Play.sfx.play('nope'); return; }
    const hog = todo.find(j => s.mines.has(j));
    if (hog != null) { s.last = hog; return wake(hog); }
    Play.sfx.play('tap');
    let k = 0; for (const j of todo) k += reveal(j, 1);
    s.hop = {i, t: 0};
  }
  function soft(i, why) { S.shake = {i, t: 0.35, soft: true}; sh.toast(why, 'warn'); Play.sfx.play('nope'); }
  function wake(i) {   // 吵醒了：刺竖起来，外壳盖上题目，答对 1 道接着玩
    const s = S, [x, y] = center(i);
    s.woke = i; s.wakes++; s.wakeT = 0;
    fx.shake(7, 0.3); fx.burst(x, y, {n: 14, colors: ['#ff8a7a', '#ffd0c8', '#fff'], speed: 220, life: 0.5, size: 4});
    Play.sfx.play('hurt'); if (window.Juice) Juice.buzz('wrong');
    dirty = true;
    setTimeout(() => { if (S === s && c && s.woke === i && s.mode === 'play') c.die(); }, 650);
  }
  function hush() {   // 答对了：刺猬又睡着，这一格插上小花
    const s = S, i = s.woke;
    if (i < 0) return;
    s.woke = -1; s.caught.add(i); s.flags.delete(i);
    const [x, y] = center(i);
    fx.burst(x, y - 6, {n: 14, colors: PETALS, speed: 180, life: 0.6, size: 4, grav: 300});
    sh.toast('🦔 刺猬又睡着了，给它插上小花', 'good');
    dirty = true; paintHud();
  }

  // ------------------------------------------------------------ 题格：题卡盖在花田上，答对开出探照花
  async function askQ(i) {
    const s = S, q = s.qs.get(i);
    if (!q || q.st === 'open') return;
    s.asking = i; s.last = i; dirty = true; Play.sfx.play('pop');
    paintQ();
    if (!q.item) {
      try {
        q.item = await sh.gateAsk(`mines${s.lv.n || 0}-${i}`);
        if (q.item.say && typeof Speak !== 'undefined') Speak.play(q.item.say, 'en', 'word');
      } catch (e) { q.item = null; q.lost = true; }
      if (S !== s) return;
      if (q.lost) { sh.toast('题没拿到，这朵花苞直接开吧', 'warn'); return bloom(i, false); }
    }
    paintQ();
  }
  async function choose(o) {
    const s = S, i = s.asking, q = s.qs.get(i);
    if (!q || !q.item || q.busy || q.tried.has(o)) return;
    if (q.right >= 0) {   // 第一次错的时候已经知道对的是哪个了
      if (o === q.right) return bloom(i, false);
      q.tried.add(o); q.miss++; Play.sfx.play('wrong');
      if (q.tried.size >= 2) sh.toast('对的那个亮起来了，点它开花', 'warn'); else sh.toast('还不是它，再选一次', 'warn');
      return paintQ();
    }
    q.busy = true; paintQ();
    const a = await sh.gateAnswer(q.item, o);
    q.busy = false;
    if (S !== s) return;
    q.right = a.right;
    if (a.lost) return bloom(i, false);
    if (a.correct) return bloom(i, true);
    q.tried.add(o); q.miss++; s.qMiss++;
    Play.sfx.play('wrong'); fx.shake(3, 0.15);
    sh.toast('花苞晃了晃，不是它，再选一次', 'warn');
    paintQ();
  }
  function later() { S.asking = null; dirty = true; paintQ(); }
  function bloom(i, first) {   // 探照花：周围安全的翻开，刺猬插上金色小花
    const s = S, q = s.qs.get(i), [x, y] = center(i), cs = GEO ? GEO.cs : 40;
    q.st = 'open'; s.asking = null; paintQ();
    s.open.add(i); s.at.set(i, clock); s.popped.add(i);
    s.beams.push({i, t: 0});
    let found = 0, k = 0;
    for (const j of s.adj.get(i)) {
      if (s.mines.has(j)) { if (!s.caught.has(j) && !s.gold.has(j)) { s.gold.add(j); s.flags.delete(j); found++; } }
      else if (!s.open.has(j) && !s.qs.has(j)) { s.flags.delete(j); k += reveal(j, 1); }
    }
    s.got += first ? 3 : 1;
    Play.sfx.play(first ? 'right' : 'star'); Play.sfx.play('shield', 0.2);
    fx.ring(x, y, {color: '#ffd84a', width: 9, grow: 520, life: 0.6});
    fx.burst(x, y, {n: first ? 34 : 18, colors: ['#ffc21a', '#fff', '#ff9fc4'], speed: 360, shape: 'star', size: 7, life: 0.9});
    fx.text(x, y - cs * 0.7, first ? '+3 🍯' : '+1 🍯', {color: '#e08a00', size: Math.max(22, cs * 0.5)});
    if (window.Juice) Juice.buzz('right');
    sh.toast(first ? `🌻 答对了！探照花照亮了 ${k} 格${found ? `，找到 ${found} 只刺猬` : ''}` : `🌻 花开了${found ? `，找到 ${found} 只刺猬` : ''}`, 'good');
    dirty = true; paintHud();
    checkWin(2);
  }

  // ------------------------------------------------------------ 输入：点、右键、长按
  function toLogic(e) { const r = sh.canvas.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * stage.W, (e.clientY - r.top) / r.height * stage.H]; }
  function cellAtPx(px, py) {
    if (!GEO) return null;
    let best = null, bd = Infinity;
    for (const i of S.adj.keys()) { const [x, y] = cellCenter(i), d = (x - px) ** 2 + (y - py) ** 2; if (d < bd) { bd = d; best = i; } }
    return bd <= (GEO.cs * 0.6) ** 2 ? best : null;
  }
  let press = null;
  function pdown(e) {
    Play.sfx.unlock();
    if (!S || S.mode !== 'play' || Play.input.blocked) return;
    const [x, y] = toLogic(e), i = cellAtPx(x, y);
    if (i == null) return;
    e.preventDefault();
    if (e.button === 2) { tap(i, true); press = null; return; }
    press = {i, x, y, done: false, timer: setTimeout(() => {
      if (!press || press.i !== i) return;
      press.done = true;
      if (S.started && covered(i)) { plant(i); if (navigator.vibrate) try { navigator.vibrate(18); } catch (e2) { /* 不支持 */ } }
    }, LONG)};
    S.press = {i, t: 0};
  }
  function pmove(e) {
    if (!press) return;
    const [x, y] = toLogic(e);
    if (Math.hypot(x - press.x, y - press.y) > (GEO ? GEO.cs * 0.5 : 20)) { clearTimeout(press.timer); press = null; S.press = null; }
  }
  function pup() {
    if (!press) return;
    clearTimeout(press.timer);
    const p = press; press = null; S.press = null;
    if (!p.done) tap(p.i, false);
  }

  // ------------------------------------------------------------ 每帧
  function update(dt) {
    clock += dt; fx.update(dt);
    const s = S; if (!s) return;
    for (const [i, t] of s.at) if (!s.popped.has(i) && clock >= t) pop(i);
    while (s.rings.length && clock >= s.rings[0].t) { const r = s.rings.shift(); Play.sfx.play('ding', 0, Math.min(8, r.n + 1)); }
    if (s.shake && (s.shake.t -= dt) <= 0) s.shake = null;
    if (s.grow && (s.grow.t += dt) > 0.5) s.grow = null;
    if (s.hop && (s.hop.t += dt) > 0.3) s.hop = null;
    if (s.press) s.press.t += dt;
    if (s.woke >= 0) s.wakeT = (s.wakeT || 0) + dt;
    s.beams = s.beams.filter(b => (b.t += dt) < 1.6);
    if (s.bloomT >= 0) s.bloomT += dt;
    if (s.mode === 'clearing' && clock >= s.winAt) arrived();
    if (s.mode === 'play' && !Play.input.blocked && Play.input.take('mode')) setMode(!s.flagMode);
    if (s.mode === 'play' && c && c.elapsed() >= s.limit) { s.mode = 'done'; sh.toast('⏰ 时间到！', 'warn'); setTimeout(() => report('lose'), 900); }
    if (s.mode === 'play') { const p = pct(); const h = p >= 75 ? 3 : p >= 50 ? 2 : p >= 25 ? 1 : 0; if (h > s.half) { s.half = h; if (h === 2) sh.toast('🌼 一半啦！', 'good'); } }
    if (c && c.net && (sendT -= dt) <= 0 && (dirty || sendT < -3) && s.mode !== 'preview') { sendT = 0.5; dirty = false; c.net.send({t: 'mn', o: pct(), st: status()}); }
    if (hud && s.mode !== 'preview') hudTick();
  }
  const status = () => S.woke >= 0 ? 'rv' : S.asking != null ? 'q' : '';

  // ------------------------------------------------------------ 画面
  function geo() {   // 花田放中间；宽屏右边是大家的小窗；上面让给计数，下面让给按钮
    const W = stage.W, H = stage.H, wide = W >= 860, lv = S.lv, hex = lv.kind === 'hex';
    let top = wide ? 74 : 92, bot = wide ? 84 : 96;
    const side = wide && others().length ? 250 : 0;
    if (hud && hud.topEl.offsetParent) {   // 按上面的计数、下面的按钮实际占了多高让开（手机上字和按钮相对更大）
      const cr = sh.canvas.getBoundingClientRect(), k = H / (cr.height || H);
      top = Math.max(top * 0.6, (hud.topEl.getBoundingClientRect().bottom - cr.top) * k + 14);
      bot = Math.max(bot * 0.6, (cr.bottom - hud.botEl.getBoundingClientRect().top) * k + 14);
    }
    const aw = W - 36 - side, ah = H - top - bot;
    let cs = hex ? Math.min(aw / (lv.cols + 0.5), ah / ((lv.rows - 1) * 0.866 + 1.155)) : Math.min(aw / lv.cols, ah / lv.rows);
    cs = Math.min(cs, 72);
    const bw = hex ? cs * (lv.cols + 0.5) : cs * lv.cols, bh = hex ? cs * ((lv.rows - 1) * 0.866 + 1.155) : cs * lv.rows;
    const pad = Math.max(8, cs * 0.2);
    return {W, H, wide, cs, hex, bw, bh, pad, side, x0: (W - side - bw) / 2, y0: top + (ah - bh) / 2};
  }
  function cellCenter(i) {
    const g = GEO, cols = S.lv.cols, r = Math.floor(i / cols), q = i % cols;
    if (g.hex) return [g.x0 + (q + 0.5 + (r % 2 ? 0.5 : 0)) * g.cs, g.y0 + g.cs * 0.577 + r * g.cs * 0.866];
    return [g.x0 + (q + 0.5) * g.cs, g.y0 + (r + 0.5) * g.cs];
  }
  const center = i => GEO ? cellCenter(i) : [0, 0];

  function render() {
    const s = S, g = GEO = geo();
    stage.begin();
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    Play.scene.sky(ctx, g.W, g.H, s.theme);
    Play.scene.drawClouds(ctx, g.W, s.theme, clock * 3);
    Play.scene.hills(ctx, g.W, g.H, g.H + 20, s.theme, 0);
    board(g, s);
    if (g.side) minis(g);
    ctx.restore();
    fx.draw(ctx); fx.drawFlash(ctx, g.W, g.H);
  }
  function shapePath(x, y, w, cs, g, lift) {   // 一格的形状（方格圆角、六边形）
    if (g.hex) { hexPath(x, y - (lift || 0), cs * 0.56); return; }
    Play.roundRect(ctx, x - w / 2, y - w / 2 - (lift || 0), w, w, w * 0.22);
  }
  function hexPath(x, y, R) {
    ctx.beginPath();
    for (let k = 0; k < 6; k++) { const a = (60 * k - 30) * Math.PI / 180; ctx[k ? 'lineTo' : 'moveTo'](x + R * Math.cos(a), y + R * Math.sin(a)); }
    ctx.closePath();
  }
  function turf(x, y, cs, g, P, gold, lift, i) {   // 鼓起来的草皮：底下一道深色厚边，上面亮、下面暗，一条高光
    const w = cs - 6, d = Math.max(3, cs * 0.08);
    ctx.fillStyle = INK; shapePath(x, y + d, w, cs, g, 0); ctx.fill();
    const gr = ctx.createLinearGradient(0, y - w / 2, 0, y + w / 2);
    gr.addColorStop(0, gold ? '#ffe680' : P.g1); gr.addColorStop(1, gold ? '#f5b301' : P.g2);
    ctx.fillStyle = gr; ctx.strokeStyle = INK; ctx.lineWidth = Math.max(2, cs * 0.045);
    shapePath(x, y, w, cs, g, lift); ctx.fill(); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.35)';
    if (g.hex) { ctx.beginPath(); ctx.ellipse(x, y - cs * 0.24 - (lift || 0), cs * 0.26, cs * 0.07, 0, 0, 7); ctx.fill(); }
    else { Play.roundRect(ctx, x - w / 2 + 5, y - w / 2 + 4 - (lift || 0), w - 10, w * 0.17, w * 0.085); ctx.fill(); }
    if (!gold && i != null && (i * 7) % 3 === 0) {   // 几根草叶
      ctx.strokeStyle = P.blade; ctx.lineWidth = Math.max(1.6, cs * 0.035); ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.globalAlpha = 0.7;
      const bx = x + ((i * 13) % 5 - 2) * cs * 0.08, by = y + cs * 0.16 - (lift || 0);
      ctx.beginPath(); ctx.moveTo(bx - cs * 0.07, by + cs * 0.06); ctx.lineTo(bx, by - cs * 0.07); ctx.lineTo(bx + cs * 0.07, by + cs * 0.06); ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }
  function soil(x, y, cs, g, P, lit) {
    const w = cs - 6;
    ctx.fillStyle = lit ? '#fff6c4' : P.soil; ctx.strokeStyle = lit ? '#ffcf3a' : P.soil2; ctx.lineWidth = 2;
    shapePath(x, y, w, cs, g, 0); ctx.fill(); ctx.stroke();
  }
  function board(g, s) {
    const P = theme(), cs = g.cs, pad = g.pad;
    const minX = g.x0 - pad, minY = g.y0 - pad, BW = g.bw + pad * 2, BH = g.bh + pad * 2;
    ctx.fillStyle = INK; Play.roundRect(ctx, minX, minY + 7, BW, BH, 24); ctx.fill();
    ctx.fillStyle = P.frame; ctx.strokeStyle = INK; ctx.lineWidth = 4; Play.roundRect(ctx, minX, minY, BW, BH, 24); ctx.fill(); ctx.stroke();
    // 糖石
    for (const i of s.lv.holes || []) {
      const [x, y] = cellCenter(i), w = cs - 10;
      ctx.fillStyle = INK; shapePath(x, y + 4, w, cs, g); ctx.fill();
      ctx.fillStyle = '#bfeee0'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5; shapePath(x, y, w, cs, g); ctx.fill(); ctx.stroke();
      ctx.save(); shapePath(x, y, w, cs, g); ctx.clip(); ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = cs * 0.1;
      for (let k = -2; k <= 2; k++) { ctx.beginPath(); ctx.moveTo(x - cs + k * cs * 0.35, y + cs); ctx.lineTo(x + cs + k * cs * 0.35, y - cs); ctx.stroke(); }
      ctx.restore();
    }
    const lit = new Set(); for (const b of s.beams) { lit.add(b.i); for (const j of s.adj.get(b.i)) lit.add(j); }
    const wakeHog = [], flyers = [];
    for (const i of s.adj.keys()) {
      const [x0, y0] = cellCenter(i), sk = s.shake && s.shake.i === i ? Math.sin(clock * 60) * 4 : 0, x = x0 + sk;
      const t = s.at.get(i), q = s.qs.get(i);
      const hop = s.hop && s.adj.get(s.hop.i).includes(i) && covered(i) ? Math.sin(s.hop.t / 0.3 * Math.PI) * cs * 0.08 : 0;
      const pressed = s.press && s.press.i === i ? 2 : 0;
      if (s.open.has(i) && t != null && clock >= t) {
        soil(x, y0, cs, g, P, lit.has(i));
        const n = s.num.get(i);
        if (n && !(q && clock - t < 0.9)) {
          ctx.font = `900 ${Math.round(cs * 0.56)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.lineWidth = Math.max(3, cs * 0.08); ctx.strokeStyle = '#fff'; ctx.lineJoin = 'round'; ctx.strokeText(String(n), x, y0 + cs * 0.03);
          ctx.fillStyle = NUMC[n] || INK; ctx.fillText(String(n), x, y0 + cs * 0.03);
        }
        if (q) {   // 开过的题格：一朵向日葵（刚开时大，慢慢缩到角上）
          const k = Math.min(1, (clock - t) / 0.9), sc = cs / 48 * (1.25 - 0.8 * Play.ease.outCubic(k));
          sun(x + (n ? cs * 0.3 * k : 0), y0 - (n ? cs * 0.3 * k : 0), n ? sc : Math.max(sc, cs / 70));
        }
        const fly = clock - t;
        if (fly < POP) flyers.push(() => {   // 草皮蹦起来、转一下、变小、淡掉
          const k = fly / POP, rot = ((i * 37) % 2 ? 1 : -1) * (0.3 + (i % 5) * 0.06) * k;
          ctx.save(); ctx.globalAlpha = 1 - k; ctx.translate(x, y0 - cs * 0.5 * Math.sin(k * Math.PI * 0.8)); ctx.rotate(rot); ctx.scale(1 - k * 0.45, 1 - k * 0.45); ctx.translate(-x, -y0);
          turf(x, y0, cs, g, P, !!q, 0, i); ctx.restore();
        });
        continue;
      }
      if (s.caught.has(i) || s.gold.has(i) && s.bloomT >= 0) { soil(x, y0, cs, g, P); hog(x - cs * 0.06, y0 + cs * 0.08, cs / 64, 'sleep'); flowerFlag(x + cs * 0.2, y0 + cs * 0.02, cs, s.gold.has(i) ? '#ffd21f' : PETALS[i % 4], 0.8); continue; }
      if (s.woke === i) { soil(x, y0, cs, g, {soil: '#ffe0da', soil2: '#ff9a8a'}); wakeHog.push(i); continue; }
      if (q) {
        if (s.asking === i) { ctx.save(); ctx.globalAlpha = 0.6 + 0.3 * Math.sin(clock * 6); ctx.fillStyle = '#ffd84a'; ctx.beginPath(); ctx.arc(x, y0, cs * 0.85, 0, 7); ctx.fill(); ctx.restore(); }
        turf(x, y0, cs, g, P, true, hop + pressed, i);
        bud(x, y0 - hop - pressed + cs * 0.04, cs / 52 * (1 + 0.04 * Math.sin(clock * 3 + i)));
        badge(x + cs * 0.3, y0 - cs * 0.3 - hop, cs, '?');
        continue;
      }
      turf(x, y0, cs, g, P, false, hop + pressed, i);
      const f = flowersOn(i);
      if (f) {
        const gk = s.grow && s.grow.i === i ? Play.ease.outBack(Math.min(1, s.grow.t / 0.35)) : 1;
        flowerFlag(x, y0 + cs * 0.12 - hop, cs, s.gold.has(i) ? '#ffd21f' : PETALS[i % 4], gk);
        if (f > 1) flowerFlag(x + cs * 0.22, y0 + cs * 0.18 - hop, cs, '#ff9e5e', gk * 0.75);
      }
      if (!s.started && i === s.lv.start && s.mode !== 'preview') {   // 第一下：小蜜蜂标着
        ctx.save(); ctx.globalAlpha = 0.45 + 0.35 * Math.sin(clock * 5); ctx.strokeStyle = '#fff'; ctx.lineWidth = 4; ctx.setLineDash([8, 6]);
        shapePath(x, y0, cs + 2, cs, g, 0); ctx.stroke(); ctx.restore();
      }
    }
    // 光：探照花
    for (const b of s.beams) {
      const [x, y] = cellCenter(b.i), a = Math.min(1, (1.6 - b.t) / 0.6), R = cs * (1.2 + Math.min(1, b.t * 3) * 0.9);
      ctx.save(); ctx.globalAlpha = a * 0.55;
      const gr = ctx.createRadialGradient(x, y, 0, x, y, R);
      gr.addColorStop(0, 'rgba(255,246,176,.95)'); gr.addColorStop(0.55, 'rgba(255,216,74,.45)'); gr.addColorStop(1, 'rgba(255,216,74,0)');
      ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, R, 0, 7); ctx.fill();
      ctx.restore();
    }
    for (const f of flyers) f();   // 蹦起来的草皮画在最上面
    // 地道：两头的洞和虚线
    for (const [a, b] of s.lv.tunnels || []) {
      const [ax, ay] = cellCenter(a), [bx, by] = cellCenter(b);
      ctx.save(); ctx.strokeStyle = '#ffd84a'; ctx.lineWidth = 3.5; ctx.setLineDash([5, 8]); ctx.lineDashOffset = -clock * 18; ctx.globalAlpha = 0.85;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.quadraticCurveTo((ax + bx) / 2 - (by - ay) * 0.3, (ay + by) / 2 + (bx - ax) * 0.3, bx, by); ctx.stroke(); ctx.restore();
      for (const [x, y] of [[ax, ay], [bx, by]]) {
        ctx.fillStyle = INK; ctx.beginPath(); ctx.ellipse(x - cs * 0.3, y + cs * 0.3, cs * 0.14, cs * 0.08, 0, 0, 7); ctx.fill();
        ctx.fillStyle = '#ff7a2a'; ctx.beginPath(); ctx.ellipse(x - cs * 0.3, y + cs * 0.3, cs * 0.09, cs * 0.045, 0, 0, 7); ctx.fill();
      }
    }
    // 吵醒的刺猬（盖在格子上面，竖着刺、冒 !）
    for (const i of wakeHog) {
      const [x, y] = cellCenter(i), k = Math.min(1, (s.wakeT || 0) / 0.25);
      ctx.save(); ctx.globalAlpha = 0.5; const gr = ctx.createRadialGradient(x, y, 0, x, y, cs * 1.3); gr.addColorStop(0, 'rgba(255,140,120,.8)'); gr.addColorStop(1, 'rgba(255,106,90,0)'); ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, cs * 1.3, 0, 7); ctx.fill(); ctx.restore();
      hog(x - cs * 0.04, y + cs * 0.06 - (1 - k) * cs * 0.3, cs / 52 * (0.6 + 0.4 * Play.ease.outBack(k)), 'wake');
    }
    // 过关：刺猬一只只醒来挥手，身边开花
    if (s.bloomT >= 0) {
      const hogs = [...s.mines.keys()].sort((a, b) => dist(a) - dist(b));
      const nOn = Math.floor(s.bloomT / 1.1 * hogs.length);
      hogs.forEach((i, k) => {
        if (k > nOn) return;
        const [x, y] = cellCenter(i), pop = k === nOn ? 1.25 : 1;
        soil(x, y, cs, g, P, true);
        flowerFlag(x + cs * 0.22, y + cs * 0.02, cs, PETALS[i % 4], 0.9 * pop);
        hog(x - cs * 0.1, y + cs * 0.1, cs / 70 * pop, 'cheer');
      });
    }
    // 小伙伴：跟着最后点的地方
    {
      const [bx, by] = cellCenter(s.last ?? s.lv.start);
      drawBee(ctx, bx + cs * 0.42, by - cs * 0.5 + Math.sin(clock * 4) * 2, Math.max(0.28, cs / 150), {pose: s.mode === 'win' || s.mode === 'clearing' ? 'cheer' : 'fly', col: myColor(), rot: 0.14});
    }
  }
  function dist(i) { const [a, b] = cellCenter(i), [x, y] = cellCenter(S.last ?? S.lv.start); return Math.hypot(a - x, b - y); }
  function badge(x, y, cs, t) {
    const r = Math.max(7, cs * 0.15);
    ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); ctx.stroke();
    ctx.fillStyle = INK; ctx.font = `900 ${Math.round(r * 1.4)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(t, x, y + 1);
  }
  function flower(x, y, k, col) {
    ctx.save(); ctx.translate(x, y); ctx.scale(k, k); ctx.lineWidth = 1.6; ctx.strokeStyle = INK;
    for (let p = 0; p < 5; p++) { const a = p * 1.2566; ctx.fillStyle = col; ctx.beginPath(); ctx.arc(Math.cos(a) * 5.5, Math.sin(a) * 5.5, 4.6, 0, 7); ctx.fill(); ctx.stroke(); }
    ctx.fillStyle = '#ffc21a'; ctx.beginPath(); ctx.arc(0, 0, 3.6, 0, 7); ctx.fill(); ctx.stroke();
    ctx.restore();
  }
  function flowerFlag(x, y, cs, col, k) {   // 插在草皮上的小花：茎、叶、花
    const s = cs / 62 * (k == null ? 1 : k);
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.lineCap = 'round';
    ctx.strokeStyle = '#3f9a3a'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(0, 18); ctx.quadraticCurveTo(-2, 6, 0, -6); ctx.stroke();
    ctx.fillStyle = '#6cc24a'; ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(0, 8); ctx.quadraticCurveTo(9, 0, 14, 6); ctx.quadraticCurveTo(8, 13, 0, 8); ctx.fill(); ctx.stroke();
    ctx.restore();
    flower(x, y - 10 * s, 2.1 * s, col);
  }
  function bud(x, y, s) {
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.strokeStyle = '#3f9a3a'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(0, 18); ctx.lineTo(0, 4); ctx.stroke();
    ctx.fillStyle = '#6cc24a'; ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(0, 6); ctx.quadraticCurveTo(-12, 0, -10, -8); ctx.quadraticCurveTo(-2, -8, 0, 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#ff8fb8'; ctx.lineWidth = 2.6; ctx.beginPath(); ctx.moveTo(0, -20); ctx.bezierCurveTo(10, -12, 11, -2, 0, 6); ctx.bezierCurveTo(-11, -2, -10, -12, 0, -20); ctx.fill(); ctx.stroke();
    ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(0, -20); ctx.bezierCurveTo(4, -10, 4, 0, 0, 6); ctx.stroke();
    ctx.restore();
  }
  function sun(x, y, s) {   // 向日葵（探照花）
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.strokeStyle = INK; ctx.lineWidth = 2.4;
    for (let k = 0; k < 12; k++) { ctx.save(); ctx.rotate(k * Math.PI / 6); ctx.fillStyle = '#ffd21f'; ctx.beginPath(); ctx.ellipse(0, -19, 7, 13, 0, 0, 7); ctx.fill(); ctx.stroke(); ctx.restore(); }
    ctx.fillStyle = '#9a5a2a'; ctx.lineWidth = 2.6; ctx.beginPath(); ctx.arc(0, 0, 13, 0, 7); ctx.fill(); ctx.stroke();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(-5, 3); ctx.quadraticCurveTo(0, 8, 5, 3); ctx.stroke();
    ctx.restore();
  }
  function hog(x, y, s, mood) {   // 小刺猬：睡着（眯眼、z）、吵醒（刺竖起来、圆眼、!）、过关挥手
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.strokeStyle = INK;
    ctx.fillStyle = 'rgba(58,48,38,.18)'; ctx.beginPath(); ctx.ellipse(0, 17, 26, 5, 0, 0, 7); ctx.fill();
    const n = 13, puff = mood === 'wake' ? 1 + 0.08 * Math.sin(clock * 30) : 1;
    ctx.beginPath(); ctx.moveTo(-22, 8);
    for (let i = 0; i <= n; i++) { const a = Math.PI * (1.02 + i / n * 0.98), r = i % 2 ? (mood === 'wake' ? 33 : 28) * puff : 19; ctx.lineTo(Math.cos(a) * r * 1.1, Math.sin(a) * r + 4); }
    ctx.lineTo(22, 8); ctx.closePath(); ctx.fillStyle = '#9a6447'; ctx.lineWidth = 2.6; ctx.fill(); ctx.stroke();
    ctx.strokeStyle = '#c48a63'; ctx.lineWidth = 2.4; ctx.beginPath(); ctx.moveTo(-15, 0); ctx.lineTo(-11, -8); ctx.moveTo(-6, -4); ctx.lineTo(-3, -13); ctx.moveTo(4, -5); ctx.lineTo(5, -13); ctx.stroke();
    ctx.strokeStyle = INK; ctx.fillStyle = '#ffd9ad'; ctx.lineWidth = 2.6;
    ctx.beginPath(); ctx.moveTo(-22, 8); ctx.quadraticCurveTo(-24, 18, -12, 18); ctx.lineTo(14, 18); ctx.quadraticCurveTo(30, 18, 30, 6); ctx.quadraticCurveTo(28, -6, 14, -6); ctx.quadraticCurveTo(6, -4, 4, 4); ctx.quadraticCurveTo(0, 10, -22, 8); ctx.fill(); ctx.stroke();
    ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(29, 4, 3.4, 0, 7); ctx.fill();
    ctx.lineWidth = 2.4;
    if (mood === 'wake') { ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(13, 0, 5, 0, 7); ctx.fill(); ctx.lineWidth = 2; ctx.stroke(); ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(14, 0, 2.4, 0, 7); ctx.fill(); }
    else { ctx.beginPath(); ctx.moveTo(9, mood === 'cheer' ? 2 : 1); ctx.quadraticCurveTo(13, mood === 'cheer' ? -3 : 5, 17, mood === 'cheer' ? 2 : 1); ctx.stroke(); }
    ctx.fillStyle = 'rgba(255,138,138,.75)'; ctx.beginPath(); ctx.ellipse(18, 10, 4, 2.4, 0, 0, 7); ctx.fill();
    if (mood === 'cheer') {
      ctx.fillStyle = '#c4413a'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(14, 11); ctx.quadraticCurveTo(19, 17, 24, 11); ctx.closePath(); ctx.fill(); ctx.stroke();
      const wv = Math.sin(clock * 10) * 4;
      ctx.lineWidth = 3.4; ctx.beginPath(); ctx.moveTo(-4, 14); ctx.quadraticCurveTo(-10, 0, 0 + wv, -8 - 14); ctx.stroke();
      ctx.fillStyle = '#ffd9ad'; ctx.lineWidth = 2.2; ctx.beginPath(); ctx.arc(wv, -23, 4, 0, 7); ctx.fill(); ctx.stroke();
    }
    if (mood === 'sleep') { ctx.fillStyle = INK; ctx.globalAlpha = 0.7; ctx.font = `700 13px ${FONT}`; ctx.textAlign = 'left'; ctx.fillText('z', 24, -14 - Math.sin(clock * 2) * 2); ctx.globalAlpha = 0.5; ctx.font = `700 10px ${FONT}`; ctx.fillText('z', 32, -24 - Math.sin(clock * 2 + 1) * 2); ctx.globalAlpha = 1; }
    if (mood === 'wake') {
      ctx.fillStyle = '#fff'; ctx.lineWidth = 2.6; ctx.beginPath(); ctx.arc(-6, -42, 12, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#e2463e'; ctx.font = `900 18px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('!', -6, -41);
    }
    ctx.restore();
  }
  // 宽屏右边：大家的小窗——只画翻了百分之几、在干什么，不画格子（抄不了）
  function minis(g) {
    const ps = others();
    const x = g.W - g.side + 6, w = g.side - 26;
    ps.forEach((p, k) => {
      const m = mates[p.id] || {o: 0, st: ''}, y = 86 + k * 168, h = 150;
      ctx.fillStyle = INK; Play.roundRect(ctx, x, y + 6, w, h, 18); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 3; Play.roundRect(ctx, x, y, w, h, 18); ctx.fill(); ctx.stroke();
      drawBee(ctx, x + 42, y + 82, 0.55, {pose: m.done ? 'cheer' : 'fly', col: p.role});
      const cx = x + w - 56, cy = y + 76, r = 36, col = (BODY[p.role] || BODY.sun)[1], o = m.done ? 100 : m.o || 0;
      ctx.lineCap = 'round'; ctx.lineWidth = 10; ctx.strokeStyle = '#efe6d2'; ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.stroke();
      ctx.strokeStyle = col; ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + o / 100 * Math.PI * 2); ctx.stroke();
      ctx.fillStyle = INK; ctx.font = `900 20px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(`${o}%`, cx, cy + 1);
      ctx.textAlign = 'left'; ctx.font = `800 16px ${FONT}`; ctx.fillText(p.name, x + 14, y + 22);
      ctx.textAlign = 'center'; ctx.font = `700 13px ${FONT}`; ctx.fillStyle = '#6b6152';
      ctx.fillText(m.done ? `🏁 翻完了 · ${m.done.secs}s` : m.st === 'q' ? '在答题格 🌷' : m.st === 'rv' ? '刺猬醒了，在答题' : '翻开中', x + w / 2, y + h - 16);
    });
  }
  /* 小蜜蜂（和一笔连花路同一个画法） */
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
  window.MinesArt = {state: () => S, load: lv => { const m = S.mode; S = fresh(lv, S.avatar); S.mode = m === 'preview' ? 'preview' : 'play'; if (hud) paintHud(); }, px: i => {   // 截图、调试用：换一关、格子 i 在页面上的位置
    const r = sh.canvas.getBoundingClientRect(), [x, y] = center(i);
    return [r.left + x / stage.W * r.width, r.top + y / stage.H * r.height];
  }};

  // ------------------------------------------------------------ 上面的计数、题卡，下面的按钮
  function buildHud() {
    if (!hudEl) { hudEl = document.createElement('div'); sh.canvas.parentNode.insertBefore(hudEl, sh.hud.nextSibling); }
    hudEl.className = 'mn-layer' + (c && c.net ? ' mp' : '');
    hudEl.innerHTML = `<div class="mn-top"><span class="mn-chip mn-time"></span><span class="mn-chip mn-hogs"></span><span class="mn-chip mn-pct"></span><span class="mn-chip mn-honey"></span></div>
      <div class="mn-q" hidden><div class="mn-topic"></div><div class="mn-text"><span></span><button type="button" class="mn-say" aria-label="再听一遍">🔊</button></div>
        <div class="mn-opts"></div><div class="mn-foot"></div><button type="button" class="mn-later">先翻别的</button></div>
      <div class="mn-bot"><div class="mn-modes"><button type="button" class="mn-mode" data-m="dig">⛏ 翻开</button><button type="button" class="mn-mode" data-m="flag">🌼 插花</button></div><span class="mn-tip"></span></div>`;
    const $ = q => hudEl.querySelector(q);
    hud = {time: $('.mn-time'), hogs: $('.mn-hogs'), pct: $('.mn-pct'), honey: $('.mn-honey'), q: $('.mn-q'), topic: $('.mn-topic'), text: $('.mn-text span'),
           say: $('.mn-say'), opts: $('.mn-opts'), foot: $('.mn-foot'), later: $('.mn-later'), tip: $('.mn-tip'), topEl: $('.mn-top'), botEl: $('.mn-bot'), modes: hudEl.querySelectorAll('.mn-mode'), last: ''};
    hud.say.onclick = e => { e.stopPropagation(); const q = S.qs.get(S.asking); if (q && q.item && q.item.say && typeof Speak !== 'undefined') Speak.play(q.item.say, 'en', 'word'); };
    hud.later.onclick = e => { e.stopPropagation(); later(); };
    hud.modes.forEach(b => { b.onclick = e => { e.stopPropagation(); setMode(b.dataset.m === 'flag'); }; });
    hud.tip.textContent = touchy ? '长按格子也能插花 · 点数字一圈都翻' : '左键翻开 · 右键插花 · 点数字一圈都翻 · F 切换';
    paintHud();
  }
  function setMode(flag) {
    if (!S || S.mode === 'preview') return;
    S.flagMode = flag; Play.sfx.play('tick');
    paintHud();
  }
  function paintHud() {
    if (!hud || !S) return;
    hud.modes.forEach(b => b.classList.toggle('on', (b.dataset.m === 'flag') === S.flagMode));
    hud.hogs.textContent = `🦔 还睡着 ${Math.max(0, leftHogs())}`;
    hud.honey.textContent = `🍯 ${S.got}`;
    hud.last = '';
  }
  function paintQ() {
    if (!hud) return;
    const s = S, q = s.asking != null ? s.qs.get(s.asking) : null;
    hud.q.hidden = !q;
    hudEl.classList.toggle('asking', !!q);
    if (!q) return;
    const it = q.item;
    hud.topic.textContent = `${(it && it.topic) || '题格'} · 答对开出探照花`;
    hud.text.textContent = it ? it.q : '题来了……';
    hud.q.classList.toggle('long', !!it && it.q.length > 16);
    hud.say.hidden = !(it && it.say);
    const show = q.right >= 0 && q.tried.size >= 2;
    hud.opts.innerHTML = it ? (it.options || []).map((o, k) => `<button type="button" class="mn-opt${q.tried.has(k) ? ' no' : ''}${show && k === q.right ? ' yes' : ''}" data-k="${k}"${q.tried.has(k) || q.busy ? ' disabled' : ''}>${esc(String(o).replace(/^[A-H]\.\s*/, ''))}</button>`).join('') : '';
    hud.opts.querySelectorAll('.mn-opt').forEach(b => { b.onclick = e => { e.stopPropagation(); choose(+b.dataset.k); }; });
    hud.foot.textContent = show ? '亮着的就是答案，点它照样开花' : q.tried.size ? '不是它，再选一次' : '想好了再点，花田不跑';
  }
  function hudTick() {
    const s = S, t = Math.floor(c ? c.elapsed() : 0), k = `${t}|${s.open.size}|${s.got}`;
    if (hud.last === k) return;
    hud.last = k;
    hud.time.textContent = `⏱ ${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
    hud.pct.textContent = `🌼 翻开 ${pct()}%`;
    hud.honey.textContent = `🍯 ${s.got}`;
    hud.hogs.textContent = `🦔 还睡着 ${Math.max(0, leftHogs())}`;
  }

  // ------------------------------------------------------------ 翻完了：几个人一起比谁先
  const others = () => c && c.peers ? c.peers : [];
  const seconds = () => c ? c.elapsed() : 0;
  function arrived() {
    const s = S;
    if (s.mode !== 'clearing') return;
    s.mode = 'win'; s.asking = null; paintQ();
    Play.sfx.play('chord', 0, 3);
    const ok = s.qMiss === 0 ? 1 : 0;
    const me = {secs: +seconds().toFixed(1), honey: s.got, ok};
    if (!others().length) return report('win');
    mates[c.me.id] = Object.assign(mates[c.me.id] || {}, {done: me, o: 100});
    c.net.send({t: 'mn', o: 100, done: me});
    const k = place(c.me.id);
    sh.toast(k === 1 ? '🥇 第一个翻完！看看大家翻到哪了' : `第 ${k} 个翻完！给还在翻的人加油吧`, 'good');
    allIn();
  }
  function place(id) { const d = mates[id] && mates[id].done; return d ? 1 + Object.values(mates).filter(m => m.done && m.done.secs < d.secs).length : 0; }
  function onMate(m) {
    const p = others().find(x => x.id === m.from); if (!p) return;
    const was = mates[m.from] && mates[m.from].done;
    mates[m.from] = Object.assign(mates[m.from] || {}, {name: p.name, role: p.role, o: Math.max(0, Math.min(100, +m.o || 0)), st: m.st || ''}, m.done ? {done: m.done} : {});
    if (m.done && !was) { sh.toast(`${place(m.from) === 1 ? '🥇 ' : ''}${p.name} 翻完啦！`, 'good'); allIn(); }
    else if (m.st === 'q' && (!mates[m.from].seenQ || clock - mates[m.from].seenQ > 20)) mates[m.from].seenQ = clock;
  }
  function allIn() {
    if (!c.host || S.mode !== 'win') return;
    const ids = [c.me.id, ...others().map(p => p.id)];
    if (!ids.every(id => mates[id] && mates[id].done)) return;
    const d = ids.map(id => mates[id].done);
    report('win', Math.max(...d.map(x => x.secs)), d.reduce((a, x) => a + x.ok, 0), d.reduce((a, x) => a + (x.honey || 0), 0));
  }
  function ranking() {
    const rows = Object.entries(mates).filter(([, m]) => m.done).sort((a, b) => a[1].done.secs - b[1].done.secs);
    return rows.map(([id, m], i) => `${['🥇', '🥈', '🥉', '4️⃣'][i]} ${+id === c.me.id ? '我' : esc(m.name)} ${m.done.secs}s`).join(' · ');
  }
  function report(result, teamSecs, teamOk, teamHoney) {
    const s = S;
    if (s.sent) return;
    s.sent = true;
    if (teamSecs != null) return c.finish(result, {seconds: teamSecs, gates: teamOk, score: teamHoney}, null);
    const secs = +seconds().toFixed(1), best = s.lv.best, ok = s.qMiss === 0;
    const title = result === 'win'
      ? `🌼 花田翻完啦！<small class="mn-sub">⏱ ${secs} 秒${best != null ? (secs < best ? ` · 新纪录，快了 ${(best - secs).toFixed(1)} 秒` : ` · 纪录 ${best} 秒`) : ''} · 🍯 ${s.got}${s.qs.size ? (ok ? ' · 题格都一次答对' : ' · 题格有答错的，下次一次答对') : ''}${s.wakes ? '' : ' · 一只刺猬都没吵醒'}</small>`
      : `⏰ 时间到，翻开了 ${pct()}%<small class="mn-sub">下次接着翻</small>`;
    c.finish(result, {seconds: secs, gates: ok ? 1 : 0, score: s.got, far: openedSafe() / Math.max(1, s.safe.length)}, title);
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = Play.stage(sh.canvas, {height: 760, minW: 360, maxW: 2000});
    ctx = stage.ctx;
    S = fresh(null, sh.data.avatar);
    reveal(S.lv.start); S.started = true; for (const i of S.open) { S.at.set(i, -9); S.popped.add(i); }
    const cv = sh.canvas;
    cv.style.touchAction = 'none';
    cv.addEventListener('pointerdown', pdown);
    cv.addEventListener('pointermove', pmove);
    cv.addEventListener('pointerup', pup); cv.addEventListener('pointercancel', () => { if (press) clearTimeout(press.timer); press = null; if (S) S.press = null; });
    cv.addEventListener('contextmenu', e => e.preventDefault());
    Play.loop(update, render).start();
  }
  function ready() {
    S = fresh(S && S.mode !== 'preview' ? S.base : null, sh.data.avatar);
    fx.parts = []; fx.texts = []; hud = null; mates = {};
    if (hudEl) hudEl.innerHTML = '';
  }
  function begin(cx) {
    c = cx; mates = {}; sendT = 0; dirty = true;
    if (cx.net) { cx.net.off('mn'); cx.net.on('mn', onMate); }
    S = fresh(cx.level && cx.level.mines ? cx.level : null, (cx.match && cx.match.avatar) || sh.data.avatar, cx.me && cx.me.role);
    S.limit = Math.max(30, Math.min(600, (cx.match && !cx.match.unlimited && cx.match.seconds_left) || 600));
    S.mode = 'play';
    Play.input.clear();
    buildHud();
    sh.toast(others().length ? '每人一块花田，一样大、一样难，看谁先翻完！' : '先点小蜜蜂标着的那一格，一下翻开一大片！', 'good');
  }

  const rules = [
    '先点<b>小蜜蜂标着的那一格</b>：一定安全，一下翻开一大片',
    '翻开的<b>数字</b>是它周围睡着几只小刺猬；推出刺猬在哪，就在那格<b>插一朵小花</b>（电脑右键、手机长按，或者切到「插花」）',
    '点一个数字：它周围插的花和数字一样多，剩下的格子<b>一起翻开</b>',
    '<b>金色花苞</b>（题格）下面一定安全：点它答一道题，答对开出<b>探照花</b>，照亮周围、刺猬自动插上花；答错再选，连错两次亮出答案照样开',
    '翻到刺猬会把它<b>吵醒</b>：答对 1 道题它又睡着，这一格插上小花，接着玩',
    '安全的格子（包括题格）<b>全翻开</b>就过关。糖石不是格子；大刺猬算 2 只（插 2 朵花）；地道两头也算挨着',
    '几个人一起：<b>每人一块花田</b>（同一关翻一翻，一样难），<b>谁先翻完谁第一</b>，大家都翻完才过关',
  ];

  PlayShell.init({
    quiz: 'revive', tempo: 100, rules, moreHint: '一起比谁先翻完',
    keys: '电脑：左键翻开，右键插花，点数字一圈都翻；F 切换翻开 / 插花；P 暂停',
    input: {mode: ['f', 'F']},
    revive: {verb: '哄刺猬接着睡', done: '🦔 刺猬又睡着了！'},
    setup: boot, ready, begin,
    end() { if (S) S.mode = S.mode === 'win' ? 'win' : 'done'; if (press) clearTimeout(press.timer); press = null; if (hudEl) hudEl.innerHTML = ''; hud = null; },
    finale() { if (S) { S.bloomT = 0; S.mode = 'win'; } },   // 过关仪式：刺猬一只只醒来挥手，身边开花
    verdict(res, result) {
      if (result !== 'win' || !others().length) return {};
      const k = place(c.me.id);
      return {title: `${k === 1 ? '🥇 我第一个翻完！' : `🎉 大家都翻完了，我第 ${k} 个`}<small class="mn-sub">${ranking()}</small>`};
    },
    onRevive() { if (S) hush(); },
    pause(on) { if (on) { Play.input.clear(); if (press) clearTimeout(press.timer); press = null; } },
  });
})();

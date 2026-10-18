/* 贪吃蛇大作战（合作长大，两个人各用一台设备）。关卡在 app/arena/games/levels/snake/，格式见 app/arena/games/snake.py。
   - 自己的蛇自己的设备算（转弯、冲刺、吃豆子、撞墙 / 撞电脑蛇），每秒发 15 次蛇头位置和长度，对方按蛇头轨迹画出身体；
   - 豆子和电脑蛇由开房间的设备算：豆子被吃了补新的（'food'），电脑蛇每秒发 10 次（'ai'）；
   - 两条蛇加起来够长，开房间的设备报过关；时间到了还不够长就差一点（外壳 shell.js 结算）。
   - 倒下答题复活后长度不变（像贪吃蛇大作战的「复活」），所以倒下不掉豆子；代价是答题这段时间少一条蛇在长。
   - 刷新 / 换设备：存档里有自己的蛇（位置、方向、长度）和豆子、电脑蛇，接着玩。 */
(function () {
  const SEG = 7, SPEED = 135, BOOST = 225, TURN = 3.4, START_LEN = 10, MIN_LEN = 6, HEAD_R = 10;
  const ROLE_COLOR = {green: ['#3fcf6e', '#1f9e4c'], pink: ['#ff7fb8', '#e04b8f']};
  const AI_COLORS = [['#9b7bff', '#6a4fd8'], ['#ffb347', '#e0832a'], ['#4fc3ff', '#2a8fd0'], ['#ff6b6b', '#d04545'], ['#c0c0c0', '#8a8a8a'], ['#ffd23f', '#d9a800']];
  const FOOD_COLORS = ['#ff6b6b', '#ffd23f', '#7bdff2', '#b8f2a6', '#c3a6ff', '#ff9ff3'];

  // 每个世界一个场景：场地外、场地、格子线、闪闪的小点（星星、雪花、气泡、火星）
  const SCENES = {
    day: {out: '#8fd17a', field: '#b8eb9f', grid: 'rgba(0,80,0,.07)'},
    night: {out: '#1d1f4a', field: '#2a2f6e', grid: 'rgba(255,255,255,.06)', sparkle: '#fff'},
    desert: {out: '#d9a35b', field: '#f3d39a', grid: 'rgba(120,70,0,.08)'},
    snow: {out: '#a9c8e6', field: '#eef6ff', grid: 'rgba(40,90,150,.08)', sparkle: '#9fc4ea'},
    sea: {out: '#145a7a', field: '#1f7fa6', grid: 'rgba(255,255,255,.07)', sparkle: '#bff0ff'},
    volcano: {out: '#3a1d1d', field: '#5b2b25', grid: 'rgba(255,160,90,.08)', sparkle: '#ffb36b'},
  };
  let sh, mp, loop, fx, lv = null, t = 0, ended = true, clockT = 0, snapT = 0;
  let me = null, peer = null, foods = new Map(), ais = [], foodN = 0, rng = Math.random, pointer = null, cam = {x: 0, y: 0};

  // ---------------------------------------------------------------- 蛇（自己的、队友的、电脑的都用这个）
  function snake(x, y, a, len, colors) { return {x, y, a, len, path: [{x, y}], colors, alive: true, boost: false}; }
  function grow(s, x, y) {   // 蛇头走到新位置：轨迹加点，按长度截掉尾巴
    const last = s.path[0];
    if (!last || Math.hypot(x - last.x, y - last.y) >= 3) s.path.unshift({x, y});
    s.x = x; s.y = y;
    let need = s.len * SEG, acc = 0;
    for (let i = 1; i < s.path.length; i++) {
      acc += Math.hypot(s.path[i].x - s.path[i - 1].x, s.path[i].y - s.path[i - 1].y);
      if (acc > need) { s.path.length = i + 1; break; }
    }
  }
  function body(s) {   // 每 SEG 取一个点：身体的一节一节
    const out = []; let acc = 0, next = 0;
    for (let i = 1; i < s.path.length && out.length < s.len; i++) {
      const a = s.path[i - 1], b = s.path[i], d = Math.hypot(b.x - a.x, b.y - a.y);
      while (acc + d >= next && out.length < s.len) { const k = (next - acc) / (d || 1); out.push({x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k}); next += SEG; }
      acc += d;
    }
    return out;
  }
  const radius = s => Math.min(16, 8 + s.len / 18);
  function hitsBody(hx, hy, s, skip) {   // 蛇头碰到 s 的身体（skip：自己的前几节不算）
    if (!s || !s.alive) return false;
    const r = radius(s), segs = body(s);
    for (let i = skip || 0; i < segs.length; i += 1) if (Math.hypot(segs[i].x - hx, segs[i].y - hy) < r + HEAD_R - 4) return true;
    return false;
  }

  // ---------------------------------------------------------------- 我的蛇
  function step(dt) {
    t += dt; fx.update(dt);
    if (mp.host) hostStep(dt);
    else for (const a of ais) { const p = a.track && a.track.get(['x', 'y']); if (p && a.alive) grow(a, p.x, p.y); }
    if (peer && peer.track) { const p = peer.track.get(['x', 'y']); if (p && peer.alive) grow(peer, p.x, p.y); }
    if (!me || ended) return;
    if (me.inv > 0) me.inv -= dt;
    if (me.alive) control(dt);
    clockT -= dt;
    if (clockT <= 0) {
      clockT = 0.25;
      const left = Math.max(0, lv.time - mp.elapsed());
      sh.hudClock(`⏱ ${Math.ceil(left)} · 🐍 ${teamLen()}/${lv.target}`);
    }
  }
  const teamLen = () => (me && me.alive ? me.len : 0) + (peer && peer.alive ? peer.len : 0);
  function control(dt) {
    const I = Play.input;
    let turn = (I.keys.right ? 1 : 0) - (I.keys.left ? 1 : 0);
    if (pointer) {   // 手指 / 鼠标按住：往那个方向游
      const want = Math.atan2(pointer.y - me.y, pointer.x - me.x);
      let d = want - me.a; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI;
      turn = Math.abs(d) < 0.05 ? 0 : Math.sign(d) * Math.min(1, Math.abs(d) * 3);
    }
    me.a += turn * TURN * dt;
    me.boost = !!I.keys.boost && me.len > MIN_LEN;
    if (me.boost) { me.shrink = (me.shrink || 0) + dt * 3; if (me.shrink >= 1) { me.shrink -= 1; me.len--; } }
    const sp = me.boost ? BOOST : SPEED;
    const nx = me.x + Math.cos(me.a) * sp * dt, ny = me.y + Math.sin(me.a) * sp * dt;
    grow(me, nx, ny);
    // 撞墙、撞电脑蛇
    const [W, H] = lv.size;
    if (nx < HEAD_R || ny < HEAD_R || nx > W - HEAD_R || ny > H - HEAD_R) return die('撞到墙了！');
    if (!(me.inv > 0)) for (const a of ais) if (a.alive && hitsBody(nx, ny, a, 0)) return die('撞到电脑蛇了！');   // 刚复活的 2 秒不会撞
    // 吃豆子
    const eaten = [];
    for (const [id, f] of foods) if (Math.hypot(f.x - nx, f.y - ny) < HEAD_R + 6 + f.v) { eaten.push(id); me.len += f.v; }
    if (eaten.length) {
      eaten.forEach(id => foods.delete(id)); Play.sfx.play('coin');
      if (mp.host) hostEat(eaten); else mp.net.send({t: 'eat', ids: eaten});
    }
  }
  function die(why) {
    me.alive = false; me.boost = false;
    fx.burst(me.x, me.y, {n: 24, colors: me.colors, speed: 260, size: 6, grav: 0});
    fx.shake(5, 0.25); Play.sfx.play('hurt');
    me.path = [];   // 长度留着，复活时还是这么长
    mp.toast(`${why}答对题就带着 ${me.len} 节复活`, 'warn');
    mp.die();
  }
  function coil(s, x, y, a) {   // 把蛇盘在 (x, y) 附近：头朝 a，身子绕成圈（多长都放得下，不会伸出墙外）
    const total = s.len * SEG + 20, r = Math.min(70, 26 + s.len * 0.6), turn = 1 / r;
    let px = x, py = y, pa = a + Math.PI;
    const pts = [{x: px, y: py}];
    for (let d = 0; d < total; d += 4) { pa += turn * 4; px += Math.cos(pa) * 4; py += Math.sin(pa) * 4; pts.push({x: px, y: py}); }
    s.x = x; s.y = y; s.a = a; s.path = pts;
  }
  function safeSpot() {   // 离电脑蛇远一点的地方
    const [W, H] = lv.size;
    let best = null;
    for (let k = 0; k < 12; k++) {
      const x = W * (0.25 + Math.random() * 0.5), y = H * (0.25 + Math.random() * 0.5);
      const far = Math.min(1e9, ...ais.filter(a => a.alive).map(a => Math.hypot(a.x - x, a.y - y)));
      if (!best || far > best.far) best = {x, y, far};
    }
    return best;
  }
  function respawn() {
    const p = safeSpot(), [W, H] = lv.size;
    me.len = Math.max(START_LEN, me.len || 0);
    coil(me, p.x, p.y, Math.atan2(H / 2 - p.y, W / 2 - p.x));
    Object.assign(me, {alive: true, inv: 2});
  }

  function loadWorld(w) {   // 重连：换成存档 / 队友那里的豆子和电脑蛇
    if (!w) return;
    foods = new Map();
    for (const [id, x, y, v, c] of w.f || []) foods.set(id, {id, x, y, v, c});
    foodN = Math.max(w.n || 0, ...[...foods.keys()].map(k => k + 1), foodN);
    (w.ai || []).forEach(([x, y, len, alive], i) => {
      const a = ais[i]; if (!a) return;
      Object.assign(a, {x, y, len, alive: !!alive, path: [{x, y}], think: 0});
      if (a.track) { a.track.clear(); a.track.push({x, y}); }
    });
  }

  // ---------------------------------------------------------------- 开房间的设备：豆子、电脑蛇、过关
  function addFood(x, y, v) {
    const [W, H] = lv.size, id = ++foodN;
    const f = {id, x: Math.max(20, Math.min(W - 20, x ?? 30 + rng() * (W - 60))), y: Math.max(20, Math.min(H - 20, y ?? 30 + rng() * (H - 60))),
               v: v || (rng() < 0.15 ? 3 : rng() < 0.4 ? 2 : 1), c: Math.floor(rng() * FOOD_COLORS.length)};
    foods.set(id, f); return f;
  }
  const pack = f => [f.id, Math.round(f.x), Math.round(f.y), f.v, f.c];
  function hostEat(ids) {
    ids.forEach(id => foods.delete(id));
    const add = [];
    while (foods.size < lv.food) add.push(pack(addFood()));
    mp.net.send({t: 'food', del: ids, add});
  }
  function hostDrop(pts) {
    const add = pts.map(([x, y]) => pack(addFood(x + (rng() - 0.5) * 10, y + (rng() - 0.5) * 10, 2)));
    mp.net.send({t: 'food', del: [], add});
  }
  function spawnAi(a) {
    const [W, H] = lv.size;
    for (let k = 0; k < 20; k++) {
      const x = 60 + rng() * (W - 120), y = 60 + rng() * (H - 120);
      const far = [me, peer].every(s => !s || !s.alive || Math.hypot(s.x - x, s.y - y) > 350);
      if (far || k === 19) { Object.assign(a, {x, y, a: rng() * Math.PI * 2, len: lv.ai_len, path: [{x, y}], alive: true, think: 0}); return; }
    }
  }
  function hostStep(dt) {
    const [W, H] = lv.size;
    for (const a of ais) {
      if (!a.alive) { if ((a.back -= dt) <= 0) spawnAi(a); continue; }
      a.think -= dt;
      if (a.think <= 0) {   // 想一想往哪游：靠墙就转开，有时去拦玩家，平时去吃最近的豆子
        a.think = 0.25 + rng() * 0.3;
        let tx = null, ty = null;
        const players = [me, peer].filter(s => s && s.alive);
        if (players.length && rng() < lv.ai_aggr) {
          const p = players.sort((u, v) => Math.hypot(u.x - a.x, u.y - a.y) - Math.hypot(v.x - a.x, v.y - a.y))[0];
          if (Math.hypot(p.x - a.x, p.y - a.y) < 400) { tx = p.x + Math.cos(p.a || 0) * 120; ty = p.y + Math.sin(p.a || 0) * 120; }
        }
        if (tx === null) {
          let best = null, bd = 1e9;
          for (const f of foods.values()) { const d = Math.hypot(f.x - a.x, f.y - a.y); if (d < bd) { bd = d; best = f; } }
          if (best && bd < 500) { tx = best.x; ty = best.y; } else { tx = a.x + Math.cos(a.a) * 200; ty = a.y + Math.sin(a.a) * 200; }
        }
        a.want = Math.atan2(ty - a.y, tx - a.x);
      }
      const m = 90;   // 离墙太近：朝场地中间转
      if (a.x < m || a.y < m || a.x > W - m || a.y > H - m) a.want = Math.atan2(H / 2 - a.y, W / 2 - a.x);
      let d = a.want - a.a; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI;
      a.a += Math.sign(d) * Math.min(Math.abs(d), TURN * 0.8 * dt);
      const sp = SPEED * lv.ai_speed;
      const nx = Math.max(12, Math.min(W - 12, a.x + Math.cos(a.a) * sp * dt)), ny = Math.max(12, Math.min(H - 12, a.y + Math.sin(a.a) * sp * dt));
      grow(a, nx, ny);
      // 电脑蛇的头撞到玩家身上：散成豆子
      if (hitsBody(nx, ny, me, 0) || hitsBody(nx, ny, peer, 0) || ais.some(o => o !== a && o.alive && hitsBody(nx, ny, o, 0))) aiDie(a);
      else {   // 电脑蛇也吃豆子
        const eaten = [];
        for (const [id, f] of foods) if (Math.hypot(f.x - nx, f.y - ny) < 14) eaten.push(id);
        if (eaten.length) { a.len = Math.min(lv.ai_len * 2, a.len + eaten.length); hostEat(eaten); }
      }
    }
    snapT -= dt;
    if (snapT <= 0) { snapT = 0.1; mp.net.send({t: 'ai', s: ais.map(a => [a.id, Math.round(a.x), Math.round(a.y), a.len, a.alive ? 1 : 0])}); }
    if (ended) return;
    if (teamLen() >= lv.target) { ended = true; Play.sfx.play('win'); mp.finish('win', {score: teamLen()}); }
    else if (mp.elapsed() >= lv.time) { ended = true; mp.finish('lose', {why: `时间到了！我们一共长到 ${teamLen()}，目标是 ${lv.target}，再试一次！`}); }
  }
  function aiDie(a) {
    a.alive = false; a.back = 4;
    const pts = body(a).filter((_, i) => i % 2 === 0).map(p => [p.x, p.y]);
    fx.burst(a.x, a.y, {n: 20, colors: a.colors, speed: 240, size: 5, grav: 0});
    fx.text(a.x, a.y - 20, '撞上啦！', {size: 22, color: '#ff7a00'});
    Play.sfx.play('boom');
    hostDrop(pts.map(([x, y]) => [Math.round(x), Math.round(y)]));
    a.path = [];
  }

  // ---------------------------------------------------------------- 画
  function render() {
    const st = sh.stage, c = st.ctx;
    st.begin();
    const sc = SCENES[lv && lv.world && lv.world.scene] || SCENES.day;
    c.fillStyle = sc.out; c.fillRect(0, 0, st.W, st.H);
    if (!lv) return;
    const [W, H] = lv.size, focus = me && (me.alive || !peer) ? me : peer || me;
    const k = Math.max(st.W / Math.max(W, 1100), st.H / Math.max(H, 650), 0.62);   // 比例：一屏看得到周围一大片
    if (focus) { cam.x += ((focus.x || W / 2) - cam.x) * 0.15; cam.y += ((focus.y || H / 2) - cam.y) * 0.15; }
    const [sx, sy] = fx.offset();
    c.save();
    c.translate(st.W / 2 + sx, st.H / 2 + sy); c.scale(k, k); c.translate(-cam.x, -cam.y);
    // 场地
    c.fillStyle = sc.field; c.fillRect(0, 0, W, H);
    c.strokeStyle = sc.grid; c.lineWidth = 2;
    for (let x = 0; x <= W; x += 60) { c.beginPath(); c.moveTo(x, 0); c.lineTo(x, H); c.stroke(); }
    for (let y = 0; y <= H; y += 60) { c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke(); }
    if (sc.sparkle) { c.fillStyle = sc.sparkle; for (let i = 0; i < 80; i++) { c.globalAlpha = 0.3 + 0.5 * Math.abs(Math.sin(t + i)); c.fillRect((i * 337) % W, (i * 211) % H, 2, 2); } c.globalAlpha = 1; }
    c.strokeStyle = '#ff6b6b'; c.lineWidth = 8; c.strokeRect(0, 0, W, H);
    for (const f of foods.values()) {
      const r = 3 + f.v * 1.6 + Math.sin(t * 4 + f.id) * 0.8;
      c.fillStyle = FOOD_COLORS[f.c] || '#ffd23f'; c.globalAlpha = 0.35; c.beginPath(); c.arc(f.x, f.y, r + 4, 0, 7); c.fill();
      c.globalAlpha = 1; c.beginPath(); c.arc(f.x, f.y, r, 0, 7); c.fill();
    }
    for (const a of ais) if (a.alive) drawSnake(c, a, '');
    if (peer && peer.alive && mp) drawSnake(c, peer, mp.peer.name);
    if (me && me.alive && mp) drawSnake(c, me, mp.me.name, true);
    if (peer && !peer.alive && peer.x) bubbleQ(c, peer.x, peer.y);
    if (me && !me.alive && me.x) bubbleQ(c, me.x, me.y);
    fx.draw(c);
    c.restore();
    minimap(c, st, W, H);
  }
  function drawSnake(c, s, name, mine) {
    const segs = body(s), r = radius(s);
    if (mine && me.inv > 0 && Math.floor(t * 10) % 2) c.globalAlpha = 0.5;
    for (let i = segs.length - 1; i >= 0; i--) {
      c.fillStyle = s.colors[i % 4 < 2 ? 0 : 1]; c.beginPath(); c.arc(segs[i].x, segs[i].y, r, 0, 7); c.fill();
    }
    const hx = s.x, hy = s.y, a = Math.atan2(hy - (segs[1] || segs[0] || s).y, hx - (segs[1] || segs[0] || s).x) || s.a || 0;
    c.fillStyle = s.colors[0]; c.beginPath(); c.arc(hx, hy, r + 2, 0, 7); c.fill();
    for (const d of [-1, 1]) {
      const ex = hx + Math.cos(a + d * 0.6) * r * 0.6, ey = hy + Math.sin(a + d * 0.6) * r * 0.6;
      c.fillStyle = '#fff'; c.beginPath(); c.arc(ex, ey, r * 0.42, 0, 7); c.fill();
      c.fillStyle = '#2b2340'; c.beginPath(); c.arc(ex + Math.cos(a) * 2, ey + Math.sin(a) * 2, r * 0.2, 0, 7); c.fill();
    }
    c.globalAlpha = 1;
    if (s.boost) { c.globalAlpha = 0.3; c.strokeStyle = '#fff'; c.lineWidth = 3; c.beginPath(); c.arc(hx, hy, r + 6, 0, 7); c.stroke(); c.globalAlpha = 1; }
    if (name) {
      c.font = '800 13px "PingFang SC", system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
      const w = c.measureText(name).width + 12, ny = hy - r - 18;
      c.fillStyle = mine ? 'rgba(255,255,255,.92)' : 'rgba(43,35,64,.8)'; Play.roundRect(c, hx - w / 2, ny - 9, w, 18, 9); c.fill();
      c.fillStyle = mine ? '#2b2340' : '#fff'; c.fillText(name, hx, ny);
    }
  }
  function bubbleQ(c, x, y) {
    c.fillStyle = '#fff'; c.strokeStyle = '#2b2340'; c.lineWidth = 3;
    c.beginPath(); c.arc(x, y + Math.sin(t * 4) * 3, 16, 0, 7); c.fill(); c.stroke();
    c.fillStyle = '#2b2340'; c.font = 'bold 18px system-ui'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('?', x, y + Math.sin(t * 4) * 3);
  }
  function minimap(c, st, W, H) {
    const mw = 120, mh = mw * H / W, x0 = st.W - mw - 10, y0 = st.H - mh - 10;
    c.fillStyle = 'rgba(43,35,64,.55)'; Play.roundRect(c, x0 - 3, y0 - 3, mw + 6, mh + 6, 8); c.fill();
    const dot = (s, col, r) => { if (s && s.alive) { c.fillStyle = col; c.beginPath(); c.arc(x0 + s.x / W * mw, y0 + s.y / H * mh, r, 0, 7); c.fill(); } };
    for (const a of ais) dot(a, a.colors[0], 2.5);
    if (peer && mp) dot(peer, peer.colors[0], 4);
    if (me && mp) dot(me, '#fff', 4.5);
  }

  // ---------------------------------------------------------------- 外壳
  function pointerSetup(canvas) {
    const toWorld = e => {
      const st = sh.stage, r = canvas.getBoundingClientRect();
      const lx = (e.clientX - r.left) / r.width * st.W, ly = (e.clientY - r.top) / r.height * st.H;
      const [W, H] = lv.size, k = Math.max(st.W / Math.max(W, 1100), st.H / Math.max(H, 650), 0.62);
      return {x: (lx - st.W / 2) / k + cam.x, y: (ly - st.H / 2) / k + cam.y, sx: lx, sy: ly};
    };
    let held = false;
    canvas.addEventListener('pointerdown', e => { if (ended) return; held = true; pointer = toWorld(e); canvas.setPointerCapture && canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointermove', e => { if (held) pointer = toWorld(e); });
    const up = () => { held = false; pointer = null; };
    canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
    // 拖动时屏幕跟着动：每帧把屏幕上的那个点重新换成场地坐标
    setInterval(() => { if (held && pointer && lv) { const st = sh.stage, [W, H] = lv.size, k = Math.max(st.W / Math.max(W, 1100), st.H / Math.max(H, 650), 0.62); pointer.x = (pointer.sx - st.W / 2) / k + cam.x; pointer.y = (pointer.sy - st.H / 2) / k + cam.y; } }, 50);
  }
  let pointerReady = false;
  PlayShell.init({
    quiz: 'revive', tempo: 126,
    rules: ['两条小蛇是一队，一起<b>吃豆子长大</b>，两条加起来够长就过关。',
            '← → 转弯，按住空格冲刺（会变短一点）；手机、平板：手指按住往哪拖就往哪游。',
            '撞到墙、撞到电脑蛇就倒下：答题复活。电脑蛇的头撞到你们身上，它就散成一地豆子！',
            '答错多欠一道（最多欠 3 道）；「这题不会」不加。两条小蛇互相穿过去不会撞。',
            '有时间限制；在目标时间内过关、倒下不超过 1 次，还能多拿星星。'],
    keys: '电脑键盘：← → 或 A D 转弯，空格 冲刺；也可以按住鼠标往哪拖就往哪游',
    input: {left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], boost: [' ', 'ArrowUp', 'w']},
    touch: [['boost', '⚡ 冲刺', 'big']], touchSplit: 0,
    preview(s, l) {
      sh = s; fx = fx || new Play.FX();
      if (!ended) return;
      lv = l; me = null; peer = null; ais = []; foods = new Map(); mp = null;
      const r = MpNet.rng(7);
      for (let i = 0; i < 40; i++) foods.set(i, {id: i, x: r() * l.size[0], y: r() * l.size[1], v: 1 + Math.floor(r() * 3), c: i % 6});
      cam = {x: l.size[0] / 2, y: l.size[1] / 2};
      for (let i = 0; i < Math.min(3, l.ai); i++) { const a = snake(l.size[0] * (0.3 + 0.2 * i), l.size[1] * 0.4, 0, l.ai_len, AI_COLORS[i]); for (let j = 0; j < 60; j++) grow(a, a.x + 4, a.y + Math.sin(j / 6) * 4); ais.push(a); }
      render();
    },
    begin(ctx) {
      mp = ctx; sh = ctx.sh; fx = new Play.FX(); lv = ctx.level; t = 0; clockT = 0; snapT = 0; ended = false; foodN = 0;
      if (!pointerReady) { pointerSetup(sh.canvas); pointerReady = true; }
      rng = MpNet.rng(ctx.seed);
      const [W, H] = lv.size, mine = ctx.roleIndex(ctx.me.role);
      me = snake(W / 2 + (mine ? 60 : -60), H / 2, mine ? 0 : Math.PI, START_LEN, ROLE_COLOR[ctx.me.role] || ROLE_COLOR.green);
      for (let i = 0; i < START_LEN * 3; i++) grow(me, me.x + Math.cos(me.a) * 2.5, me.y + Math.sin(me.a) * 2.5);
      peer = snake(W / 2 + (mine ? -60 : 60), H / 2, mine ? Math.PI : 0, START_LEN, ROLE_COLOR[ctx.peer.role] || ROLE_COLOR.pink);
      peer.track = new MpNet.Track(100);
      foods = new Map(); ais = [];
      for (let i = 0; i < lv.ai; i++) ais.push(Object.assign(snake(0, 0, 0, lv.ai_len, AI_COLORS[i % AI_COLORS.length]), {id: i, track: new MpNet.Track(100)}));
      const rs = ctx.resume;
      if (rs && rs.world) loadWorld(rs.world);
      else if (ctx.host) { for (let i = 0; i < lv.food; i++) addFood(); ais.forEach(spawnAi); }
      if (ctx.host) {
        while (foods.size < lv.food) addFood();
        setTimeout(() => mp.net.send({t: 'food', all: [...foods.values()].map(pack)}), 300);
      }
      if (rs && rs.me) {   // 接着玩：还是原来那么长、在原来的地方
        me.len = Math.max(MIN_LEN, rs.me.len | 0 || START_LEN);
        if (rs.me.alive) { coil(me, rs.me.x, rs.me.y, rs.me.a || 0); me.inv = 2; }
        else { me.alive = false; me.path = []; me.x = rs.me.x; me.y = rs.me.y; }
      }
      cam = {x: me.x, y: me.y};
      if (lv.tip) mp.toast(lv.tip.split('。')[0], 'good');
      const net = ctx.net;
      net.off('s', 'food', 'ai', 'eat', 'drop');
      net.on('s', m => {
        if (m.d) { if (peer.alive) { peer.alive = false; peer.path = []; } peer.x = m.x; peer.y = m.y; return; }
        if (!peer.alive) { peer.alive = true; peer.path = [{x: m.x, y: m.y}]; peer.track.clear(); }
        peer.len = m.len; peer.a = m.a; peer.boost = !!m.b; peer.track.push(m);
      });
      net.on('food', m => {
        if (mp.host) return;
        if (m.all) { foods = new Map(); }
        for (const [id, x, y, v, c] of (m.all || m.add || [])) foods.set(id, {id, x, y, v, c});
        for (const id of m.del || []) foods.delete(id);
      });
      net.on('eat', m => { if (mp.host) hostEat((m.ids || []).filter(id => foods.has(id))); });
      net.on('drop', m => { if (mp.host) hostDrop((m.pts || []).slice(0, 40)); });
      net.on('ai', m => {
        if (mp.host) return;
        for (const [id, x, y, len, alive] of m.s) {
          const a = ais[id]; if (!a) continue;
          if (a.alive && !alive) { fx.burst(a.x, a.y, {n: 20, colors: a.colors, speed: 240, size: 5, grav: 0}); Play.sfx.play('boom'); a.path = []; }
          if (!a.alive && alive) { a.path = [{x, y}]; a.track.clear(); a.x = x; a.y = y; }
          a.alive = !!alive; a.len = len; a.track.push({x, y});
        }
      });
      net.stream(() => me && !ended ? (me.alive ? {t: 's', x: Math.round(me.x), y: Math.round(me.y), a: +me.a.toFixed(2), len: me.len, b: me.boost ? 1 : 0} : {t: 's', d: 1, x: Math.round(me.x || 0), y: Math.round(me.y || 0)}) : null, 15);
      if (loop) loop.stop();
      loop = Play.loop(step, render);
      loop.start();
    },
    onRevive() { if (me) { respawn(); Play.sfx.play('power'); } },
    save: () => me && {x: Math.round(me.x), y: Math.round(me.y), a: +me.a.toFixed(2), len: me.len, alive: me.alive ? 1 : 0},
    saveWorld: () => ({f: [...foods.values()].map(pack), n: foodN, ai: ais.map(a => [Math.round(a.x), Math.round(a.y), a.len, a.alive ? 1 : 0])}),
    peerView: () => peer && {x: Math.round(peer.x), y: Math.round(peer.y), a: +(peer.a || 0).toFixed(2), len: peer.len, alive: peer.alive ? 1 : 0},
    loadMe(v) {
      if (!me || !v || !me.alive || !v.alive) return;
      me.len = Math.max(MIN_LEN, v.len | 0 || me.len); coil(me, v.x, v.y, v.a || 0); me.inv = 2;
    },
    loadWorld(w) { loadWorld(w); if (mp && mp.host) mp.net.send({t: 'food', all: [...foods.values()].map(pack)}); },
    end() { ended = true; pointer = null; if (loop) loop.stop(); if (mp) mp.net.stopStreams(); },
  });
})();

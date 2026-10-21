/* 双人闯关（冰火人玩法，两个人各用一台设备）。关卡在 app/arena/games/levels/duo/，格式见 app/arena/games/duo.py。
   - 自己的角色自己的设备算（走、跳、掉进池子、拿宝石、推拉杆），每秒发 15 次位置给队友；
   - 按钮、门、桥、升降台按两个人的位置各自算（两边看到的几乎一样）；拉杆、宝石用消息同步；
   - 两个人都站在自己的门前，开房间的设备报过关（外壳 shell.js 负责结算）。
   - 掉进池子：外壳弹出答题（ctx.die），答对一道就从刚才站稳的地方复活。 */
(function () {
  const T = 30, COLS = 32, ROWS = 15, MW = COLS * T, MH = ROWS * T;
  const GRAV = 1500, JUMP = 480, SPEED = 165, CW = 20, CH = 26;
  const KILL = {fire: 'WG', water: 'FG'};
  const GEM = {fire: 'f', water: 'w'};
  const DOOR = {fire: 'D', water: 'E'};
  const LINK_COLORS = {a: '#ffcf3f', b: '#b07cff', c: '#3fd0a6', d: '#ff7aa8'};
  const SCENES = {
    forest: {bg: ['#2f4a3a', '#5d7b5a'], stone: '#8d8a6a', edge: '#6d6a4f', moss: '#7fb24a', brick: 'rgba(0,0,0,.08)'},
    cave: {bg: ['#2c2547', '#4b3f78'], stone: '#7c74a6', edge: '#5b5482', moss: '#9fd8ff', brick: 'rgba(0,0,0,.1)'},
    lava: {bg: ['#3a1a1a', '#7a3324'], stone: '#8a6a5a', edge: '#5f463b', moss: '#ffb36b', brick: 'rgba(0,0,0,.12)'},
    ice: {bg: ['#1f3550', '#4f7ea8'], stone: '#a9c4dc', edge: '#7d9bb8', moss: '#e8f6ff', brick: 'rgba(0,30,60,.08)'},
  };
  const POOL = {F: ['#ff9a3d', '#ff4d2e', '#ffd46b'], W: ['#5ab8ff', '#2f6fdc', '#c9ecff'], G: ['#7be36b', '#2fa04a', '#d4ffb0']};

  let sh, mp, loop, fx, Wd = null, me = null, peerTrack = null, peerRaw = null, t = 0, clockT = 0, doorT = 0, ended = true;

  // ---------------------------------------------------------------- 关卡
  function build(lv) {
    const g = lv.map.map(r => r.split(''));
    const w = {lv, g, gems: new Map(), starts: {}, doors: {}, btns: [], levers: [], gates: [], bridges: [], lifts: [], got: 0,
               scene: (lv.world && lv.world.scene) || 'forest'};
    for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
      const ch = g[r][c];
      if (ch === 'f' || ch === 'w') { w.gems.set(`${r},${c}`, {r, c, kind: ch}); g[r][c] = '.'; }
      if (ch === '1') { w.starts.fire = {c, r}; g[r][c] = '.'; }
      if (ch === '2') { w.starts.water = {c, r}; g[r][c] = '.'; }
      if (ch === 'D') { w.doors.fire = {c, r}; g[r][c] = '.'; }
      if (ch === 'E') { w.doors.water = {c, r}; g[r][c] = '.'; }
    }
    (lv.objects || []).forEach((o, i) => {
      const it = Object.assign({i}, o);
      if (o.k === 'btn') w.btns.push(it);
      if (o.k === 'lever') { it.on = false; w.levers.push(it); }
      if (o.k === 'gate') { it.open = it.inv ? 1 : 0; w.gates.push(it); }
      if (o.k === 'bridge') { it.on = 0; w.bridges.push(it); }
      if (o.k === 'lift') { it.pos = o.y * T; it.dy = 0; w.lifts.push(it); }
    });
    return w;
  }
  function loadWorld(w) {   // 重连：还剩哪些宝石、拉杆在哪边（门、桥、升降台跟着拉杆和按钮自己动）
    if (!w || !Array.isArray(w.gems)) return;
    const keep = new Set(w.gems);
    for (const k of [...Wd.gems.keys()]) if (!keep.has(k)) Wd.gems.delete(k);
    Wd.got = Math.max(Wd.got, w.got | 0);
    (w.lv || []).forEach((on, i) => { if (Wd.levers[i]) Wd.levers[i].on = !!on; });
  }
  const tile = (r, c) => (r < 0 || c < 0 || r >= ROWS || c >= COLS) ? '#' : Wd.g[r][c];
  const solidTile = ch => ch === '#' || ch === 'F' || ch === 'W' || ch === 'G';

  // 机关：按钮被谁踩着、拉杆在哪边 → 哪些 id 打开 → 门、桥、升降台往哪动
  function bodies() {
    const out = [];
    if (me && !me.dead) out.push(me);
    if (peerRaw && !peerRaw.d) out.push({x: peerRaw.x, y: peerRaw.y});
    return out;
  }
  const hit = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  const btnRect = b => ({x: b.x * T + 3, y: b.y * T + T - 7, w: T - 6, h: 7});
  function active() {
    const on = {}, bs = bodies();
    for (const b of Wd.btns) { b.down = bs.some(p => hit({x: p.x, y: p.y, w: CW, h: CH + 1}, btnRect(b))); if (b.down) on[b.id] = true; }
    for (const l of Wd.levers) if (l.on) on[l.id] = true;
    return on;
  }
  const linkColor = id => LINK_COLORS[String(id)[0]] || '#ffcf3f';
  function movers(dt) {
    const on = active(), isOn = id => String(id).split('|').some(i => on[i]);   // "a|c"：a 或 c 打开都行
    for (const g of Wd.gates) { const want = isOn(g.id) !== !!g.inv ? 1 : 0; g.open += Math.sign(want - g.open) * Math.min(Math.abs(want - g.open), dt * 2.2); }
    for (const b of Wd.bridges) { const want = isOn(b.id) ? 1 : 0; b.on += Math.sign(want - b.on) * Math.min(Math.abs(want - b.on), dt * 4); }
    for (const l of Wd.lifts) {
      const want = (isOn(l.id) ? l.to : l.y) * T, d = want - l.pos, step = Math.sign(d) * Math.min(Math.abs(d), 85 * dt);
      l.pos += step; l.dy = step;
    }
  }
  function solids(x, y, w, h) {
    const out = [];
    const c0 = Math.floor(x / T) - 1, c1 = Math.floor((x + w) / T) + 1, r0 = Math.floor(y / T) - 1, r1 = Math.floor((y + h) / T) + 1;
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) if (solidTile(tile(r, c))) out.push({x: c * T, y: r * T, w: T, h: T});
    for (const g of Wd.gates) { const hh = g.h * T * (1 - g.open); if (hh > 1) out.push({x: g.x * T, y: g.y * T, w: g.w * T, h: hh}); }
    for (const b of Wd.bridges) if (b.on > 0.5) out.push({x: b.x * T, y: b.y * T, w: b.w * T, h: 10});
    for (const l of Wd.lifts) out.push({x: l.x * T, y: l.pos, w: l.w * T, h: 14, lift: l});
    return out;
  }

  // ---------------------------------------------------------------- 我的角色
  function spawn(role) {
    const s = Wd.starts[role];
    return {role, x: s.c * T + (T - CW) / 2, y: s.r * T + T - CH, vx: 0, vy: 0, g: false, face: 1, dead: false, ride: null,
            safe: null, door: false, inv: 0, walk: 0};
  }
  function step(dt) {
    t += dt;
    movers(dt);
    fx.update(dt);
    if (!me || ended) return;
    if (me.inv > 0) me.inv -= dt;
    if (!me.dead) physics(dt);
    // 两个人都到门口：开房间的设备报过关
    if (mp.host && me.door && peerRaw && peerRaw.a && !peerRaw.d && !me.dead) {
      doorT += dt;
      if (doorT > 0.4 && !mp.finishing) { ended = true; Play.sfx.play('win'); mp.finish('win', {gems: Wd.got}); }
    } else doorT = 0;
    clockT -= dt;
    if (clockT <= 0) { clockT = 0.25; sh.hudClock(`⏱ ${mp.elapsed().toFixed(0)} 秒 · 💎 ${Wd.got}/${Wd.lv.gems}`); }
  }
  function physics(dt) {
    const I = Play.input, dir = (I.keys.right ? 1 : 0) - (I.keys.left ? 1 : 0);
    me.vx = dir * SPEED;
    if (dir) me.face = dir;
    me.walk += Math.abs(me.vx) * dt;
    if (I.keys.jump && me.g) { me.vy = -JUMP; me.g = false; Play.sfx.play('jump'); }
    if (me.ride) me.y += me.ride.dy;   // 站在升降台上跟着走
    me.vy = Math.min(me.vy + GRAV * dt, 900);
    // 横着走
    me.x += me.vx * dt;
    for (const s of solids(me.x, me.y, CW, CH)) if (hit({x: me.x, y: me.y, w: CW, h: CH}, s)) {
      if (me.vx > 0) me.x = s.x - CW; else if (me.vx < 0) me.x = s.x + s.w;
    }
    // 上下
    me.y += me.vy * dt; me.g = false; me.ride = null;
    for (const s of solids(me.x, me.y, CW, CH)) if (hit({x: me.x, y: me.y, w: CW, h: CH}, s)) {
      if (me.vy >= 0) { me.y = s.y - CH; me.vy = 0; me.g = true; me.ride = s.lift || null; }
      else { me.y = s.y + s.h; me.vy = 0; }
    }
    unstick();
    // 脚下是什么
    if (me.g && !me.ride) {
      const r = Math.floor((me.y + CH + 1) / T), cs = [Math.floor((me.x + 5) / T), Math.floor((me.x + CW - 5) / T)];
      const under = cs.map(c => tile(r, c));
      if (me.inv <= 0 && under.some(ch => KILL[me.role].includes(ch))) return die(under.find(ch => KILL[me.role].includes(ch)));
      if (under.every(ch => ch === '#')) me.safe = {x: me.x, y: me.y};
    }
    // 宝石
    for (const [k, gm] of Wd.gems) {
      if (gm.kind !== GEM[me.role]) continue;
      if (hit({x: me.x, y: me.y, w: CW, h: CH}, {x: gm.c * T + 7, y: gm.r * T + 7, w: T - 14, h: T - 14})) {
        Wd.gems.delete(k); Wd.got++; mp.net.send({t: 'gem', k});
        Play.sfx.play('coin'); fx.burst(gm.c * T + T / 2, gm.r * T + T / 2, {n: 12, colors: me.role === 'fire' ? ['#ff6b3d', '#ffd23f'] : ['#5ab8ff', '#fff'], speed: 200, size: 4, shape: 'star', grav: 200});
      }
    }
    // 拉杆：往右推打开，往左推关上
    for (const l of Wd.levers) {
      if (!hit({x: me.x, y: me.y, w: CW, h: CH}, {x: l.x * T + 4, y: l.y * T + 8, w: T - 8, h: T - 8})) continue;
      if (me.vx > 0 && !l.on) setLever(l, true, true);
      if (me.vx < 0 && l.on) setLever(l, false, true);
    }
    // 门
    const d = Wd.doors[me.role];
    const wasDoor = me.door;
    me.door = me.g && Math.abs(me.x + CW / 2 - (d.c * T + T / 2)) < 12 && Math.abs(me.y + CH - (d.r * T + T)) < 4;
    if (me.door && !wasDoor) { Play.sfx.play('star'); if (!(peerRaw && peerRaw.a)) mp.toast(`到门口啦，等${mp.peer ? mp.peer.name : '队友'}`, 'good'); }
  }
  function unstick() {   // 门关下来、升降台压下来：把自己挤到最近的空地方
    for (let k = 0; k < 3; k++) {
      const s = solids(me.x, me.y, CW, CH).find(o => hit({x: me.x, y: me.y, w: CW, h: CH}, o));
      if (!s) return;
      const opts = [[s.x - CW - me.x, 0], [s.x + s.w - me.x, 0], [0, s.y - CH - me.y], [0, s.y + s.h - me.y]];
      opts.sort((a, b) => Math.abs(a[0]) + Math.abs(a[1]) - Math.abs(b[0]) - Math.abs(b[1]));
      me.x += opts[0][0]; me.y += opts[0][1];
      if (opts[0][1] < 0) { me.vy = Math.min(0, me.vy); me.g = true; }
    }
  }
  function setLever(l, on, mine) {
    l.on = on; Play.sfx.play('click');
    if (mine) mp.net.send({t: 'lever', i: l.i, on});
  }
  function die(ch) {
    me.dead = true; me.vx = me.vy = 0;
    const c = POOL[ch] || POOL.G;
    fx.burst(me.x + CW / 2, me.y + CH / 2, {n: 26, colors: [c[0], c[2], '#fff'], speed: 260, size: 5, grav: 400});
    fx.shake(5, 0.25); Play.sfx.play('hurt');
    mp.toast(me.role === 'fire' ? '哎呀，火苗碰到水了！答题复活' : ch === 'G' ? '碰到毒水了！答题复活' : '哎呀，水滴碰到岩浆了！答题复活', 'warn');
    mp.die();
  }

  // ---------------------------------------------------------------- 画
  function view() {
    const st = sh.stage, k = Math.min(st.W / MW, st.H / MH);
    return {k, ox: (st.W - MW * k) / 2, oy: (st.H - MH * k) / 2};
  }
  function render() {
    const st = sh.stage, c = st.ctx;
    st.begin();
    const v = view(), sc = SCENES[Wd.scene] || SCENES.forest;
    c.fillStyle = sc.bg[0]; c.fillRect(0, 0, st.W, st.H);
    c.save();
    const [sx, sy] = fx.offset();
    c.translate(v.ox + sx, v.oy + sy); c.scale(v.k, v.k);
    drawBack(c, sc);
    drawObjects(c);
    drawTiles(c, sc);
    drawGems(c);
    if (peerTrack) {
      const p = peerTrack.get(['x', 'y']);
      if (p && mp.peer) drawHero(c, p.x, p.y, mp.peer.role, p.f || 1, p.vx ? t * 10 : 0, mp.peer.name, p.d ? 0.35 : 1, p.d);
    }
    if (me) drawHero(c, me.x, me.y, me.role, me.face, me.vx && me.g ? me.walk / 9 : 0, mp ? mp.me.name : '', me.dead ? 0.35 : (me.inv > 0 && Math.floor(t * 12) % 2 ? 0.5 : 1), me.dead, true);
    fx.draw(c);
    c.restore();
  }
  function drawBack(c, sc) {
    const g = c.createLinearGradient(0, 0, 0, MH); g.addColorStop(0, sc.bg[0]); g.addColorStop(1, sc.bg[1]);
    c.fillStyle = g; c.fillRect(0, 0, MW, MH);
    c.strokeStyle = sc.brick; c.lineWidth = 1;
    for (let r = 0; r < ROWS * 2; r++) for (let q = 0; q < COLS; q++) { c.strokeRect(q * T + (r % 2 ? T / 2 : 0), r * T / 2, T, T / 2); }
  }
  function drawTiles(c, sc) {
    for (let r = 0; r < ROWS; r++) for (let q = 0; q < COLS; q++) {
      const ch = Wd.g[r][q], x = q * T, y = r * T;
      if (ch === '#') {
        c.fillStyle = sc.stone; c.fillRect(x, y, T, T);
        c.fillStyle = sc.edge; c.fillRect(x, y + T - 4, T, 4); c.fillRect(x + T - 3, y, 3, T);
        c.fillStyle = 'rgba(255,255,255,.12)'; c.fillRect(x, y, T, 3);
        if (r > 0 && !solidTile(Wd.g[r - 1][q])) { c.fillStyle = sc.moss; c.fillRect(x, y, T, 4); }
      } else if (POOL[ch]) {
        const p = POOL[ch];
        c.fillStyle = sc.stone; c.fillRect(x, y + T * 0.55, T, T * 0.45);
        const gr = c.createLinearGradient(0, y, 0, y + T); gr.addColorStop(0, p[0]); gr.addColorStop(1, p[1]);
        c.fillStyle = gr; c.beginPath(); c.moveTo(x, y + T * 0.7);
        for (let i = 0; i <= 6; i++) c.lineTo(x + i * T / 6, y + 6 + Math.sin(t * 4 + (q * 6 + i) * 0.9) * 2.2);
        c.lineTo(x + T, y + T * 0.7); c.closePath(); c.fill();
        c.fillStyle = p[2]; c.globalAlpha = 0.7;
        const bx = x + ((t * 9 + q * 13) % T), by = y + 10 + ((q * 7 + Math.floor(t * 2)) % 6);
        c.beginPath(); c.arc(bx, by, 1.8, 0, 7); c.fill(); c.globalAlpha = 1;
      }
    }
    for (const role of ['fire', 'water']) {   // 门
      const d = Wd.doors[role], x = d.c * T, y = d.r * T - T;
      const lit = (me && me.role === role && me.door) || (peerRaw && mp && mp.peer && mp.peer.role === role && peerRaw.a);
      c.fillStyle = '#3a2f2a'; Play.roundRect(c, x + 1, y - 2, T - 2, 2 * T + 2, 12); c.fill();
      c.fillStyle = role === 'fire' ? (lit ? '#ffb35c' : '#c4532e') : (lit ? '#9fdcff' : '#2f6fdc');
      Play.roundRect(c, x + 4, y + 2, T - 8, 2 * T - 4, 10); c.fill();
      c.font = '16px system-ui'; c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillText(role === 'fire' ? '🔥' : '💧', x + T / 2, y + T * 0.75);
      if (lit) { c.globalAlpha = 0.25 + 0.15 * Math.sin(t * 6); c.fillStyle = '#fff'; Play.roundRect(c, x + 4, y + 2, T - 8, 2 * T - 4, 10); c.fill(); c.globalAlpha = 1; }
    }
  }
  function drawObjects(c) {
    for (const b of Wd.btns) {
      const r = btnRect(b), col = linkColor(b.id), h = b.down ? 3 : 6;
      c.fillStyle = '#3a2f2a'; c.fillRect(r.x - 2, r.y + r.h - 3, r.w + 4, 3);
      c.fillStyle = col; Play.roundRect(c, r.x + 2, r.y + r.h - 3 - h, r.w - 4, h, 3); c.fill();
    }
    for (const l of Wd.levers) {
      const x = l.x * T + T / 2, y = l.y * T + T, col = linkColor(l.id);
      c.strokeStyle = '#3a2f2a'; c.lineWidth = 4; c.beginPath(); c.moveTo(x, y - 4); c.lineTo(x + (l.on ? 9 : -9), y - 22); c.stroke();
      c.fillStyle = col; c.beginPath(); c.arc(x + (l.on ? 9 : -9), y - 22, 5, 0, 7); c.fill();
      c.fillStyle = '#3a2f2a'; Play.roundRect(c, x - 10, y - 6, 20, 6, 3); c.fill();
    }
    for (const g of Wd.gates) {
      const hh = g.h * T * (1 - g.open); if (hh < 1) continue;
      const col = linkColor(g.id);
      c.fillStyle = '#4a4058'; c.fillRect(g.x * T + 3, g.y * T, g.w * T - 6, hh);
      c.fillStyle = col; for (let y = g.y * T + 6; y < g.y * T + hh - 4; y += 14) c.fillRect(g.x * T + 6, y, g.w * T - 12, 5);
    }
    for (const b of Wd.bridges) {
      if (b.on < 0.02) { c.globalAlpha = 0.25; c.setLineDash([6, 6]); c.strokeStyle = linkColor(b.id); c.strokeRect(b.x * T, b.y * T, b.w * T, 10); c.setLineDash([]); c.globalAlpha = 1; continue; }
      c.globalAlpha = b.on; c.fillStyle = '#a5784a';
      for (let i = 0; i < b.w * 2; i++) c.fillRect(b.x * T + i * T / 2 + 1, b.y * T, T / 2 - 2, 10);
      c.fillStyle = linkColor(b.id); c.fillRect(b.x * T, b.y * T + 8, b.w * T, 2); c.globalAlpha = 1;
    }
    for (const l of Wd.lifts) {
      const col = linkColor(l.id);
      c.strokeStyle = 'rgba(255,255,255,.15)'; c.setLineDash([4, 6]); c.beginPath();
      c.moveTo(l.x * T + l.w * T / 2, l.to * T); c.lineTo(l.x * T + l.w * T / 2, l.y * T); c.stroke(); c.setLineDash([]);
      c.fillStyle = '#5b6070'; Play.roundRect(c, l.x * T, l.pos, l.w * T, 14, 4); c.fill();
      c.fillStyle = col; c.fillRect(l.x * T + 4, l.pos + 5, l.w * T - 8, 4);
    }
  }
  function drawGems(c) {
    for (const gm of Wd.gems.values()) {
      const x = gm.c * T + T / 2, y = gm.r * T + T / 2 + Math.sin(t * 3 + gm.c) * 2.5;
      c.fillStyle = gm.kind === 'f' ? '#ff4d3d' : '#3d9bff';
      c.beginPath(); c.moveTo(x, y - 9); c.lineTo(x + 8, y - 2); c.lineTo(x, y + 9); c.lineTo(x - 8, y - 2); c.closePath(); c.fill();
      c.fillStyle = 'rgba(255,255,255,.6)'; c.beginPath(); c.moveTo(x, y - 9); c.lineTo(x + 3, y - 2); c.lineTo(x - 4, y - 2); c.closePath(); c.fill();
    }
  }
  function drawHero(c, x, y, role, face, walk, name, alpha, dead, mine) {
    c.save(); c.globalAlpha = alpha;
    const cx = x + CW / 2, fire = role === 'fire';
    const body = fire ? '#ff7a2f' : '#3a8fff', dark = fire ? '#c84a16' : '#2560c0';
    const step = Math.sin(walk) * 3;
    c.fillStyle = dark;   // 脚
    c.fillRect(cx - 7, y + CH - 5 + Math.max(0, step), 5, 5); c.fillRect(cx + 2, y + CH - 5 + Math.max(0, -step), 5, 5);
    c.fillStyle = body; Play.roundRect(c, cx - 8, y + 11, 16, CH - 14, 6); c.fill();   // 身体
    if (fire) {   // 火焰头
      for (let i = 0; i < 3; i++) {
        const fl = Math.sin(t * 12 + i * 2) * 2;
        c.fillStyle = ['#ff4d2e', '#ff9a3d', '#ffd46b'][i];
        c.beginPath(); c.moveTo(cx - 9 + i * 1.5, y + 12); c.quadraticCurveTo(cx - 6, y - 4 - fl + i * 3, cx + (i - 1) * 3, y - 10 - fl + i * 5);
        c.quadraticCurveTo(cx + 6, y - 2 + i * 3, cx + 9 - i * 1.5, y + 12); c.closePath(); c.fill();
      }
    } else {   // 水滴头
      c.fillStyle = '#7cc8ff'; c.beginPath(); c.moveTo(cx, y - 10); c.quadraticCurveTo(cx + 11, y + 4, cx + 8, y + 9);
      c.arc(cx, y + 7, 8.5, 0.2, Math.PI - 0.2); c.quadraticCurveTo(cx - 11, y + 4, cx, y - 10); c.fill();
      c.fillStyle = 'rgba(255,255,255,.7)'; c.beginPath(); c.ellipse(cx - 3, y + 1, 2, 3.5, -0.4, 0, 7); c.fill();
    }
    // 眼睛
    c.fillStyle = '#fff'; c.beginPath(); c.arc(cx - 3 + face * 1.5, y + 6, 2.8, 0, 7); c.arc(cx + 3 + face * 1.5, y + 6, 2.8, 0, 7); c.fill();
    c.fillStyle = '#2b2340';
    if (dead) { c.font = 'bold 9px system-ui'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('× ×', cx + face, y + 6); }
    else { c.beginPath(); c.arc(cx - 3 + face * 2.5, y + 6.3, 1.4, 0, 7); c.arc(cx + 3 + face * 2.5, y + 6.3, 1.4, 0, 7); c.fill(); }
    c.restore();
    if (dead) {   // 在答题：头上一个问号泡泡
      c.save(); c.globalAlpha = 0.9; c.fillStyle = '#fff'; c.strokeStyle = '#2b2340'; c.lineWidth = 2;
      c.beginPath(); c.arc(cx, y - 22 + Math.sin(t * 4) * 2, 10, 0, 7); c.fill(); c.stroke();
      c.fillStyle = '#2b2340'; c.font = 'bold 13px system-ui'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('?', cx, y - 22 + Math.sin(t * 4) * 2);
      c.restore();
    }
    if (name) {
      c.font = '800 10px "PingFang SC", system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
      const w = c.measureText(name).width + 10, ny = y - (dead ? 38 : 20);
      c.fillStyle = mine ? 'rgba(255,255,255,.92)' : 'rgba(43,35,64,.75)'; Play.roundRect(c, cx - w / 2, ny - 7, w, 14, 7); c.fill();
      c.fillStyle = mine ? '#2b2340' : '#fff'; c.fillText(name, cx, ny);
    }
  }

  // ---------------------------------------------------------------- 外壳
  PlayShell.init({
    quiz: 'revive', tempo: 118,
    rules: ['一个人当 <b>小火苗</b> 🔥，一个人当 <b>小水滴</b> 💧，各用自己的平板或电脑。',
            '火苗不怕岩浆，水滴不怕水，<b>绿色毒水</b>谁都不能碰。',
            '踩住按钮门才开；拉杆往右推打开、往左推关上；升降台、桥也要队友帮忙。',
            '掉进池子就做一道题：<b>答对一道就复活</b>；答错了看懂讲解，换一道接着答。',
            '两个人都站到自己的门前就过关！拿到全部宝石、在目标时间内过关，还能多拿星星。'],
    keys: '电脑键盘：← → 或 A D 走，↑ / W / 空格 跳',
    input: {left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], jump: ['ArrowUp', 'w', ' ']},
    touch: [['left', '◀', 'big'], ['right', '▶', 'big'], ['jump', '⤒ 跳', 'big']], touchSplit: 2,
    preview(s, lv) {
      sh = s; fx = fx || new Play.FX();
      if (!ended) return;
      Wd = build(lv); me = null; peerTrack = null; peerRaw = null;
      const st = sh.stage; st.begin(); render();
      const v = view(), c = st.ctx;
      c.save(); c.translate(v.ox, v.oy); c.scale(v.k, v.k);
      for (const role of ['fire', 'water']) { const p = spawn(role); drawHero(c, p.x, p.y, role, 1, 0, '', 1, false); }
      c.restore();
    },
    begin(ctx) {
      mp = ctx; sh = ctx.sh; fx = new Play.FX(); t = 0; doorT = 0; clockT = 0; ended = false;
      Wd = build(ctx.level); me = spawn(ctx.me.role); peerTrack = new MpNet.Track(100); peerRaw = null;
      const p0 = spawn(ctx.peer.role); peerTrack.push({x: p0.x, y: p0.y, f: 1}); peerRaw = {x: p0.x, y: p0.y};
      const rs = ctx.resume;
      if (rs && rs.world) loadWorld(rs.world);
      if (rs && rs.me && typeof rs.me.x === 'number') {   // 接着玩：回到原来的位置（倒下着的话等答完题回到安全的地方）
        Object.assign(me, {x: rs.me.x, y: rs.me.y, safe: rs.me.safe || null, dead: !!rs.me.dead, inv: 1.2});
      }
      if (ctx.level.tip && !rs) mp.toast(ctx.level.tip.split('。')[0], 'good');
      ctx.net.off('s', 'gem', 'lever');
      ctx.net.on('s', m => { peerRaw = m; peerTrack.push(m); });
      ctx.net.on('gem', m => { if (Wd.gems.delete(m.k)) { Wd.got++; Play.sfx.play('coin'); } });
      ctx.net.on('lever', m => { const l = Wd.levers.find(x => x.i === m.i); if (l) setLever(l, !!m.on, false); });
      ctx.net.stream(() => me && !ended ? {t: 's', x: Math.round(me.x), y: Math.round(me.y), vx: Math.round(me.vx), f: me.face, d: me.dead ? 1 : 0, a: me.door ? 1 : 0} : null, 15);
      if (loop) loop.stop();
      loop = Play.loop(step, render);
      loop.start();
    },
    onRevive() {
      if (!me) return;
      const s = me.safe || spawn(me.role);
      me.x = s.x; me.y = s.y; me.vx = me.vy = 0; me.dead = false; me.inv = 1.2;
      fx.ring(me.x + CW / 2, me.y + CH / 2, {color: '#fff', width: 4, grow: 300, life: 0.4});
      Play.sfx.play('power');
    },
    end() { ended = true; if (loop) loop.stop(); if (mp) mp.net.stopStreams(); },
    save: () => me && {x: Math.round(me.x), y: Math.round(me.y), dead: me.dead ? 1 : 0, safe: me.safe && {x: Math.round(me.safe.x), y: Math.round(me.safe.y)}},
    saveWorld: () => Wd && {gems: [...Wd.gems.keys()], got: Wd.got, lv: Wd.levers.map(l => l.on ? 1 : 0)},
    loadWorld: w => loadWorld(w),
    peerView: () => peerRaw && {x: Math.round(peerRaw.x), y: Math.round(peerRaw.y)},
    loadMe(v) { if (me && v && !me.dead && typeof v.x === 'number') { me.x = v.x; me.y = v.y; me.vx = me.vy = 0; me.inv = 1.2; } },
  });
})();

/* 泡泡堂（合作打怪，两个人各用一台设备）。关卡在 app/arena/mp/levels/bubble/，格式见 app/arena/mp/bubble.py。
   - 自己的角色自己的设备算（走、放泡泡、被困、捡道具、救队友），每秒发 15 次位置；
   - 泡泡什么时候炸、炸到哪、砖块掉什么、怪兽怎么走，都由开房间的设备算，再发给另一台（'boom'、'w' 消息）；
   - 被泡泡炸到或被怪兽碰到：困在泡泡里。队友跑过来碰一下就救出来（不算倒下），不然答题自救（外壳 mpshell.js）。
   - 怪兽全打败，开房间的设备报过关。
   - 刷新 / 换设备：存档里有自己（位置、道具、是不是被困着）和地图（炸开的砖块、地上的道具、怪兽、泡泡），接着玩。 */
(function () {
  const T = 40, COLS = 17, ROWS = 11, MW = COLS * T, MH = ROWS * T, HB = 14;
  const FUSE = 2.6, FLAME = 0.5;
  const MON = {s: {kind: 'slime', speed: 48}, b: {kind: 'bat', speed: 86}, g: {kind: 'ghost', speed: 44, ghost: true}};
  const ITEMS = {fire: '🔥', bub: '🫧', shoe: '👟'};
  const ROLE_COLOR = {red: '#ff5d6c', blue: '#4d8dff'};
  const SCENES = {
    candy: {floor: ['#ffe8f2', '#fff4f9'], wall: '#ff8fbf', wallHi: '#ffc4dd', wallLo: '#d9659a', box: '#c98b55', boxHi: '#e4ad78', ribbon: '#ff5d8f'},
    ice: {floor: ['#e3f2ff', '#f1f9ff'], wall: '#7fb6ff', wallHi: '#cfe5ff', wallLo: '#4f86d6', box: '#9fc7e8', boxHi: '#d6ecff', ribbon: '#5a8fd6'},
  };

  let sh, mp, loop, fx, g = null, scene = 'candy', bubbles = new Map(), flames = [], items = new Map(), mons = [], monTracks = new Map();
  let me = null, peerTrack = null, peerRaw = null, t = 0, clockT = 0, snapT = 0, ended = true, bubN = 0, rng = Math.random, lastDir = null, saveT = 0, winT = 0;

  // ---------------------------------------------------------------- 地图
  const key = (r, c) => `${r},${c}`;
  const cellOf = v => Math.floor(v / T);
  const ctr = n => n * T + T / 2;
  function build(lv) {
    g = lv.map.map(r => r.split('')); scene = (lv.world && lv.world.scene) || 'candy';
    bubbles = new Map(); flames = []; items = new Map(); mons = []; monTracks = new Map();
    const starts = {};
    let id = 0;
    for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) {
      const ch = g[r][c];
      if (ch === '1' || ch === '2') { starts[ch] = {r, c}; g[r][c] = '.'; }
      if (MON[ch]) { mons.push({id: id++, kind: ch, x: ctr(c), y: ctr(r), dir: [0, 0], alive: true, speed: MON[ch].speed}); g[r][c] = '.'; }
    }
    return starts;
  }
  const bubbleAt = (r, c) => { for (const b of bubbles.values()) if (b.r === r && b.c === c) return b; return null; };
  function blocked(r, c, ghost) {
    if (r < 0 || c < 0 || r >= ROWS || c >= COLS) return true;
    const ch = g[r][c];
    if (ch === '#') return true;
    if (ch === '+' && !ghost) return true;
    const b = bubbleAt(r, c);
    return !!(b && !(b.passMe && !ghost));
  }

  // ---------------------------------------------------------------- 我的角色
  function boxFree(x, y) {
    for (const [dx, dy] of [[-HB, -HB], [HB, -HB], [-HB, HB], [HB, HB]]) if (blocked(cellOf(y + dy), cellOf(x + dx))) return false;
    return true;
  }
  function move(dx, dy, step) {
    const nx = me.x + dx * step, ny = me.y + dy * step;
    if (boxFree(nx, ny)) { me.x = nx; me.y = ny; return true; }
    // 拐角帮一把：前面那格是空的，就把身子往那一行 / 列的中间挪
    const r0 = cellOf(me.y), c0 = cellOf(me.x);
    if (dx) {
      const ahead = cellOf(me.x + dx * (HB + step));
      for (const r of [r0, me.y < ctr(r0) ? r0 - 1 : r0 + 1]) {
        if (!blocked(r, ahead) && Math.abs(ctr(r) - me.y) < T * 0.6) { me.y += Math.sign(ctr(r) - me.y) * Math.min(step, Math.abs(ctr(r) - me.y)); return true; }
      }
    } else {
      const ahead = cellOf(me.y + dy * (HB + step));
      for (const c of [c0, me.x < ctr(c0) ? c0 - 1 : c0 + 1]) {
        if (!blocked(ahead, c) && Math.abs(ctr(c) - me.x) < T * 0.6) { me.x += Math.sign(ctr(c) - me.x) * Math.min(step, Math.abs(ctr(c) - me.x)); return true; }
      }
    }
    return false;
  }
  function step(dt) {
    t += dt; fx.update(dt);
    flames = flames.filter(f => (f.t -= dt) > 0);
    if (mp.host) hostStep(dt);
    else for (const m of mons) { const tr = monTracks.get(m.id), p = tr && tr.get(['x', 'y']); if (p) { m.x = p.x; m.y = p.y; } }
    if (!me || ended) return;
    if (me.inv > 0) me.inv -= dt;
    // 自己放的泡泡：走出那一格之前可以穿过去
    for (const b of bubbles.values()) if (b.passMe && !overlapsCell(b.r, b.c)) b.passMe = false;
    if (!me.trapped) control(dt);
    checkHurt();
    clockT -= dt;
    if (clockT <= 0) { clockT = 0.25; sh.hudClock(`⏱ ${mp.elapsed().toFixed(0)} 秒 · 👾 ${mons.filter(m => m.alive).length} 只`); }
  }
  function overlapsCell(r, c) {
    return me.x + HB > c * T && me.x - HB < c * T + T && me.y + HB > r * T && me.y - HB < r * T + T;
  }
  function control(dt) {
    const I = Play.input;
    for (const d of ['up', 'down', 'left', 'right']) if (I.take(d)) lastDir = d;
    let d = lastDir && I.keys[lastDir] ? lastDir : ['up', 'down', 'left', 'right'].find(k => I.keys[k]);
    me.mv = false;
    if (d) {
      const v = {up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0]}[d];
      if (v[0]) me.face = v[0];
      me.mv = move(v[0], v[1], (110 + 25 * me.shoe) * dt);
    }
    if (I.take('bomb')) place();
    // 捡道具
    const k = key(cellOf(me.y), cellOf(me.x)), it = items.get(k);
    if (it) { items.delete(k); apply(it); mp.net.send({t: 'pick', k}); }
    // 救队友：碰一下困在泡泡里的队友
    if (peerRaw && peerRaw.d && Math.hypot(peerRaw.x - me.x, peerRaw.y - me.y) < 30 && t > saveT) {
      saveT = t + 1; mp.net.send({t: 'save', to: mp.peer.id});
      fx.burst(peerRaw.x, peerRaw.y, {n: 16, colors: ['#fff', '#9fe7ff', '#ffd23f'], speed: 220, shape: 'star', size: 5, grav: 100});
      Play.sfx.play('shield'); mp.toast(`你救了${mp.peer.name}！`, 'good');
    }
  }
  function apply(it) {
    if (it === 'fire') me.power = Math.min(6, me.power + 1);
    if (it === 'bub') me.max = Math.min(5, me.max + 1);
    if (it === 'shoe') me.shoe = Math.min(3, me.shoe + 1);
    Play.sfx.play('buy');
    mp.toast({fire: '🔥 泡泡炸得更远了', bub: '🫧 可以多放一个泡泡', shoe: '👟 跑得更快了'}[it], 'good');
  }
  function place() {
    const r = cellOf(me.y), c = cellOf(me.x);
    const mine = [...bubbles.values()].filter(b => b.owner === mp.me.id).length;
    if (mine >= me.max || bubbleAt(r, c) || g[r][c] !== '.') return;
    const b = {id: `${mp.me.id}-${++bubN}`, r, c, p: me.power, owner: mp.me.id, fuse: FUSE, born: t, passMe: true};
    bubbles.set(b.id, b); Play.sfx.play('pop');
    mp.net.send({t: 'bub', id: b.id, r, c, p: b.p});
  }
  function checkHurt() {
    if (me.trapped || me.inv > 0) return;
    const r = cellOf(me.y), c = cellOf(me.x);
    const fire = flames.some(f => f.r === r && f.c === c);
    const mon = mons.some(m => m.alive && Math.hypot(m.x - me.x, m.y - me.y) < 24);
    if (fire || mon) {
      me.trapped = true; me.mv = false;
      Play.sfx.play('hurt'); fx.shake(4, 0.2);
      mp.toast(mp.peer ? `被困住了！等${mp.peer.name}来救，或者答题自救` : '被困住了！答题自救', 'warn');
      mp.die();
    }
  }

  const pick = (o, ks) => Object.fromEntries(ks.filter(k => typeof o[k] === 'number').map(k => [k, o[k]]));
  function loadWorld(w) {   // 重连：换成存档 / 队友那里的地图
    if (!w || !w.g || w.g.length !== ROWS) return;
    g = w.g.map(r => r.split(''));
    items = new Map((w.it || []).filter(([, it]) => ITEMS[it]));
    for (const [id, x, y, alive] of w.m || []) {
      const m = mons.find(o => o.id === id); if (!m) continue;
      Object.assign(m, {x, y, alive: !!alive, dir: [0, 0], decided: undefined});
      const tr = monTracks.get(id); if (tr) { tr.clear(); tr.push({x, y}); }
    }
    bubbles = new Map();
    for (const [id, r, c, p, owner, fuse] of w.b || []) bubbles.set(id, {id, r, c, p, owner, fuse: Math.max(0.3, fuse), born: t - (FUSE - Math.max(0.3, fuse)), passMe: false});
  }

  // ---------------------------------------------------------------- 开房间的设备：泡泡爆炸、砖块、怪兽
  function hostStep(dt) {
    for (const b of [...bubbles.values()]) if ((b.fuse -= dt) <= 0) explode(b.id);
    for (const m of mons) {
      if (!m.alive) continue;
      monMove(m, dt);
      if (flames.some(f => f.r === cellOf(m.y) && f.c === cellOf(m.x))) {
        m.alive = false; Play.sfx.play('boom');
        fx.burst(m.x, m.y, {n: 20, colors: ['#b8f2a6', '#fff', '#ffd23f'], speed: 260, shape: 'star', size: 6});
        fx.text(m.x, m.y - 20, '打败！', {size: 20, color: '#ff7a00'});
      }
    }
    snapT -= dt;
    if (snapT <= 0) { snapT = 0.1; mp.net.send({t: 'w', m: mons.map(m => [m.id, Math.round(m.x), Math.round(m.y), m.alive ? 1 : 0])}); }
    if (!mons.some(m => m.alive) && !ended) {
      winT += dt;
      if (winT > 1) { ended = true; mp.finish({score: mons.length}); }
    }
  }
  function explode(id) {
    const b = bubbles.get(id); if (!b) return;
    bubbles.delete(id);
    const cells = [[b.r, b.c]], blocks = [];
    for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      for (let i = 1; i <= b.p; i++) {
        const r = b.r + dr * i, c = b.c + dc * i;
        if (g[r][c] === '#') break;
        cells.push([r, c]);
        if (g[r][c] === '+') {
          if (!blocks.some(x => x[0] === r && x[1] === c)) {
            const roll = rng();
            blocks.push([r, c, roll < 0.12 ? 'fire' : roll < 0.24 ? 'bub' : roll < 0.33 ? 'shoe' : 0]);
          }
          break;
        }
        const nb = bubbleAt(r, c);
        if (nb) { nb.fuse = Math.min(nb.fuse, 0.08); break; }   // 连环爆炸
      }
    }
    const msg = {t: 'boom', id, cells, blocks};
    applyBoom(msg);
    mp.net.send(msg);
  }
  function applyBoom(m) {
    bubbles.delete(m.id);
    for (const [r, c] of m.cells) flames.push({r, c, t: FLAME, max: FLAME});
    for (const [r, c, it] of m.blocks) {
      g[r][c] = '.';
      fx.burst(ctr(c), ctr(r), {n: 8, colors: [SCENES[scene].box, SCENES[scene].boxHi], speed: 160, size: 4, shape: 'confetti', grav: 300});
      if (it) items.set(key(r, c), it);
    }
    Play.sfx.play('whoosh');
  }
  function monMove(m, dt) {
    const ghost = MON[m.kind].ghost, s = m.speed * dt;
    const r = cellOf(m.y), c = cellOf(m.x), cx = ctr(c), cy = ctr(r), k = r * COLS + c;
    const near = Math.abs(m.x - cx) <= s + 0.01 && Math.abs(m.y - cy) <= s + 0.01;
    const stuck = !m.dir[0] && !m.dir[1];
    if (near && (m.decided !== k || stuck || blocked(r + m.dir[1], c + m.dir[0], ghost))) {   // 走到格子中间：选下一步往哪走
      m.x = cx; m.y = cy; m.decided = k;
      const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]].filter(([dx, dy]) => !blocked(r + dy, c + dx, ghost));
      const fwd = dirs.filter(d => !(d[0] === -m.dir[0] && d[1] === -m.dir[1]));
      const pool = fwd.length ? fwd : dirs, straight = pool.find(d => d[0] === m.dir[0] && d[1] === m.dir[1]);
      const near2 = nearestPlayer(m);
      if (!pool.length) m.dir = [0, 0];
      else if (near2 && rng() < (m.kind === 'b' ? 0.5 : 0.3)) m.dir = pool.slice().sort((a, b) => dist(c + a[0], r + a[1], near2) - dist(c + b[0], r + b[1], near2))[0];
      else m.dir = straight && rng() < 0.7 ? straight : pool[Math.floor(rng() * pool.length)];
    } else if (!near) {   // 半路上前面突然有了泡泡：掉头
      const ar = cellOf(m.y + m.dir[1] * T / 2), ac = cellOf(m.x + m.dir[0] * T / 2);
      if ((ar !== r || ac !== c) && blocked(ar, ac, ghost)) m.dir = [-m.dir[0], -m.dir[1]];
    }
    m.x += m.dir[0] * s; m.y += m.dir[1] * s;
  }
  const dist = (c, r, p) => Math.abs(ctr(c) - p.x) + Math.abs(ctr(r) - p.y);
  function nearestPlayer(m) {
    const ps = [];
    if (me && !me.trapped) ps.push(me);
    if (peerRaw && !peerRaw.d) ps.push(peerRaw);
    ps.sort((a, b) => Math.hypot(a.x - m.x, a.y - m.y) - Math.hypot(b.x - m.x, b.y - m.y));
    return ps[0] && Math.hypot(ps[0].x - m.x, ps[0].y - m.y) < T * 5 ? ps[0] : null;
  }

  // ---------------------------------------------------------------- 画
  function view() { const st = sh.stage, k = Math.min(st.W / MW, st.H / MH); return {k, ox: (st.W - MW * k) / 2, oy: (st.H - MH * k) / 2}; }
  function render() {
    const st = sh.stage, c = st.ctx, sc = SCENES[scene] || SCENES.candy;
    st.begin();
    c.fillStyle = sc.wallLo; c.fillRect(0, 0, st.W, st.H);
    const v = view(), [sx, sy] = fx.offset();
    c.save(); c.translate(v.ox + sx, v.oy + sy); c.scale(v.k, v.k);
    for (let r = 0; r < ROWS; r++) for (let q = 0; q < COLS; q++) { c.fillStyle = sc.floor[(r + q) % 2]; c.fillRect(q * T, r * T, T, T); }
    for (const [k, it] of items) { const [r, q] = k.split(',').map(Number); drawItem(c, q * T + T / 2, r * T + T / 2, it); }
    for (const f of flames) drawFlame(c, f);
    for (const b of bubbles.values()) drawBubble(c, b);
    for (let r = 0; r < ROWS; r++) for (let q = 0; q < COLS; q++) {
      const ch = g[r][q], x = q * T, y = r * T;
      if (ch === '#') {
        c.fillStyle = sc.wallLo; Play.roundRect(c, x + 1, y + 3, T - 2, T - 3, 8); c.fill();
        c.fillStyle = sc.wall; Play.roundRect(c, x + 1, y, T - 2, T - 5, 8); c.fill();
        c.fillStyle = sc.wallHi; Play.roundRect(c, x + 6, y + 4, T - 18, 6, 3); c.fill();
      } else if (ch === '+') {
        c.fillStyle = sc.box; Play.roundRect(c, x + 3, y + 3, T - 6, T - 6, 5); c.fill();
        c.fillStyle = sc.boxHi; c.fillRect(x + 6, y + 6, T - 12, 4);
        c.fillStyle = sc.ribbon; c.fillRect(x + T / 2 - 3, y + 3, 6, T - 6); c.fillRect(x + 3, y + T / 2 - 3, T - 6, 6);
      }
    }
    // 人和怪兽按上下排序画
    const list = [];
    for (const m of mons) if (m.alive) list.push({y: m.y, draw: () => Art.monster(c, m.x, m.y + 14, {kind: MON[m.kind].kind, t: t + m.id, scale: 0.5, facing: m.dir[0] > 0 ? 1 : -1})});
    if (peerTrack && mp && mp.peer) { const p = peerTrack.get(['x', 'y']); if (p) list.push({y: p.y, draw: () => drawPlayer(c, p.x, p.y, mp.peer, p.f || 1, p.mv, p.d, false)}); }
    if (me && mp) list.push({y: me.y, draw: () => drawPlayer(c, me.x, me.y, mp.me, me.face, me.mv, me.trapped, true)});
    list.sort((a, b) => a.y - b.y).forEach(o => o.draw());
    fx.draw(c);
    c.restore();
  }
  function drawItem(c, x, y, it) {
    c.fillStyle = 'rgba(255,255,255,.85)'; c.beginPath(); c.arc(x, y + Math.sin(t * 4) * 2, 14, 0, 7); c.fill();
    c.font = '18px system-ui'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(ITEMS[it], x, y + 1 + Math.sin(t * 4) * 2);
  }
  function drawBubble(c, b) {
    const x = ctr(b.c), y = ctr(b.r), owner = mp && (b.owner === mp.me.id ? mp.me : mp.peer);
    const col = owner ? ROLE_COLOR[owner.role] : '#7bdff2';
    const left = mp && mp.host ? b.fuse : FUSE - (t - (b.born || t));
    const s = 15 + Math.sin(t * (left < 0.8 ? 18 : 6)) * 1.6;
    c.globalAlpha = 0.75; c.fillStyle = col; c.beginPath(); c.arc(x, y, s, 0, 7); c.fill(); c.globalAlpha = 1;
    c.strokeStyle = 'rgba(255,255,255,.9)'; c.lineWidth = 2; c.beginPath(); c.arc(x, y, s, 0, 7); c.stroke();
    c.fillStyle = 'rgba(255,255,255,.85)'; c.beginPath(); c.ellipse(x - 5, y - 6, 4, 6, -0.6, 0, 7); c.fill();
  }
  function drawFlame(c, f) {
    const x = f.c * T, y = f.r * T, a = Math.min(1, f.t / f.max * 1.6);
    c.globalAlpha = a; c.fillStyle = '#5ad1ff'; Play.roundRect(c, x + 2, y + 2, T - 4, T - 4, 10); c.fill();
    c.fillStyle = '#e6fbff'; Play.roundRect(c, x + 9, y + 9, T - 18, T - 18, 6); c.fill(); c.globalAlpha = 1;
  }
  function drawPlayer(c, x, y, m, face, mv, trapped, mine) {
    c.fillStyle = ROLE_COLOR[m.role] || '#999'; c.globalAlpha = 0.55; c.beginPath(); c.ellipse(x, y + 15, 15, 5, 0, 0, 7); c.fill(); c.globalAlpha = 1;
    Art.hero(c, x, y + 15, {avatar: m.avatar, pose: trapped ? 'hurt' : mv ? 'run' : 'idle', t: t * (mv ? 1.6 : 1), scale: 0.26, facing: face});
    if (trapped) {
      c.globalAlpha = 0.45; c.fillStyle = '#bfefff'; c.beginPath(); c.arc(x, y - 4, 24, 0, 7); c.fill(); c.globalAlpha = 1;
      c.strokeStyle = '#fff'; c.lineWidth = 2.5; c.beginPath(); c.arc(x, y - 4, 24 + Math.sin(t * 5), 0, 7); c.stroke();
      c.fillStyle = '#2b2340'; c.font = 'bold 14px system-ui'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('?', x + 18, y - 26);
    }
    if (me && mine && me.inv > 0 && Math.floor(t * 10) % 2) { c.globalAlpha = 0.4; c.fillStyle = '#fff'; c.beginPath(); c.arc(x, y - 4, 18, 0, 7); c.fill(); c.globalAlpha = 1; }
    c.font = '800 10px "PingFang SC", system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
    const w = c.measureText(m.name).width + 10, ny = y - 36;
    c.fillStyle = mine ? 'rgba(255,255,255,.92)' : 'rgba(43,35,64,.78)'; Play.roundRect(c, x - w / 2, ny - 7, w, 14, 7); c.fill();
    c.fillStyle = mine ? '#2b2340' : '#fff'; c.fillText(m.name, x, ny);
  }

  // ---------------------------------------------------------------- 外壳
  let starts = null;
  MpShell.init({
    id: 'bubble', tempo: 132,
    rules: ['两个人一队，在格子地图上<b>放泡泡</b>：泡泡过一会儿炸成十字，炸开砖块、打败怪兽。',
            '砖块里会掉道具：🔥 炸得更远、🫧 多放一个泡泡、👟 跑得更快。',
            '被泡泡炸到、被怪兽碰到就会<b>困在泡泡里</b>：队友跑过来碰一下马上救出来！',
            '没人救就答题自救：答对出来；答错多欠一道（最多欠 3 道）；「这题不会」不加。',
            '打败所有怪兽就过关；在目标时间内过关、被困答题不超过 2 次，还能多拿星星。'],
    keys: '电脑键盘：方向键 或 W A S D 走，空格 放泡泡',
    input: {up: ['ArrowUp', 'w'], down: ['ArrowDown', 's'], left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], bomb: [' ', 'j', 'Enter']},
    touch: [['left', '◀'], ['up', '▲'], ['down', '▼'], ['right', '▶'], ['bomb', '🫧', 'big']], touchSplit: 4,
    preview(s, lv) {
      sh = s; fx = fx || new Play.FX();
      if (!ended) return;
      starts = build(lv); me = null; peerTrack = null; mp = null;
      render();
    },
    begin(ctx) {
      mp = ctx; sh = ctx.sh; fx = new Play.FX(); t = 0; clockT = 0; snapT = 0; winT = 0; ended = false; bubN = 0; lastDir = null;
      rng = MpNet.rng(ctx.seed);
      starts = build(ctx.level);
      const mine = starts[ctx.roleIndex(ctx.me.role) === 0 ? '1' : '2'], other = starts[mine === starts['1'] ? '2' : '1'];
      me = {x: ctr(mine.c), y: ctr(mine.r), face: 1, mv: false, trapped: false, inv: 0, power: 1, max: 1, shoe: 0};
      peerTrack = new MpNet.Track(100); peerRaw = {x: ctr(other.c), y: ctr(other.r), f: 1}; peerTrack.push(Object.assign({}, peerRaw));
      const rs = ctx.resume;
      if (rs && rs.world) loadWorld(rs.world);
      if (rs && rs.me) Object.assign(me, pick(rs.me, ['x', 'y', 'face', 'power', 'max', 'shoe']), {trapped: !!rs.me.trapped, inv: rs.me.trapped ? 0 : 2});
      if (ctx.level.tip && !rs) mp.toast(ctx.level.tip.split('。')[0], 'good');
      const net = ctx.net;
      net.off('s', 'bub', 'boom', 'w', 'pick', 'save');
      net.on('s', m => { peerRaw = m; peerTrack.push(m); });
      net.on('bub', m => {
        if (bubbles.has(m.id)) return;
        const b = {id: m.id, r: m.r, c: m.c, p: Math.max(1, Math.min(6, m.p | 0)), owner: m.from, fuse: FUSE, born: t, passMe: me && overlapsCell(m.r, m.c)};
        bubbles.set(b.id, b);
      });
      net.on('boom', m => { if (!mp.host) applyBoom(m); });
      net.on('w', m => {
        if (mp.host) return;
        for (const [id, x, y, alive] of m.m) {
          const mo = mons.find(o => o.id === id); if (!mo) continue;
          if (mo.alive && !alive) { fx.burst(mo.x, mo.y, {n: 20, colors: ['#b8f2a6', '#fff', '#ffd23f'], speed: 260, shape: 'star', size: 6}); Play.sfx.play('boom'); }
          mo.alive = !!alive;
          if (!monTracks.has(id)) monTracks.set(id, new MpNet.Track(100));
          monTracks.get(id).push({x, y});
          mo.dir = [Math.sign(x - mo.x), 0];
        }
      });
      net.on('pick', m => { items.delete(m.k); });
      net.on('save', m => {
        if (m.to !== mp.me.id || !me.trapped) return;
        me.trapped = false; me.inv = 2;
        mp.toast(`${mp.peer.name}救了你！`, 'good'); Play.sfx.play('power');
        mp.rescue();
      });
      net.stream(() => me && !ended ? {t: 's', x: Math.round(me.x), y: Math.round(me.y), f: me.face, mv: me.mv ? 1 : 0, d: me.trapped ? 1 : 0} : null, 15);
      if (loop) loop.stop();
      loop = Play.loop(step, render);
      loop.start();
    },
    onRevive(info) {
      if (!me) return;
      me.trapped = false; me.inv = 2;
      if (!info.rescued) { fx.ring(me.x, me.y, {color: '#fff', width: 4, grow: 300, life: 0.4}); Play.sfx.play('power'); }
    },
    end() { ended = true; if (loop) loop.stop(); if (mp) mp.net.stopStreams(); },
    save: () => me && {x: Math.round(me.x), y: Math.round(me.y), face: me.face, power: me.power, max: me.max, shoe: me.shoe, trapped: me.trapped ? 1 : 0},
    saveWorld: () => g && {g: g.map(r => r.join('')), it: [...items], m: mons.map(m => [m.id, Math.round(m.x), Math.round(m.y), m.alive ? 1 : 0]),
                           b: [...bubbles.values()].map(b => [b.id, b.r, b.c, b.p, b.owner, +(mp && mp.host ? b.fuse : FUSE - (t - (b.born || t))).toFixed(2)])},
    loadWorld: w => loadWorld(w),
    peerView: () => peerRaw && {x: Math.round(peerRaw.x), y: Math.round(peerRaw.y)},
    loadMe(v) { if (me && v && !me.trapped && boxFree(v.x, v.y)) { me.x = v.x; me.y = v.y; } },
  });
})();

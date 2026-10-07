/* 火柴人大战（单机打电脑）。玩法在这里，出题、经验值、冷冻在服务器（app/arena.py），答题面板是 arena.js 的 ArenaQuiz。
   - 能量 = 经验值：走路、跳、出拳都耗能；能量快没了提醒，没了就打不动、走得慢，得去答题。
   - 答题时火柴人进入「充能」姿势：不能动，受到的伤害减半。
   - 用能量换武器：木棍 / 护盾 / 剑 / 激光；连对 3 题攒一次必杀。
   - 电脑也会「答题」：它的答对率和你的目标答对率一样（75%），谁赢看专注和操作。 */
(function () {
  const H = 460, GROUND = 400, GRAV = 2000;
  let W = 960;   // 场地宽度跟着屏幕比例变（高度固定），手机横屏也铺满
  const START_ENERGY = 300, LOW = 60, MAX_ENERGY = 2000;
  const COST = {move: 3, jump: 6, punch: 8};
  const WEAPONS = {
    fist: {name: '拳头', dmg: 5, range: 58, cd: 0.42, cost: 8},
    stick: {name: '木棍', price: 200, dmg: 9, range: 78, cd: 0.5, cost: 8, icon: '🪵'},
    sword: {name: '剑', price: 500, dmg: 14, range: 96, cd: 0.55, cost: 10, icon: '🗡️'},
    laser: {name: '激光', price: 1000, dmg: 12, range: 900, cd: 0.9, cost: 15, icon: '🔫', ranged: true},
  };
  const SHIELD_PRICE = 300, SHIELD_HP = 40;
  const SHOP = [['stick', '🪵 木棍', 200], ['shield', '🛡️ 护盾', 300], ['sword', '🗡️ 剑', 500], ['laser', '🔫 激光', 1000]];

  const root = document.getElementById('sm');
  const cv = root.querySelector('canvas'), ctx = cv.getContext('2d');
  const $q = s => root.querySelector(s);
  let match = null, quiz = null, raf = 0, last = 0, over = true;
  let P, A, fx, keys = {}, tLeft = 180, matchLen = 180, warnedLow = false, zeroTime = 0, specials = 0, bought = [];

  function fit() {
    const r = cv.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const w = Math.max(640, Math.min(1400, Math.round(H * r.width / r.height)));
    if (w !== W) {
      const k = w / W; W = w; cv.width = W;
      if (P) { P.x *= k; A.x *= k; }
    }
  }
  window.addEventListener('resize', () => { fit(); if (over) draw(); });

  function fighter(x, color, facing) {
    return {x, y: GROUND, vx: 0, vy: 0, facing, color, hp: 100, energy: START_ENERGY, weapon: 'fist', shield: 0,
            cd: 0, atk: 0, hurt: 0, charge: false, sp: 0, walk: 0, onGround: true};
  }
  function reset() {
    P = fighter(W * 0.23, getComputedStyle(document.body).getPropertyValue('--brand').trim() || '#2f6f5e', 1);
    A = fighter(W * 0.77, '#e0573a', -1);
    A.ai = {think: 0, answering: 0, aggr: 0.6};
    fx = []; warnedLow = false; zeroTime = 0; specials = 0; bought = [];
    tLeft = matchLen;
  }

  // ---------------------------------------------------------------- 动作
  function spend(f, n) {
    if (f.energy <= 0) return false;
    f.energy = Math.max(0, f.energy - n);
    return true;
  }
  function attack(f, o) {
    if (f.cd > 0 || f.charge || f.hurt > 0.25) return;
    const w = WEAPONS[f.weapon];
    if (f.energy <= 0) { if (f === P) say('能量用完了，打不动！快去答题', 'warn'); return; }
    spend(f, w.cost);
    f.cd = w.cd; f.atk = 0.18;
    if (w.ranged) { fx.push({kind: 'beam', x: f.x + f.facing * 30, y: f.y - 62, vx: 1100 * f.facing, owner: f, dmg: w.dmg, life: 1}); return; }
    const dx = (o.x - f.x) * f.facing;
    if (dx > 0 && dx < w.range && Math.abs(o.y - f.y) < 70) hit(o, w.dmg, f.facing);
  }
  function special(f, o) {
    if (f.sp <= 0 || f.charge) return;
    f.sp--; f.atk = 0.4;
    fx.push({kind: 'wave', x: f.x, y: f.y - 40, r: 10, life: 0.5, color: f.color});
    if (Math.abs(o.x - f.x) < 340) hit(o, 25, Math.sign(o.x - f.x) || f.facing, 520);
    if (f === P) { specials++; say('⚡ 必杀！', 'good'); }
  }
  function hit(o, dmg, dir, kb) {
    let d = dmg;
    if (o.charge) d = Math.ceil(d / 2);           // 答题时伤害减半
    if (o.shield > 0) { const s = Math.min(o.shield, d); o.shield -= s; d -= s; }
    o.hp = Math.max(0, o.hp - d);
    o.vx = dir * (kb || 260); o.hurt = 0.3;
    fx.push({kind: 'num', x: o.x, y: o.y - 110, text: d ? '-' + d : '🛡️', life: 0.8, color: '#e0573a'});
  }
  function buy(f, what) {
    const price = what === 'shield' ? SHIELD_PRICE : WEAPONS[what].price;
    if (f.energy < price) { if (f === P) say(`还差 ${price - Math.floor(f.energy)} 能量，去答题攒吧`, 'warn'); return false; }
    if (what !== 'shield' && f.weapon === what) return false;
    f.energy -= price;
    if (what === 'shield') f.shield = SHIELD_HP; else f.weapon = what;
    if (f === P) { bought.push(what); say(`换上了${what === 'shield' ? '护盾' : WEAPONS[what].name}！`, 'good'); }
    return true;
  }

  // ---------------------------------------------------------------- 电脑
  function aiStep(dt) {
    const ai = A.ai;
    if (ai.answering > 0) {                        // 电脑在答题：和孩子一样的目标答对率
      ai.answering -= dt;
      if (ai.answering <= 0) {
        A.charge = false;
        if (Math.random() < 0.75) { A.energy += 70 + Math.random() * 80; ai.streak = (ai.streak || 0) + 1; if (ai.streak % 3 === 0) A.sp++; }
        else { ai.streak = 0; ai.answering = -5; }   // 答错：冷冻 5 秒（负数表示冷冻）
      }
      return;
    }
    if (ai.answering < 0) ai.answering = Math.min(0, ai.answering + dt);
    // 落后补偿：电脑领先太多就收着点打，孩子领先就更积极
    const lead = A.hp - P.hp;
    ai.aggr = lead > 30 ? 0.35 : lead < -30 ? 0.85 : 0.6;
    ai.think -= dt;
    if (A.energy < LOW && ai.answering === 0 && Math.random() < dt * 1.5) { A.charge = true; ai.answering = 4 + Math.random() * 3; return; }
    if (A.energy > 700 && A.weapon === 'fist') buy(A, 'stick');
    else if (A.energy > 900 && A.weapon === 'stick') buy(A, 'sword');
    else if (A.energy > 500 && !A.shield && P.weapon !== 'fist') buy(A, 'shield');
    if (A.sp > 0 && Math.abs(P.x - A.x) < 300 && Math.random() < dt * 2) special(A, P);
    const w = WEAPONS[A.weapon], dist = P.x - A.x;
    A.facing = Math.sign(dist) || A.facing;
    if (ai.think <= 0) { ai.think = 0.15 + Math.random() * 0.25; ai.move = Math.abs(dist) > w.range * 0.8 ? Math.sign(dist) : (Math.random() < 0.25 ? -Math.sign(dist) : 0); }
    move(A, ai.move || 0, dt, ai.aggr);
    if (Math.abs(dist) < w.range && Math.random() < dt * 3 * ai.aggr) attack(A, P);
    if (P.atk > 0 && Math.random() < dt * 2 && A.onGround) jump(A);
  }

  function move(f, dir, dt, speedK) {
    if (f.charge || !dir) { f.walk = 0; return; }
    let sp = 260 * (speedK ? 0.75 + speedK * 0.4 : 1);
    if (f.energy <= 0) sp *= 0.4; else spend(f, COST.move * dt);
    f.x += dir * sp * dt; f.facing = dir; f.walk += dt * 10;
  }
  function jump(f) {
    if (!f.onGround || f.charge) return;
    if (f.energy <= 0) return;
    spend(f, COST.jump); f.vy = -720; f.onGround = false;
  }

  // ---------------------------------------------------------------- 主循环
  function step(dt) {
    tLeft -= dt;
    if (!P.charge) {
      const dir = (keys.right ? 1 : 0) - (keys.left ? 1 : 0);
      move(P, dir, dt);
      if (keys.jump) { jump(P); keys.jump = false; }
      if (keys.attack) { attack(P, A); keys.attack = false; }
    }
    aiStep(dt);
    for (const f of [P, A]) {
      f.cd = Math.max(0, f.cd - dt); f.atk = Math.max(0, f.atk - dt); f.hurt = Math.max(0, f.hurt - dt);
      f.vy += GRAV * dt; f.y += f.vy * dt; f.x += f.vx * dt; f.vx *= Math.pow(0.02, dt);
      if (f.y >= GROUND) { f.y = GROUND; f.vy = 0; f.onGround = true; }
      f.x = Math.max(40, Math.min(W - 40, f.x));
      f.energy = Math.min(MAX_ENERGY, f.energy);
    }
    const gap = A.x - P.x, MIN = 34;   // 两个人不叠在一起
    if (Math.abs(gap) < MIN && Math.abs(P.y - A.y) < 60) {
      const push = (MIN - Math.abs(gap)) / 2 * (Math.sign(gap) || 1);
      P.x -= push; A.x += push;
      P.x = Math.max(40, Math.min(W - 40, P.x)); A.x = Math.max(40, Math.min(W - 40, A.x));
    }
    if (P.energy <= 0) zeroTime += dt;
    if (P.energy < LOW && P.energy > 0 && !warnedLow) { warnedLow = true; say('能量快没了！点「答题」补充', 'warn'); }
    if (P.energy >= LOW) warnedLow = false;
    for (const e of fx) {
      e.life -= dt;
      if (e.kind === 'beam') {
        e.x += e.vx * dt;
        const o = e.owner === P ? A : P;
        if (Math.abs(e.x - o.x) < 24 && e.life > 0) { hit(o, e.dmg, Math.sign(e.vx), 160); e.life = 0; }
      } else if (e.kind === 'num') e.y -= 50 * dt;
      else if (e.kind === 'wave') e.r += 700 * dt;
    }
    fx = fx.filter(e => e.life > 0 && e.x > -50 && e.x < W + 50);
    if (P.hp <= 0 || A.hp <= 0 || tLeft <= 0) finish();
  }

  function draw() {
    const css = getComputedStyle(document.body);
    const ink = css.getPropertyValue('--ink').trim() || '#1f2328', card = css.getPropertyValue('--card').trim() || '#fff';
    const line = css.getPropertyValue('--line').trim() || '#e7e3d9';
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = card; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = line; ctx.fillRect(0, GROUND + 6, W, H - GROUND);
    for (const f of [P, A]) drawFighter(f, ink);
    for (const e of fx) {
      ctx.globalAlpha = Math.max(0, Math.min(1, e.life * 2));
      if (e.kind === 'beam') { ctx.strokeStyle = '#2f6fdc'; ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(e.x - 30 * Math.sign(e.vx), e.y); ctx.lineTo(e.x, e.y); ctx.stroke(); }
      else if (e.kind === 'num') { ctx.fillStyle = e.color; ctx.font = 'bold 26px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(e.text, e.x, e.y); }
      else if (e.kind === 'wave') { ctx.strokeStyle = e.color; ctx.lineWidth = 6; ctx.beginPath(); ctx.arc(e.x, e.y, e.r, 0, Math.PI * 2); ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
  }

  function drawFighter(f, ink) {
    const x = f.x, y = f.y, s = f.facing, t = f.walk;
    ctx.save();
    if (f.charge) {   // 充能光环
      ctx.fillStyle = 'rgba(245,179,1,.22)'; ctx.beginPath(); ctx.ellipse(x, y - 55, 52, 72, 0, 0, Math.PI * 2); ctx.fill();
    }
    if (f.shield > 0) { ctx.strokeStyle = 'rgba(47,111,220,.55)'; ctx.lineWidth = 4; ctx.beginPath(); ctx.arc(x, y - 55, 62, 0, Math.PI * 2); ctx.stroke(); }
    ctx.globalAlpha = f.hurt > 0 ? 0.45 : 1; ctx.strokeStyle = f.color; ctx.fillStyle = f.color;
    ctx.lineWidth = 7; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const hipY = y - 40, neckY = y - 92;
    ctx.beginPath(); ctx.arc(x, neckY - 18, 15, 0, Math.PI * 2); ctx.fill();          // 头
    ctx.beginPath(); ctx.moveTo(x, neckY); ctx.lineTo(x, hipY); ctx.stroke();          // 身体
    const leg = f.onGround ? Math.sin(t) * 16 : 10;
    ctx.beginPath(); ctx.moveTo(x, hipY); ctx.lineTo(x + leg, y); ctx.moveTo(x, hipY); ctx.lineTo(x - leg, y); ctx.stroke();
    const sh = neckY + 12;
    let hx, hy;
    if (f.charge) { hx = x + s * 6; hy = sh - 36; }
    else if (f.atk > 0) { hx = x + s * 46; hy = sh + 2; }
    else { hx = x + s * 22; hy = sh + 26 + Math.sin(t) * 4; }
    ctx.beginPath(); ctx.moveTo(x, sh); ctx.lineTo(hx, hy); ctx.moveTo(x, sh); ctx.lineTo(x - s * 18, sh + 30); ctx.stroke();
    const w = f.weapon;
    if (w !== 'fist' && !f.charge) {
      ctx.lineWidth = w === 'sword' ? 5 : 6;
      ctx.strokeStyle = w === 'stick' ? '#a16207' : w === 'sword' ? '#8b9098' : '#2f6fdc';
      const len = w === 'stick' ? 42 : w === 'sword' ? 58 : 26;
      ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(hx + s * len, hy - (f.atk > 0 ? 0 : 18)); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    if (f.charge) { ctx.fillStyle = ink; ctx.font = '22px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(f === P ? '✍️' : '🤔', x, neckY - 44); }
    if (f === A && A.ai.answering < 0) { ctx.font = '22px sans-serif'; ctx.textAlign = 'center'; ctx.fillText('❄️', x, neckY - 44); }
    ctx.restore();
  }

  // ---------------------------------------------------------------- 界面
  let sayTimer = 0;
  function say(text, kind) {
    const el = $q('.sm-say'); el.textContent = text; el.className = 'sm-say on ' + (kind || '');
    clearTimeout(sayTimer); sayTimer = setTimeout(() => el.className = 'sm-say', 1800);
  }
  const hudEl = {php: $q('.sm-bar.hp.me i'), ahp: $q('.sm-bar.hp.ai i'), pen: $q('.sm-bar.en i'), pe: $q('.sm-pe'), ae: $q('.sm-ae'),
                 time: $q('.sm-time'), psp: $q('.sm-psp'), en: $q('.sm-en')};
  function syncHud() {
    hudEl.php.style.width = P.hp + '%'; hudEl.ahp.style.width = A.hp + '%';
    hudEl.pen.style.width = Math.min(100, P.energy / 10) + '%';
    hudEl.pe.textContent = Math.floor(P.energy); hudEl.ae.textContent = Math.floor(A.energy);
    hudEl.en.classList.toggle('low', P.energy < LOW);
    const s = Math.max(0, Math.ceil(tLeft));
    hudEl.time.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    hudEl.time.classList.toggle('low', s <= 20);
    hudEl.psp.textContent = P.sp > 0 ? `必杀 ×${P.sp}` : '';
  }
  function syncUI() {
    syncHud();
    const qb = $q('.sm-quiz');
    qb.classList.toggle('pulse', P.energy < LOW);
    qb.classList.toggle('empty', P.energy <= 0);
    $q('.sm-sp').disabled = P.sp <= 0;
    $q('.sm-sp').textContent = P.sp > 0 ? `必杀 ×${P.sp}` : '必杀';
    root.querySelectorAll('.sm-shop button').forEach(b => {
      const w = b.dataset.buy, price = +b.dataset.price;
      b.disabled = P.energy < price || (w !== 'shield' && P.weapon === w) || P.charge;
    });
  }

  function loop(ts) {
    const dt = Math.min(0.05, (ts - last) / 1000 || 0); last = ts;
    if (!over) { step(dt); syncUI(); }
    draw();
    if (!over) raf = requestAnimationFrame(loop);
  }

  async function begin() {
    $q('.sm-start').hidden = true; $q('.sm-end').hidden = true;
    try {
      match = await api('/api/arena/start', {game: 'stickman'});
    } catch (e) { $q('.sm-end').hidden = false; $q('.sm-end .sm-sum').innerHTML = `<p class="err">${esc(e.message)}</p>`; $q('.sm-again').hidden = true; return; }
    matchLen = Math.max(30, Math.min(180, match.seconds_left));
    reset();
    if (quiz) quiz.el.remove();
    quiz = new ArenaQuiz({
      matchId: match.match_id,
      onOpen: () => { P.charge = true; keys = {}; },
      onClose: () => { P.charge = false; },
      onXP: r => {
        P.energy += r.xp;
        if (r.special) P.sp++;
        fx.push({kind: 'num', x: P.x, y: P.y - 130, text: `+${r.xp}`, life: 1.1, color: '#d97706'});
      },
    });
    over = false; last = performance.now(); raf = requestAnimationFrame(loop);
    say('开打！能量快没了就点左下角答题', 'good');
  }

  async function finish() {
    if (over) return;
    over = true; cancelAnimationFrame(raf);
    if (quiz) quiz.close();
    const result = P.hp === A.hp ? 'draw' : (P.hp > A.hp ? 'win' : 'lose');
    draw();
    let r;
    try {
      r = await api(`/api/arena/${match.match_id}/end`, {result, stats: {zero_energy_s: Math.round(zeroTime), specials,
        weapons: bought, hp_left: Math.round(P.hp), ai_hp_left: Math.round(A.hp)}});
    } catch (e) { r = {result, answered: 0, right: 0, accuracy: 0, focus: null, xp: 0, tips: [e.message], seconds_left: 0}; }
    const title = {win: '🏆 你赢了！', lose: '💪 差一点！再来一局', draw: '🤝 平局'}[result];
    $q('.sm-end .sm-sum').innerHTML = `<h2>${title}</h2>
      <div class="statline"><div><b>${r.focus ?? '—'}</b><span>专注指数</span></div><div><b>${r.right}/${r.answered}</b><span>答对 / 答题</span></div><div><b>+${r.xp}</b><span>经验值</span></div></div>
      ${r.tips.length ? `<ul class="small">${r.tips.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
      <p class="muted small">今天还能玩 ${Math.floor((r.seconds_left || 0) / 60)} 分钟。</p>`;
    $q('.sm-again').hidden = (r.seconds_left || 0) < 30;
    $q('.sm-end').hidden = false;
  }

  // ---------------------------------------------------------------- 输入
  const KEYMAP = {ArrowLeft: 'left', a: 'left', A: 'left', ArrowRight: 'right', d: 'right', D: 'right'};
  window.addEventListener('keydown', e => {
    if (over || (quiz && quiz.isOpen)) return;
    if (KEYMAP[e.key]) { keys[KEYMAP[e.key]] = true; e.preventDefault(); }
    else if (['ArrowUp', 'w', 'W', ' '].includes(e.key)) { keys.jump = true; e.preventDefault(); }
    else if (['j', 'J', 'k', 'K'].includes(e.key)) keys.attack = true;
    else if (['l', 'L'].includes(e.key)) special(P, A);
    else if (['q', 'Q', 'Enter'].includes(e.key)) { e.preventDefault(); quiz.open(); }
    else if (['1', '2', '3', '4'].includes(e.key)) { const s = SHOP[+e.key - 1]; buy(P, s[0]); }
  });
  window.addEventListener('keyup', e => { if (KEYMAP[e.key]) keys[KEYMAP[e.key]] = false; });
  function hold(sel, key) {
    const b = $q(sel);
    const on = e => { e.preventDefault(); if (!over && !(quiz && quiz.isOpen)) keys[key] = true; };
    const off = e => { e.preventDefault(); keys[key] = false; };
    b.addEventListener('pointerdown', on); b.addEventListener('pointerup', off); b.addEventListener('pointerleave', off); b.addEventListener('pointercancel', off);
  }
  hold('.sm-left', 'left'); hold('.sm-right', 'right');
  $q('.sm-jump').addEventListener('pointerdown', e => { e.preventDefault(); if (!over) keys.jump = true; });
  $q('.sm-hit').addEventListener('pointerdown', e => { e.preventDefault(); if (!over) keys.attack = true; });
  $q('.sm-sp').onclick = () => { if (!over) special(P, A); };
  $q('.sm-quiz').onclick = () => { if (!over && quiz) quiz.open(); };
  root.querySelectorAll('.sm-shop button').forEach(b => b.onclick = () => { if (!over) buy(P, b.dataset.buy); });
  $q('.sm-go').onclick = begin;
  $q('.sm-again').onclick = begin;

  fit(); reset(); syncHud(); draw();
})();

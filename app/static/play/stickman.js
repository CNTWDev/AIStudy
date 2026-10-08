/* 火柴人大战（单机打电脑，闯关）。玩法在这里；出题、经验值、冷冻在服务器，答题面板是 quiz.js，外壳是 shell.js，
   关卡参数在 app/arena/levels.py（开局时放在 match.stage.params 里）。
   - 能量只从答题来：开局只给一点点，不会自己回（前两关会慢慢回一点，先学会玩）；走、跳、出拳都要花。
   - 答题时整个游戏暂停（时间停止），答完再接着打；答错冷冻照旧。
   - 用能量换装备：木棍 / 护盾 / 剑 / 激光，后三样要打通前面的世界才解锁；连对 3 题攒一次必杀。
   - 越往后电脑血越厚、越快、越凶、会格挡；每个世界第 6 关是大怪兽，带护盾：答对几题才能破盾，破盾后几秒内狠狠打。
   - 电脑也会「答题」（答题时不能动），答对率和你一样；谁赢看专注和操作。 */
(function () {
  const H = 450, GROUND = 392, GRAV = 2100;
  const LOW = 45, MAX_ENERGY = 2000, REGEN_CAP = 100;
  const COST = {move: 2, jump: 5};
  const WEAPONS = {
    fist: {name: '拳头', dmg: 5, range: 62, cd: 0.36, cost: 15},
    stick: {name: '木棍', price: 200, dmg: 8, range: 86, cd: 0.42, cost: 15, icon: '🪵'},
    sword: {name: '剑', price: 500, dmg: 12, range: 100, cd: 0.46, cost: 18, icon: '🗡️'},
    laser: {name: '激光', price: 800, dmg: 11, range: 900, cd: 0.8, cost: 25, icon: '🔫', ranged: true},
  };
  // 没开局时（预览）用第 1 关的样子
  const DEFAULT = {ai_hp: 70, ai_dmg: 0.6, ai_speed: 0.65, ai_aggr: 0.35, ai_dodge: 0.4, ai_guard: 0, ai_think: 6, ai_weapon: 'fist', ai_shield: 0,
                   start_energy: 260, regen: 6, seconds: 150, boss: null, shop: ['stick'], super: false};
  const WORLD_OF = {shield: 1, sword: 2, laser: 3};
  const SHIELD_PRICE = 300, SHIELD_HP = 40;
  const SHOP = [['stick', '🪵', '木棍', 200], ['shield', '🛡️', '护盾', 300], ['sword', '🗡️', '剑', 500], ['laser', '🔫', '激光', 800]];
  const RIVALS = ['rival', 'sky', 'sunny', 'berry', 'ninja', 'cat'];

  let st, loop, fx, sh, P, A, B = null, C = DEFAULT, G = null, t = 0, tLeft = DEFAULT.seconds, hitStop = 0, zeroTime = 0, specials = 0, bought = [],
      warnedLow = false, ended = false, paused = false, lastResult = null;
  let hud = {};

  function fighter(x, avatar, facing, hp) {
    return {x, y: GROUND, vx: 0, vy: 0, facing, avatar, hp: hp || 100, maxHp: hp || 100, shownHp: hp || 100, energy: C.start_energy, weapon: 'fist', shield: 0,
            cd: 0, atk: 0, hurt: 0, charge: false, sp: 0, walk: 0, onGround: true, combo: 0, comboT: 0, frozen: 0, seed: Math.random() * 10};
  }

  // ---------------------------------------------------------------- 动作
  function spend(f, n) { if (f.energy <= 0) return false; f.energy = Math.max(0, f.energy - n); return true; }
  function canAct(f) { return !f.charge && f.hurt < 0.18 && !f.frozen; }
  function attack(f, o) {
    if (f.cd > 0 || !canAct(f)) return;
    const w = WEAPONS[f.weapon];
    if (f.energy <= 0) { if (f === P) sh.toast('能量用完了！快去答题', 'warn'); return; }
    spend(f, w.cost);
    f.combo = f.comboT > 0 ? (f.combo % 3) + 1 : 1; f.comboT = 0.7;
    f.cd = w.cd * (f.combo === 3 ? 1.4 : 1); f.atk = 0.2;
    Play.sfx.play(w.ranged ? 'laser' : 'swing');
    if (w.ranged) { fx.beams.push({x: f.x + f.facing * 40, y: f.y - 68, vx: 1200 * f.facing, owner: f, dmg: w.dmg, life: 1}); return; }
    const dx = (o.x - f.x) * f.facing;
    if (dx > -10 && dx < w.range && Math.abs(o.y - f.y) < 80) hit(o, w.dmg + (f.combo === 3 ? 6 : 0), f.facing, f.combo === 3 ? 520 : 260, f);
  }
  function special(f, o) {
    if (f.sp <= 0 || !canAct(f)) return;
    f.sp--; f.atk = 0.45;
    const strong = f === P && C.super;
    fx.fx.ring(f.x, f.y - 50, {color: f === P ? '#ffd23f' : '#ff5d5d', width: 10, grow: 900, life: 0.5});
    fx.fx.ring(f.x, f.y - 50, {color: '#fff', width: 5, grow: 700, life: 0.4});
    fx.fx.flash('#fff', 0.15); fx.fx.shake(12, 0.4); Play.sfx.play('power');
    if (Math.abs(o.x - f.x) < (strong ? 480 : 360)) hit(o, strong ? 40 : 25, Math.sign(o.x - f.x) || f.facing, 700, f, true);
    if (f === P) { specials++; sh.toast('💥 必杀！', 'good'); }
  }
  function hit(o, dmg, dir, kb, from, big) {
    let d = dmg, note = '';
    if (from === A) d = Math.max(1, Math.round(d * C.ai_dmg));                      // 越往后电脑打得越疼
    if (o === A && B) {                                                              // 大怪兽：护盾没破只吃 1/4 伤害，破盾后 1.5 倍
      if (B.window > 0) d = Math.round(d * 1.5);
      else { d = Math.ceil(d / 4); note = '🛡️'; if (!B.told) { B.told = true; sh.toast('护盾太硬了！答对题目才能破盾', 'warn'); } }
    } else if (o === A && !o.charge && !o.frozen && Math.random() < C.ai_guard) { d = Math.ceil(d * 0.4); note = '格挡'; }
    if (o.shield > 0) { const s = Math.min(o.shield, d); o.shield -= s; d -= s; Play.sfx.play('shield'); if (o.shield <= 0) fx.fx.burst(o.x, o.y - 60, {n: 12, colors: ['#9bd0ff', '#fff'], speed: 300}); }
    o.hp = Math.max(0, o.hp - d);
    o.vx = dir * kb; o.hurt = big ? 0.45 : 0.28; if (big) o.vy = -380;
    hitStop = big ? 0.12 : 0.06;
    fx.fx.burst(o.x - dir * 10, o.y - 64, {n: big ? 22 : 10, colors: ['#ffd23f', '#fff', '#ff8fab'], speed: big ? 420 : 280, shape: 'star', size: 7, grav: 300});
    fx.fx.text(o.x, o.y - 140, d ? `-${d}` : '🛡️', {color: d >= 15 ? '#ff3d7f' : '#ff7a00', size: d >= 15 ? 36 : 28});
    if (note) fx.fx.text(o.x + 30, o.y - 175, note, {color: '#4d96ff', size: 22});
    fx.fx.shake(big ? 10 : 4, big ? 0.3 : 0.12);
    Play.sfx.play('hit');
    if (from && from.combo === 3 && !big) fx.fx.text(from.x, from.y - 170, '连招！', {color: '#7b5cff', size: 24});
  }
  function buy(f, what) {
    const price = what === 'shield' ? SHIELD_PRICE : WEAPONS[what].price;
    if (f === P && !C.shop.includes(what)) { sh.toast(`打通第 ${WORLD_OF[what]} 个世界才能买${what === 'shield' ? '护盾' : WEAPONS[what].name}`, 'warn'); return false; }
    if (f.energy < price) { if (f === P) sh.toast(`还差 ${price - Math.floor(f.energy)} 能量，去答题攒吧`, 'warn'); return false; }
    if (what !== 'shield' && f.weapon === what) return false;
    f.energy -= price;
    if (what === 'shield') f.shield = SHIELD_HP; else f.weapon = what;
    fx.fx.burst(f.x, f.y - 70, {n: 16, colors: ['#ffd23f', '#7bdff2', '#fff'], shape: 'star', speed: 220});
    if (f === P) { bought.push(what); Play.sfx.play('buy'); sh.toast(`换上了${what === 'shield' ? '护盾' : WEAPONS[what].name}！`, 'good'); }
    return true;
  }
  function move(f, dir, dt, speedK) {
    if (!canAct(f) || !dir) { f.walk = 0; return; }
    let sp = 270 * (speedK || 1);
    if (f.energy <= 0) sp *= 0.4; else spend(f, COST.move * dt);
    f.x += dir * sp * dt; f.facing = dir; f.walk += dt;
  }
  function jump(f) {
    if (!f.onGround || !canAct(f) || f.energy <= 0) return;
    spend(f, COST.jump); f.vy = -760; f.onGround = false; Play.sfx.play('jump');
    fx.fx.burst(f.x, f.y, {n: 6, colors: ['#e8dcc8', '#fff'], speed: 120, grav: 200, size: 5});
  }

  // ---------------------------------------------------------------- 电脑
  function aiStep(dt) {
    const ai = A.ai;
    if (ai.answering > 0) {                      // 电脑在答题：和孩子一样的目标答对率
      ai.answering -= dt;
      if (ai.answering <= 0) {
        A.charge = false;
        if (Math.random() < (sh.match.target || 0.75)) {
          const xp = Math.round((60 + Math.random() * 60) / 10) * 10; A.energy += xp; ai.streak = (ai.streak || 0) + 1;
          if (ai.streak % 3 === 0) A.sp++;
          fx.fx.text(A.x, A.y - 150, `+${xp}`, {color: '#e08a00', size: 22});
        } else { ai.streak = 0; A.frozen = 5; }  // 答错：冷冻 5 秒
      }
      return;
    }
    if (A.frozen) return;
    const lead = A.hp / A.maxHp - P.hp / P.maxHp;     // 领先多了稍微收一点，落后了更积极（按关卡的凶猛程度上下浮动）
    ai.aggr = Math.max(0.2, Math.min(0.95, C.ai_aggr + (lead > 0.3 ? -0.1 : lead < -0.3 ? 0.15 : 0)));
    ai.think -= dt;
    if (A.energy < LOW && Math.random() < dt * 1.5) { A.charge = true; ai.answering = C.ai_think - 1 + Math.random() * 2; return; }
    if (A.energy > 600 && A.weapon === 'fist') buy(A, 'stick');
    else if (A.energy > 900 && A.weapon === 'stick' && C.ai_dmg > 0.9) buy(A, 'sword');
    else if (A.energy > 500 && !A.shield && P.weapon !== 'fist' && C.ai_guard > 0.1) buy(A, 'shield');
    if (A.sp > 0 && Math.abs(P.x - A.x) < 300 && Math.random() < dt * 2) special(A, P);
    const w = WEAPONS[A.weapon], dist = P.x - A.x;
    A.facing = Math.sign(dist) || A.facing;
    if (ai.think <= 0) { ai.think = 0.15 + Math.random() * 0.25; ai.move = Math.abs(dist) > w.range * 0.8 ? Math.sign(dist) : (Math.random() < 0.25 ? -Math.sign(dist) : 0); }
    move(A, ai.move || 0, dt, C.ai_speed + ai.aggr * 0.3);
    if (Math.abs(dist) < w.range && Math.random() < dt * 3 * ai.aggr) attack(A, P);
    if (P.atk > 0 && Math.random() < dt * C.ai_dodge && A.onGround) jump(A);
  }

  // ---------------------------------------------------------------- 主循环
  function update(dt) {
    t += dt;
    fx.fx.update(dt);
    if (ended) { for (const f of [P, A]) physics(f, dt); return; }
    if (sh.over) return;   // 还没开始：只播待机动画
    paused = !!(sh.quiz && sh.quiz.isOpen);   // 答题时整个游戏暂停：怪不动、不掉血、不计时
    if (paused) { syncHud(); return; }
    if (hitStop > 0) { hitStop -= dt; return; }
    tLeft -= dt;
    if (C.regen && P.energy < REGEN_CAP) P.energy = Math.min(REGEN_CAP, P.energy + C.regen * dt);
    if (B && B.window > 0) {
      B.window -= dt;
      if (B.window <= 0) { B.pips = B.max; B.told = false; sh.toast(`护盾又长回来了！再答对 ${B.max} 题破盾`, 'warn'); Play.sfx.play('shield'); }
    }
    const I = Play.input;
    if (canAct(P)) {
      move(P, (I.keys.right ? 1 : 0) - (I.keys.left ? 1 : 0), dt);
      if (I.take('jump')) jump(P);
      if (I.take('attack')) attack(P, A);
      if (I.take('special')) special(P, A);
      for (let i = 0; i < 4; i++) if (I.take('buy' + i)) buy(P, SHOP[i][0]);
    } else I.clear();
    if (I.take('quiz')) sh.quiz.open();
    aiStep(dt);
    for (const f of [P, A]) {
      f.cd = Math.max(0, f.cd - dt); f.atk = Math.max(0, f.atk - dt); f.hurt = Math.max(0, f.hurt - dt);
      f.comboT = Math.max(0, f.comboT - dt); f.frozen = Math.max(0, f.frozen - dt);
      physics(f, dt);
      f.energy = Math.min(MAX_ENERGY, f.energy);
      f.shownHp += (f.hp - f.shownHp) * Math.min(1, dt * 6);
    }
    const gap = A.x - P.x, MIN = 46;   // 两个人不叠在一起
    if (Math.abs(gap) < MIN && Math.abs(P.y - A.y) < 70) {
      const push = (MIN - Math.abs(gap)) / 2 * (Math.sign(gap) || 1);
      P.x -= push; A.x += push; clampX(P); clampX(A);
    }
    if (P.energy <= 0) zeroTime += dt;
    if (P.energy < LOW && !warnedLow) { warnedLow = true; sh.toast('能量快没了！点「✍️ 答题」补充', 'warn'); }
    if (P.energy >= LOW) warnedLow = false;
    for (const e of fx.beams) {
      e.life -= dt; e.x += e.vx * dt;
      const o = e.owner === P ? A : P;
      if (Math.abs(e.x - o.x) < 28 && e.life > 0 && Math.abs(o.y - GROUND) < 120) { hit(o, e.dmg, Math.sign(e.vx), 180, e.owner); e.life = 0; }
    }
    fx.beams = fx.beams.filter(e => e.life > 0 && e.x > -60 && e.x < st.W + 60);
    if (P.hp <= 0 || A.hp <= 0 || tLeft <= 0) finish();
    syncHud();
  }
  function physics(f, dt) {
    f.vy += GRAV * dt; f.y += f.vy * dt; f.x += f.vx * dt; f.vx *= Math.pow(0.015, dt);
    if (f.y >= GROUND) { if (!f.onGround && f.vy > 300) fx.fx.burst(f.x, GROUND, {n: 5, colors: ['#e8dcc8'], speed: 100, grav: 200, size: 4}); f.y = GROUND; f.vy = 0; f.onGround = true; }
    clampX(f);
  }
  function clampX(f) { f.x = Math.max(50, Math.min(st.W - 50, f.x)); }

  function poseOf(f) {
    if (ended) return (f === P) === (lastResult === 'win') || lastResult === 'draw' ? 'win' : 'lose';
    if (f.frozen) return 'freeze';
    if (f.hurt > 0.12) return 'hurt';
    if (f.charge) return 'charge';
    if (f.atk > 0) return 'punch';
    if (!f.onGround) return 'jump';
    if (f.walk > 0) return 'walk';
    return 'idle';
  }

  function render() {
    const ctx = st.ctx, W = st.W;
    st.begin();
    const [ox, oy] = fx.fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    const scene = (G && G.scene) || 'sunset';
    Play.scene.sky(ctx, W, H, scene);
    Play.scene.drawClouds(ctx, W, scene, t);
    Play.scene.hills(ctx, W, H, GROUND, scene, 0);
    drawArena(ctx, W);
    for (const f of [A, P]) {
      const big = f === A && B ? 1.3 : 1, top = f.y - 175 * big;
      if (f === A && B && !ended) drawBossShield(ctx, big);
      Art.hero(ctx, f.x, f.y, {avatar: f.avatar, pose: poseOf(f), t: t + f.seed, facing: f.facing, weapon: f.weapon, shield: f.shield, seed: f.seed, scale: big,
                               alpha: f.hurt > 0 && Math.sin(t * 50) > 0 ? 0.6 : 1, lift: GROUND - f.y > 0 ? 0 : 0});
      if (f.sp > 0 && !ended) { ctx.fillStyle = '#ffd23f'; for (let i = 0; i < f.sp; i++) { Play.starPath(ctx, f.x - (f.sp - 1) * 9 + i * 18, top + 5 + Math.sin(t * 4 + i) * 3, 7, t); ctx.fill(); } }
      if (f === A && A.ai.answering > 0) bubble(ctx, A.x, top, '🤔');
      if (f === P && P.charge) bubble(ctx, P.x, P.y - 175, '✍️');
    }
    for (const e of fx.beams) {
      ctx.strokeStyle = '#7bdff2'; ctx.lineWidth = 10; ctx.lineCap = 'round'; ctx.globalAlpha = 0.5;
      ctx.beginPath(); ctx.moveTo(e.x - 50 * Math.sign(e.vx), e.y); ctx.lineTo(e.x, e.y); ctx.stroke();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 4; ctx.globalAlpha = 1; ctx.stroke();
    }
    fx.fx.draw(ctx);
    ctx.restore();
    fx.fx.drawFlash(ctx, W, H);
    if (paused && !ended) {   // 时间停止：画面蒙一层淡蓝
      ctx.fillStyle = 'rgba(40,70,140,.32)'; ctx.fillRect(0, 0, W, H);
      ctx.font = '900 22px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.lineWidth = 5; ctx.strokeStyle = '#2b2340'; ctx.strokeText('⏸ 时间停止，答完题接着打', W / 2, 96);
      ctx.fillStyle = '#fff'; ctx.fillText('⏸ 时间停止，答完题接着打', W / 2, 96);
    }
  }
  function drawBossShield(ctx, big) {
    const cx = A.x, cy = A.y - 80 * big, r = 100 * big;
    if (B.pips > 0) {
      const g = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, r);
      g.addColorStop(0, 'rgba(123,223,242,0.05)'); g.addColorStop(1, 'rgba(123,223,242,0.35)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = 'rgba(77,150,255,' + (0.6 + 0.3 * Math.sin(t * 4)) + ')'; ctx.lineWidth = 4; ctx.stroke();
      for (let i = 0; i < B.max; i++) {   // 护盾上的格子：答对一题碎一格
        const x = cx - (B.max - 1) * 15 + i * 30, y = cy - r - 16;
        ctx.font = '22px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.globalAlpha = i < B.pips ? 1 : 0.25; ctx.fillText('🛡️', x, y); ctx.globalAlpha = 1;
      }
    } else if (B.window > 0) {
      ctx.font = '900 24px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const txt = `破盾！${Math.ceil(B.window)}`;
      ctx.lineWidth = 5; ctx.strokeStyle = '#2b2340'; ctx.strokeText(txt, cx, cy - r - 16);
      ctx.fillStyle = Math.sin(t * 12) > 0 ? '#ff3d7f' : '#ffd23f'; ctx.fillText(txt, cx, cy - r - 16);
    }
  }
  function bubble(ctx, x, y, emo) {
    ctx.fillStyle = '#fff'; ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(x, y, 18, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.font = '20px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(emo, x, y + 1);
  }
  function drawArena(ctx, W) {
    // 擂台地面：木地板 + 两边的小旗
    ctx.fillStyle = '#f2c38b'; ctx.fillRect(0, GROUND, W, H - GROUND);
    ctx.fillStyle = '#e3a96b'; for (let x = 0; x < W; x += 70) ctx.fillRect(x, GROUND, 3, H - GROUND);
    ctx.fillStyle = '#fff1d6'; ctx.fillRect(0, GROUND, W, 6);
    ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(0, GROUND); ctx.lineTo(W, GROUND); ctx.stroke();
    for (const x of [26, W - 26]) {
      ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(x, GROUND); ctx.lineTo(x, GROUND - 150); ctx.stroke();
      const c = x < W / 2 ? '#3fbf8f' : '#ff5d5d', wv = Math.sin(t * 4 + x) * 4;
      ctx.fillStyle = c; ctx.beginPath(); ctx.moveTo(x, GROUND - 150); ctx.quadraticCurveTo(x + (x < W / 2 ? 30 : -30), GROUND - 140 + wv, x + (x < W / 2 ? 44 : -44), GROUND - 132); ctx.lineTo(x, GROUND - 112); ctx.closePath(); ctx.fill(); ctx.stroke();
    }
    // 彩旗
    ctx.strokeStyle = 'rgba(43,35,64,.5)'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(26, 60); ctx.quadraticCurveTo(W / 2, 100, W - 26, 60); ctx.stroke();
    const cols = ['#ffd23f', '#ff6fa3', '#4d96ff', '#3fbf8f', '#7b5cff'];
    for (let i = 1; i < 16; i++) {
      const k = i / 16, x = 26 + (W - 52) * k, y = 60 + 40 * 4 * k * (1 - k) * 0.5 * 2;
      ctx.fillStyle = cols[i % 5]; ctx.beginPath(); ctx.moveTo(x - 9, y); ctx.lineTo(x + 9, y); ctx.lineTo(x, y + 18 + Math.sin(t * 3 + i) * 2); ctx.closePath(); ctx.fill();
    }
  }

  // ---------------------------------------------------------------- 界面
  function buildUi() {
    sh.hud.innerHTML = `
      <div class="sm-card me"><canvas class="sm-face"></canvas><div class="grow"><div class="sm-name">我</div><div class="sm-hp"><i></i><b></b></div>
        <div class="sm-en"><span>⚡<b class="sm-pe">300</b></span><div class="sm-enbar"><i></i></div></div><div class="sm-sp"></div></div></div>
      <div class="sm-mid"><div class="sm-time">2:30</div><div class="sm-stage"></div></div>
      <div class="sm-card ai"><div class="grow"><div class="sm-name">电脑</div><div class="sm-hp"><i></i><b></b></div>
        <div class="sm-en"><span>⚡<b class="sm-ae">300</b></span></div><div class="sm-sp"></div></div><canvas class="sm-face"></canvas></div>`;
    sh.ctrl.innerHTML = `
      <div class="sm-shop">${SHOP.map(([k, ico, name, price], i) => `<button type="button" class="sm-buy" data-i="${i}"><span>${ico}</span><b>${name}</b><small>⚡${price}</small><em>🔒 世界${WORLD_OF[k] || ''}</em></button>`).join('')}</div>
      <div class="sm-pads">
        <div class="pl-pad"><button type="button" class="pl-btn big" data-k="left" aria-label="向左">◀</button><button type="button" class="pl-btn big" data-k="right" aria-label="向右">▶</button></div>
        <button type="button" class="pl-btn quiz" data-k="quiz">✍️ 答题</button>
        <div class="pl-pad"><button type="button" class="pl-btn big" data-k="jump">跳</button><button type="button" class="pl-btn big hit" data-k="attack">打</button><button type="button" class="pl-btn big sp" data-k="special" disabled>必杀</button></div>
      </div>`;
    sh.ctrl.querySelectorAll('[data-k]').forEach(b => Play.input.hold(b, b.dataset.k));
    sh.ctrl.querySelectorAll('.sm-buy').forEach(b => b.addEventListener('pointerdown', e => { e.preventDefault(); if (!sh.over) Play.input.pressed['buy' + b.dataset.i] = true; }));
    const q = s => sh.root.querySelector(s);
    hud = {php: q('.sm-card.me .sm-hp i'), phpT: q('.sm-card.me .sm-hp b'), ahp: q('.sm-card.ai .sm-hp i'), ahpT: q('.sm-card.ai .sm-hp b'),
           pen: q('.sm-enbar i'), pe: q('.sm-pe'), ae: q('.sm-ae'), time: q('.sm-time'), stage: q('.sm-stage'), aname: q('.sm-card.ai .sm-name'), psp: q('.sm-card.me .sm-sp'), asp: q('.sm-card.ai .sm-sp'),
           en: q('.sm-card.me .sm-en'), quiz: q('.pl-btn.quiz'), sp: q('.pl-btn.sp'), buys: [...sh.ctrl.querySelectorAll('.sm-buy')],
           faces: [q('.sm-card.me .sm-face'), q('.sm-card.ai .sm-face')]};
  }
  function syncHud() {
    hud.php.style.width = 100 * P.shownHp / P.maxHp + '%'; hud.ahp.style.width = 100 * A.shownHp / A.maxHp + '%';
    hud.phpT.textContent = Math.ceil(P.hp); hud.ahpT.textContent = Math.ceil(A.hp);
    hud.pen.style.width = Math.min(100, P.energy / 10) + '%';
    hud.pe.textContent = Math.floor(P.energy); hud.ae.textContent = Math.floor(A.energy);
    hud.en.classList.toggle('low', P.energy < LOW);
    const s = Math.max(0, Math.ceil(tLeft));
    hud.time.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    hud.time.classList.toggle('low', s <= 20);
    hud.psp.textContent = P.sp > 0 ? '⭐'.repeat(Math.min(5, P.sp)) : ''; hud.asp.textContent = A.sp > 0 ? '⭐'.repeat(Math.min(5, A.sp)) : '';
    hud.quiz.classList.toggle('pulse', P.energy < LOW); hud.quiz.classList.toggle('empty', P.energy <= 0);
    hud.sp.disabled = P.sp <= 0; hud.sp.textContent = P.sp > 0 ? `必杀×${P.sp}` : '必杀';
    hud.buys.forEach((b, i) => {
      const [k, , , price] = SHOP[i], lock = !C.shop.includes(k);
      b.classList.toggle('lock', lock); b.disabled = lock || P.energy < price || (k !== 'shield' && P.weapon === k) || P.charge;
      b.classList.toggle('have', k !== 'shield' && P.weapon === k);
    });
  }

  function reset(match) {
    const W = st.W;
    G = match && match.stage; C = (G && G.params) || DEFAULT;
    const rival = G ? RIVALS[(G.n * 7 + G.world) % RIVALS.length] : Play.pick(RIVALS);
    P = fighter(W * 0.25, (match && match.avatar) || sh.data.avatar, 1);
    A = fighter(W * 0.75, rival === P.avatar ? 'rival' : rival, -1, C.ai_hp);
    A.ai = {think: 0, answering: 0, aggr: C.ai_aggr};
    A.weapon = C.ai_weapon || 'fist'; A.shield = C.ai_shield || 0;
    B = C.boss ? {pips: C.boss.pips, max: C.boss.pips, window: 0, told: false} : null;
    paused = false;
    if (hud.stage) {
      hud.stage.textContent = G ? `第 ${G.n} 关${G.boss ? ' 👑' : ''}` : '';
      hud.aname.textContent = B ? '👑 大怪兽' : '电脑';
    }
    fx = {fx: new Play.FX(), beams: []};
    zeroTime = 0; specials = 0; bought = []; warnedLow = false; ended = false; hitStop = 0; t = 0;
    if (hud.faces) { Art.portrait(hud.faces[0], P.avatar, 'idle', {zoom: 1.9}); Art.portrait(hud.faces[1], A.avatar, 'idle', {zoom: 1.9, facing: -1}); }
  }

  function finish() {
    if (ended) return;
    ended = true;
    // 有人没血了就分胜负；时间到了比剩下的血量比例（大怪兽血多，按比例才公平）
    const pr = P.hp / P.maxHp, ar = A.hp / A.maxHp;
    const result = P.hp <= 0 ? 'lose' : A.hp <= 0 ? 'win' : Math.abs(pr - ar) < 0.01 ? 'draw' : (pr > ar ? 'win' : 'lose');
    lastResult = result;
    if (result === 'win') { fx.fx.burst(P.x, P.y - 120, {n: 60, shape: 'confetti', speed: 520, size: 10, life: 1.6}); }
    sh.quiz && sh.quiz.close();
    setTimeout(() => sh.finish(result, {zero_energy_s: Math.round(zeroTime), specials, weapons: bought, hp_left: Math.round(P.hp), ai_hp_left: Math.round(A.hp)},
      {win: '🏆 你赢了！', lose: '💪 差一点！再来一局', draw: '🤝 平局！'}[result]), 1400);
  }

  PlayShell.init({
    game: 'stickman', tempo: 138, dock: false,
    rules: ['走、跳、出拳都要花 <b>⚡能量</b>，能量<b>只能靠答题</b>补：答对一题约 +100。',
            '点 <b>✍️ 答题</b> 时<b>时间停止</b>，答完接着打。连对 3 题攒一颗 <b>⭐ 必杀</b>，答错冷冻 5 秒。',
            '一关比一关难；每个世界第 6 关是 <b>👑 大怪兽</b>，有护盾，答对几题才能破盾。',
            '每关最多 <b>3 颗星</b>：过关、专注指数 90+、血量剩一半。打通一个世界解锁新装备。',
            '题目按你自己的水平出；电脑也要答题，答对率和你一样。'],
    keys: '电脑键盘：A / D 走，W 跳，J 出拳，L 必杀，Q 答题，1–4 买装备；答题时 1–4 选答案',
    setup(s) {
      sh = s;
      st = Play.stage(sh.canvas, {height: H, minW: 640, maxW: 1300});
      st.onResize = (W, old) => { if (P) { P.x *= W / old; A.x *= W / old; } };
      Play.input.init({left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], jump: ['ArrowUp', 'w', ' '], attack: ['j', 'k'], special: ['l'],
                       quiz: ['q', 'Enter'], buy0: ['1'], buy1: ['2'], buy2: ['3'], buy3: ['4']});
      buildUi(); reset(null); syncHud();
      loop = Play.loop(update, render);
      loop.onHide = () => { if (!sh.over && !ended) sh.quiz.open(); };   // 切走了：自动打开答题（充能、减伤），回来接着打
      loop.start();
    },
    begin(match) {
      reset(match);
      tLeft = Math.max(30, Math.min(C.seconds, match.seconds_left));
      Play.input.clear(); syncHud();
      if (B) sh.toast(`👑 大怪兽有护盾！答对 ${B.max} 题破盾，再狠狠打`, 'warn');
      else if (G && G.n <= 2) sh.toast('开打！能量快没了就点「✍️ 答题」', 'good');
      else sh.toast(`第 ${G ? G.n : ''} 关，开打！`, 'good');
    },
    onQuizOpen() { P.charge = true; Play.input.clear(); Play.input.blocked = true; },
    onQuizClose() { P.charge = false; Play.input.blocked = false; },
    onXP(res) {
      P.energy += res.xp;
      if (res.special) P.sp++;
      if (B && B.pips > 0 && B.window <= 0) {   // 大怪兽：答对一题碎一格护盾，碎完破盾
        B.pips--;
        fx.fx.burst(A.x, A.y - 180, {n: 14, colors: ['#9bd0ff', '#fff'], speed: 260});
        if (B.pips <= 0) { B.window = C.boss.window; fx.fx.flash('#fff', 0.2); Play.sfx.play('power'); sh.toast(`🛡️ 护盾破了！${C.boss.window} 秒内狠狠打！`, 'good'); }
        else sh.toast(`护盾裂开了！再答对 ${B.pips} 题`, 'good');
      }
      fx.fx.text(P.x, P.y - 150, `+${res.xp}⚡`, {color: '#e08a00', size: res.crit ? 38 : 28});
      fx.fx.burst(P.x, P.y - 60, {n: res.crit ? 26 : 12, colors: ['#ffd23f', '#fff1a8'], shape: 'star', speed: 200, grav: -100});
    },
    onAnswer(res) { if (!res.correct) { P.frozen = 0; } },
  });
})();

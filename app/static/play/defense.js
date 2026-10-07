/* 星星守卫（defense）：小怪兽一波波从右边走来，答对一题就从城墙上放一颗星星魔法打它。
   连对越多星星越强；每连对 3 题（res.special）放「流星雨」砸中全场；答错被冻住一会儿，不能放魔法。
   6 波：前 5 波小怪（史莱姆 / 蝙蝠 / 小幽灵），第 6 波大怪兽（小龙）。城堡没血了就输，打败大怪兽就赢。
   公平：题目难度服务器按每个孩子调好了（目标约 72% 答对），这里再按孩子自己的答题速度调怪兽的数量和走路速度，
   慢一点但认真的孩子一样有机会，拼的是专注和准确。整局不超过 match.seconds_left（最多 240 秒）。 */
(function () {
  const {clamp, rand, lerp, pick, ease} = Play;
  const MAX_S = 240;                       // 一局最长秒数
  const CASTLE_HP = 10;
  const WAVES = 6;
  const WAVE_SEC = [20, 22, 25, 28, 30];   // 前 5 波每波出怪时长（秒）
  const PRESSURE = 1.6;                    // 怪兽血量 / 72% 答对的孩子在这段时间能打出的伤害
  const BOSS_K = 1.25, BOSS_WALK = 26, STOMP_EVERY = 3;
  const KINDS = {
    slime: {hp: 1, dmg: 1, sp: 1, hitY: 22},
    bat: {hp: 1, dmg: 1, sp: 1.25, hitY: 42},
    ghost: {hp: 2, dmg: 2, sp: 0.85, hitY: 38},
  };
  const MIX = [['slime'], ['slime', 'slime', 'bat'], ['slime', 'bat', 'ghost'], ['slime', 'bat', 'ghost', 'ghost'], ['bat', 'ghost', 'ghost', 'slime']];
  const SLIME_C = ['#7ed957', '#ff8fab', '#7bdff2', '#ffd23f', '#b69cff'];
  const HINTS = ['小史莱姆跳过来啦！', '小蝙蝠也飞来了！', '小幽灵飘过来了！', '怪兽越来越多啦！', '最后一波小怪，加油！', '大怪兽来了！'];

  let sh = null, stage = null, ctx = null, loop = null, fx = new Play.FX(), quiz = null;
  let S = null;            // 这一局的全部状态
  let L = {};              // 版面（世界坐标）
  let night = null;        // 换成夜晚时用来淡入的离屏画布
  let hudEl = {};

  // ---------------------------------------------------------------- 版面：窄屏把整个世界缩小一点
  function layout() {
    const k = clamp(stage.W / 820, 0.68, 1);
    const WW = stage.W / k, WH = stage.H / k, GY = WH - 64;
    const cx = 112, cs = 1;
    L = {k, WW, WH, GY, cx, cs, heroX: cx + 6, heroY: GY - 113 * cs, gate: cx + 84 * cs + 30, spawn: WW + 24};
  }

  // ---------------------------------------------------------------- 答题速度 → 节奏
  function pace() { return S && S.thinks.length ? S.thinks.reduce((a, b) => a + b, 0) / S.thinks.length : 6; }
  // 按目标正确率，平均多少秒能打出一次伤害（含答错冷冻）
  function unit() { return (pace() + 1.6 + 0.28 * 5) / 0.72; }
  function walkTime(kind) { return clamp(8 + 0.8 * unit(), 9, 30) / KINDS[kind].sp; }

  function newState(mode) {
    return {mode, t: 0, wave: 0, castle: CASTLE_HP, castleShake: 0, castleFlash: 0, mons: [], bolts: [], meteors: [], queue: [], spawnT: 0, gap: 3,
            boss: null, bossDead: false, minionT: 0, banner: null, nightT: 0, streak: 0, best: 0, kills: 0, frozenUntil: 0, castT: 0,
            thinks: [], readyAt: 0, deadline: 0, endT: 0, result: null, xpPop: null};
  }

  function previewState() {
    const s = newState('preview');
    s.mons = [mon('slime', 0.42), mon('bat', 0.6), mon('ghost', 0.78), mon('slime', 0.9)];
    s.mons[3].color = '#ff8fab';
    return s;
  }
  function mon(kind, p) {
    return {kind, p, hp: KINDS[kind].hp, max: KINDS[kind].hp, pend: 0, hurt: 0, dying: 0, seed: rand(0, 10), color: kind === 'slime' ? pick(SLIME_C) : null,
            walk: walkTime(kind), x: 0, y: 0, attack: 0};
  }

  // ---------------------------------------------------------------- 波次
  function startWave() {
    S.wave++;
    if (S.wave < WAVES) {
      const mix = MIX[S.wave - 1], avg = mix.reduce((a, k) => a + KINDS[k].hp, 0) / mix.length;
      const n = Math.max(2, Math.round(WAVE_SEC[S.wave - 1] / unit() * PRESSURE / avg));
      S.queue = Array.from({length: n}, (_, i) => mix[i % mix.length]);
      S.gap = WAVE_SEC[S.wave - 1] / n; S.spawnT = 0.6;
    } else {
      const hp = clamp(Math.round(40 / unit() * BOSS_K), 3, 12);
      S.boss = {hp, max: hp, p: 0, pend: 0, hurt: 0, stomp: 1.2, jump: 0, dying: 0, x: 0, y: 0};
      S.minionT = unit() * 0.8;
    }
    banner(S.wave);
  }
  function banner(w) {
    S.banner = {t: 0, life: w === WAVES ? 2.6 : 1.8, title: w === WAVES ? '👑 大怪兽！' : `第 ${w} 波！`, sub: HINTS[w - 1]};
    Play.sfx.play(w === WAVES ? 'power' : 'go');
  }

  // ---------------------------------------------------------------- 答题结果
  function onAnswer(res) {
    if (!S || S.mode !== 'play' || S.result) return;
    const now = performance.now();
    S.thinks.push(clamp((now - S.readyAt) / 1000, 1.5, 15)); if (S.thinks.length > 5) S.thinks.shift();
    if (res.correct) {
      S.readyAt = now + 750;
      S.streak = res.streak || S.streak + 1; S.best = Math.max(S.best, S.streak);
      cast(S.streak >= 3 ? 2 : 1);
      if (res.special) setTimeout(() => meteorShower(), 380);
    } else {
      const ms = res.freeze_ms || 5000;
      S.readyAt = now + ms + 150; S.streak = 0;
      S.frozenUntil = now + ms;
      fx.burst(L.heroX, L.heroY - 50, {n: 16, colors: ['#e3f6ff', '#9be7ff', '#ffffff'], speed: 180, size: 5, shape: 'star', grav: 120});
      fx.text(L.heroX + 10, L.heroY - 105, '冻住了！', {color: '#4d96ff', size: 24});
    }
    hud();
  }
  function onXP(res) {
    fx.text(L.heroX + 30, L.heroY - 120, `+${res.xp}⚡`, {color: '#e08a00', size: res.crit ? 30 : 22, rise: 50});
  }

  // 找最前面、还没被别的星星「预订」打死的怪兽
  function target() {
    const live = S.mons.filter(m => !m.dying && m.hp - m.pend > 0).sort((a, b) => b.p - a.p);
    if (live.length) return live[0];
    if (S.boss && !S.boss.dying && S.boss.hp - S.boss.pend > 0) return S.boss;
    const any = S.mons.filter(m => !m.dying).sort((a, b) => b.p - a.p)[0];
    return any || (S.boss && !S.boss.dying ? S.boss : null);
  }

  function cast(power) {
    const tg = target();
    S.castT = 0.3;
    Play.sfx.play('whoosh');
    const b = {x0: L.hand[0], y0: L.hand[1], t: 0, power, tg, heal: !tg, x: L.hand[0], y: L.hand[1]};
    const [tx, ty] = b.heal ? [L.cx, L.GY - 190 * L.cs] : aim(tg);
    b.dur = 0.38 + Math.hypot(tx - b.x0, ty - b.y0) / 2000;
    if (tg) tg.pend += power;
    S.bolts.push(b);
  }
  function aim(tg) {
    if (tg === S.boss) return [tg.x - 10, tg.y - 110 * L.bossS];
    return [Math.min(tg.x, L.WW - 36), tg.y - KINDS[tg.kind].hitY];   // 还没完全走进画面的怪兽：星星打在画面边上，不飞出去
  }

  function hit(tg, dmg, x, y, big) {
    if (!tg || tg.dying) return;
    tg.hp -= dmg; tg.hurt = 0.3;
    const isBoss = tg === S.boss;
    if (!isBoss) tg.p = Math.max(0, tg.p - 0.015 * dmg);
    fx.burst(x, y, {n: big ? 22 : 14, colors: ['#ffd23f', '#fff6a8', '#ff8fab', '#ffffff'], speed: big ? 320 : 240, size: 6, shape: 'star', grav: 300});
    fx.ring(x, y, {color: dmg > 1 ? '#ff8fab' : '#ffd23f', grow: 380, life: 0.35, max: 0.35, width: 5});
    fx.text(x, y - 20, `-${dmg}`, {color: dmg > 1 ? '#ff3d7f' : '#ff7a00', size: isBoss ? 36 : 30});
    Play.sfx.play('hit');
    if (tg.hp <= 0) kill(tg);
  }
  function kill(tg) {
    tg.dying = 0.001;
    if (tg === S.boss) {
      S.bossDead = true;
      fx.shake(14, 0.8); fx.flash('#fff', 0.3);
      for (let i = 0; i < 6; i++) setTimeout(() => {
        if (!S.boss) return;
        fx.burst(S.boss.x + rand(-70, 50), S.boss.y - rand(30, 170), {n: 26, speed: 380, size: 8, shape: i % 2 ? 'star' : 'circle'});
        Play.sfx.play('boom');
      }, i * 160);
      win();
      return;
    }
    S.kills++;
    fx.burst(tg.x, tg.y - 20, {n: 10, colors: ['#ffd23f', '#ffb703'], speed: 260, size: 6, grav: 700});
    fx.burst(tg.x, tg.y - 20, {n: 8, colors: ['#ffffff', '#c3a6ff', '#7bdff2'], speed: 200, size: 5, shape: 'star', grav: 200});
    Play.sfx.play('coin', 0.05);
  }

  // 流星雨：每只怪兽都挨一颗，大怪兽也会被砸弱
  function meteorShower() {
    if (!S || S.mode !== 'play' || S.result) return;
    Play.sfx.play('power');
    fx.flash('#fff3c4', 0.18);
    fx.text(L.WW / 2, L.WH * 0.36, '🌠 流星雨！', {color: '#ff6fa3', size: 56, life: 1.4, rise: 30});
    const tgs = S.mons.filter(m => !m.dying);
    if (S.boss && !S.boss.dying) tgs.push(S.boss, S.boss);
    const list = tgs.map((tg, i) => ({tg, delay: 0.25 + i * 0.12}));
    for (let i = 0; i < 6; i++) list.push({tg: null, delay: 0.2 + rand(0, 0.9), gx: rand(L.gate, L.WW - 30)});
    let bossOnce = false;
    for (const it of list) {
      if (it.tg === S.boss) { if (bossOnce) it.deco = true; bossOnce = true; }
      if (it.tg && !it.deco) it.tg.pend += 1;
      S.meteors.push(Object.assign({t: -it.delay, dur: 0.55, ox: rand(160, 320), oy: -rand(60, 160)}, it));
    }
  }

  // ---------------------------------------------------------------- 结束
  function win() {
    if (S.result) return;
    S.result = 'win'; S.endT = 2.4;
    for (let i = 0; i < 8; i++) setTimeout(() => confetti(), i * 220);
  }
  function lose(why) {
    if (S.result) return;
    S.result = 'lose'; S.why = why; S.endT = why === 'time' ? 1.2 : 2;
    if (why !== 'time') { fx.shake(10, 0.6); Play.sfx.play('boom'); }
  }
  function confetti() {
    fx.burst(rand(L.WW * 0.15, L.WW * 0.85), -10, {n: 26, shape: 'confetti', speed: 200, dir: Math.PI / 2, spread: 1.6, grav: 260, life: 2.2, size: 11,
                                                   colors: ['#ffd23f', '#ff6fa3', '#7bdff2', '#b8f2a6', '#c3a6ff']});
  }
  function finishNow() {
    const boss = S.bossDead ? 1 : 0;
    const stats = {wave: S.wave, boss, castle_hp: Math.max(0, S.castle), monsters: S.kills + boss, combo: S.best};
    let title;
    if (S.result === 'win') title = S.castle >= CASTLE_HP ? '🏰 城堡毫发无伤！太厉害了！' : '🏰 城堡守住啦！';
    else if (S.why === 'time') title = S.wave >= WAVES ? '⏰ 时间到！大怪兽差一点就倒了' : `⏰ 时间到！守到了第 ${S.wave} 波`;
    else title = S.wave >= WAVES ? '🐉 差一点打败大怪兽！再来一次' : `💪 守到了第 ${S.wave} 波，下次再来`;
    sh.finish(S.result, stats, title);
  }

  // ---------------------------------------------------------------- 更新
  function update(dt) {
    fx.update(dt);
    if (!S) return;
    S.t += dt;
    S.castleShake = Math.max(0, S.castleShake - dt); S.castleFlash = Math.max(0, S.castleFlash - dt); S.castT = Math.max(0, S.castT - dt);
    if (S.mode === 'preview') { for (const m of S.mons) place(m); return; }
    const now = performance.now();
    if (!S.result && now >= S.deadline) lose('time');
    if (S.result) {
      S.endT -= dt;
      if (S.endT <= 0 && !S.finished) { S.finished = true; finishNow(); }
    }
    if (S.wave === WAVES) S.nightT = Math.min(1, S.nightT + dt / 1.5);
    if (S.banner) { S.banner.t += dt; if (S.banner.t >= S.banner.life) S.banner = null; }
    const fighting = !S.result && !S.banner;
    // 出怪
    if (fighting && S.queue.length) {
      S.spawnT -= dt;
      if (S.spawnT <= 0) { S.mons.push(mon(S.queue.shift(), 0)); S.spawnT = S.gap; }
    }
    if (fighting && S.boss && !S.boss.dying) {
      S.minionT -= dt;
      if (S.minionT <= 0) { S.mons.push(mon('slime', 0)); S.minionT = 1.6 * unit(); }
    }
    // 小怪走路、撞城堡
    for (const m of S.mons) {
      m.hurt = Math.max(0, m.hurt - dt);
      if (m.dying) { m.dying += dt; place(m); continue; }
      if (m.attack) {           // 跳向城门
        m.attack += dt;
        if (m.attack > 0.35) { damageCastle(KINDS[m.kind].dmg, m.x); m.dying = 0.001; m.attackDone = true; }
      } else if (!S.result) {
        m.p += dt / m.walk;
        if (m.p >= 1) { m.p = 1; m.attack = 0.001; Play.sfx.play('jump'); }
      }
      place(m);
    }
    S.mons = S.mons.filter(m => !(m.dying > 0.45));
    // 大怪兽
    const B = S.boss;
    if (B) {
      B.hurt = Math.max(0, B.hurt - dt);
      if (B.dying) B.dying += dt;
      else if (!S.result && !S.banner) {
        if (B.p < 1) B.p = Math.min(1, B.p + dt / BOSS_WALK);
        else {
          B.stomp -= dt;
          if (B.stomp < 0.4) B.jump = 1 - B.stomp / 0.4;
          if (B.stomp <= 0) { B.stomp = STOMP_EVERY; B.jump = 0; fx.shake(8, 0.3); Play.sfx.play('boom'); damageCastle(1, L.gate); fx.burst(B.x - 30, L.GY, {n: 14, colors: ['#c99a6b', '#e8d3b0'], speed: 200, size: 6, grav: 500}); }
        }
      }
      placeBoss();
    }
    // 星星魔法弹
    for (const b of S.bolts) {
      b.t += dt;
      const k = Math.min(1, b.t / b.dur);
      const [tx, ty] = b.heal ? [L.cx, L.GY - 190 * L.cs] : aim(b.tg);
      const lift = Math.min(170, Math.abs(tx - b.x0) * 0.32) + 30;
      const mx = (b.x0 + tx) / 2, my = Math.min(b.y0, ty) - lift, e = ease.inOutSine(k) * 0.4 + k * 0.6;
      const nx = (1 - e) * (1 - e) * b.x0 + 2 * (1 - e) * e * mx + e * e * tx, ny = (1 - e) * (1 - e) * b.y0 + 2 * (1 - e) * e * my + e * e * ty;
      b.ang = Math.atan2(ny - b.y, nx - b.x); b.x = nx; b.y = ny;
      fx.burst(b.x, b.y, {n: b.power > 1 ? 2 : 1, colors: b.power > 1 ? ['#ff8fab', '#ffd23f', '#fff'] : ['#ffd23f', '#fff6a8', '#fff'], speed: 50, life: 0.45, size: b.power > 1 ? 4.5 : 3.5, shape: 'star', grav: 40});
      if (k >= 1) {
        b.done = true;
        if (b.heal) {
          if (S.castle < CASTLE_HP && !S.result) { S.castle++; fx.text(L.cx, L.GY - 200 * L.cs, '+1 ❤', {color: '#ff4d6d', size: 28}); Play.sfx.play('shield'); hud(); }
          fx.burst(tx, ty, {n: 12, shape: 'star', speed: 160, grav: 100});
        } else {
          b.tg.pend = Math.max(0, b.tg.pend - b.power);
          hit(b.tg, b.power, tx, ty, b.power > 1);
        }
      }
    }
    S.bolts = S.bolts.filter(b => !b.done);
    // 流星
    for (const m of S.meteors) {
      m.t += dt;
      if (m.t < 0) continue;
      const [tx, ty] = m.tg && !m.tg.dying ? aim(m.tg) : [m.gx || (m.tg ? m.tg.x : L.WW / 2), m.tg ? m.tg.y - 20 : L.GY];
      m.tx = tx; m.ty = ty;
      if (m.t >= m.dur) {
        m.done = true;
        fx.burst(tx, ty, {n: 18, colors: ['#ffd23f', '#ff8fab', '#fff', '#c3a6ff'], speed: 300, size: 6, shape: 'star', grav: 400});
        fx.ring(tx, ty, {color: '#fff3c4', grow: 420, width: 6});
        fx.shake(6, 0.15); Play.sfx.play('boom');
        if (m.tg && !m.deco) { m.tg.pend = Math.max(0, m.tg.pend - 1); hit(m.tg, 1, tx, ty, true); }
      }
    }
    S.meteors = S.meteors.filter(m => !m.done);
    // 这一波打完了？
    if (!S.result && !S.banner && S.wave < WAVES && !S.queue.length && !S.mons.length && !S.meteors.length) startWave();
    if (S.castle <= 0 && !S.result) lose('castle');
    // 冷冻时身边飘雪花
    if (now < S.frozenUntil && Math.random() < 0.15) fx.burst(L.heroX + rand(-30, 30), L.heroY - rand(20, 100), {n: 1, colors: ['#ffffff', '#bfe9ff'], speed: 30, size: 3.5, shape: 'star', grav: 40, life: 0.9});
    hudTick();
  }

  function damageCastle(n, x) {
    if (S.result) return;
    S.castle = Math.max(0, S.castle - n);
    S.castleShake = 0.4; S.castleFlash = 0.25;
    fx.shake(5, 0.2); fx.flash('#ff4d6d', 0.08);
    fx.text(L.cx + 30, L.GY - 150, `-${n} ❤`, {color: '#ff4d6d', size: 30});
    fx.burst(x || L.gate, L.GY - 40, {n: 12, colors: ['#ffffff', '#d9c7f5', '#ff8fab'], speed: 220, size: 6, grav: 500});
    Play.sfx.play('hurt');
    hud();
  }

  // 怪兽在画面上的位置
  function place(m) {
    const p = m.p;
    m.x = lerp(L.spawn, L.gate + 24, p); m.y = L.GY;
    if (m.attack) { const k = Math.min(1, m.attack / 0.35); m.x -= 30 * k; m.y -= Math.sin(k * Math.PI) * 40; }
    else if (m.kind === 'slime' && !m.dying) m.y -= Math.abs(Math.sin((S.t + m.seed) * 5)) * 7;
  }
  function placeBoss() {
    const B = S.boss;
    L.bossS = clamp(0.62 + 0.18 * (L.WH - 450) / 200, 0.62, 0.8);
    B.x = lerp(L.WW + 70 * L.bossS, L.gate + 120 * L.bossS, B.p);
    B.y = L.GY - Math.sin(B.jump * Math.PI) * 26;
  }

  // ---------------------------------------------------------------- 画
  function drawScene(c, theme) {
    const {WW, WH, GY} = L;
    Play.scene.sky(c, WW, WH, theme);
    if (theme === 'night') {   // 月亮
      c.fillStyle = '#fff6c9'; c.beginPath(); c.arc(WW * 0.78, 80, 30, 0, Math.PI * 2); c.fill();
      c.fillStyle = Play.scene.themes.night.sky[0]; c.beginPath(); c.arc(WW * 0.78 + 12, 72, 26, 0, Math.PI * 2); c.fill();
    } else {                  // 太阳
      const g = c.createRadialGradient(WW * 0.7, GY - 150, 10, WW * 0.7, GY - 150, 110);
      g.addColorStop(0, 'rgba(255,240,180,1)'); g.addColorStop(0.35, 'rgba(255,210,120,.9)'); g.addColorStop(1, 'rgba(255,200,140,0)');
      c.fillStyle = g; c.beginPath(); c.arc(WW * 0.7, GY - 150, 110, 0, Math.PI * 2); c.fill();
    }
    Play.scene.drawClouds(c, WW, theme, S ? S.t : 0, 0);
    Play.scene.hills(c, WW, WH, GY, theme, 0);
    Play.scene.ground(c, WW, WH, GY, theme, 0);
    // 路边的小花
    for (let i = 0; i < 9; i++) {
      const x = L.gate + 40 + ((i * 173) % Math.max(200, WW - L.gate - 60)), y = GY + 22 + (i * 37) % 26;
      c.fillStyle = ['#fff', '#ffd23f', '#ff8fab'][i % 3];
      for (let j = 0; j < 5; j++) { const a = j * 1.256; c.beginPath(); c.arc(x + Math.cos(a) * 4, y + Math.sin(a) * 4, 3, 0, Math.PI * 2); c.fill(); }
      c.fillStyle = '#ffb703'; c.beginPath(); c.arc(x, y, 2.4, 0, Math.PI * 2); c.fill();
    }
  }

  function render() {
    stage.begin();
    if (!S) return;
    const k = L.k, t = S.t;
    ctx.save(); ctx.scale(k, k);
    const [ox, oy] = fx.offset(); ctx.translate(ox, oy);
    // 背景：最后一波淡入夜晚
    if (S.nightT >= 1) drawScene(ctx, 'night');
    else {
      drawScene(ctx, 'sunset');
      if (S.nightT > 0) {
        if (!night) night = document.createElement('canvas');
        if (night.width !== stage.canvas.width || night.height !== stage.canvas.height) { night.width = stage.canvas.width; night.height = stage.canvas.height; }
        const nc = night.getContext('2d');
        nc.setTransform(stage.scale * k, 0, 0, stage.scale * k, 0, 0); nc.clearRect(0, 0, L.WW, L.WH);
        drawScene(nc, 'night');
        ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = S.nightT; ctx.drawImage(night, 0, 0); ctx.restore();
      }
    }
    // 城堡（挨打会抖、闪红）
    const shk = S.castleShake > 0 ? Math.sin(t * 70) * 5 * S.castleShake / 0.4 : 0;
    ctx.save(); ctx.translate(shk, 0);
    Art.castle(ctx, L.cx, L.GY, L.cs, S.castle / CASTLE_HP, t);
    if (S.castleFlash > 0) {
      ctx.globalAlpha = S.castleFlash * 2; ctx.fillStyle = '#ff4d6d';
      ctx.fillRect(L.cx - 66 * L.cs, L.GY - 110 * L.cs, 132 * L.cs, 110 * L.cs); ctx.globalAlpha = 1;
    }
    if (S.castle <= 3 && S.mode === 'play') {   // 冒烟
      if (Math.random() < 0.08) fx.burst(L.cx + rand(-40, 40), L.GY - 110 * L.cs, {n: 1, colors: ['rgba(90,80,110,.45)'], speed: 40, dir: -Math.PI / 2, spread: 0.6, grav: -40, size: 10, life: 1.4});
    }
    // 小勇士站在城墙上
    const now = performance.now();
    let pose = 'charge';
    if (S.mode === 'preview') pose = 'idle';
    else if (S.result === 'win') pose = 'win';
    else if (S.result === 'lose') pose = 'lose';
    else if (now < S.frozenUntil) pose = 'freeze';
    else if (S.castT > 0) pose = 'punch';
    const r = Art.hero(ctx, L.heroX, L.heroY, {avatar: sh.avatar, pose, t, scale: 0.7, facing: 1, seed: 3});
    L.hand = r.hand;
    ctx.restore();
    // 冷冻倒计时
    if (pose === 'freeze') {
      const left = Math.ceil((S.frozenUntil - now) / 1000);
      bubble(L.heroX, L.heroY - 98, `❄️ ${left}`, '#4d96ff');
    }
    // 怪兽（远的先画）
    const B = S.boss;
    if (B) drawBoss(B, t);
    const list = S.mons.slice().sort((a, b) => a.p - b.p);
    for (const m of list) drawMon(m, t);
    // 魔法弹
    for (const b of S.bolts) {
      const big = b.power > 1;
      ctx.save(); ctx.globalAlpha = 0.5; ctx.strokeStyle = big ? '#ff8fab' : '#ffd23f'; ctx.lineWidth = big ? 9 : 6; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(b.x, b.y); ctx.lineTo(b.x - Math.cos(b.ang || 0) * 26, b.y - Math.sin(b.ang || 0) * 26); ctx.stroke(); ctx.restore();
      Art.orb(ctx, b.x, b.y, big ? 13 : 9, big ? '#ff6fa3' : '#ffd23f', t);
    }
    // 流星
    for (const m of S.meteors) {
      if (m.t < 0 || m.tx === undefined) continue;
      const kk = ease.inCubic(Math.min(1, m.t / m.dur)) * 0.7 + Math.min(1, m.t / m.dur) * 0.3;
      const sx = m.tx + m.ox, sy = m.oy, x = lerp(sx, m.tx, kk), y = lerp(sy, m.ty, kk);
      const dx = sx - m.tx, dy = sy - m.ty, dl = Math.hypot(dx, dy) || 1, ex = x + dx / dl * 110, ey = y + dy / dl * 110;
      const g = ctx.createLinearGradient(x, y, ex, ey);
      g.addColorStop(0, 'rgba(255,240,170,.95)'); g.addColorStop(1, 'rgba(255,140,200,0)');
      ctx.strokeStyle = g; ctx.lineWidth = 12; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(ex, ey); ctx.stroke();
      Art.orb(ctx, x, y, 12, '#ff9f1c', t);
    }
    fx.draw(ctx);
    ctx.restore();
    // 屏幕坐标：波次横幅、闪光
    if (S.banner) drawBanner(S.banner);
    fx.drawFlash(ctx, stage.W, stage.H);
  }

  function drawMon(m, t) {
    let sx = 1, sy = 1, alpha = 1;
    if (m.dying) { const k = Math.min(1, m.dying / 0.45); sx = 1 + k * 0.5; sy = 1 - k; alpha = 1 - k; }
    else if (m.hurt > 0) { const k = m.hurt / 0.3; sx = 1 + 0.2 * k; sy = 1 - 0.2 * k; }
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(m.x, m.y); ctx.scale(sx, sy);
    Art.monster(ctx, 0, 0, {kind: m.kind, t: t + m.seed, scale: 1, hurt: m.hurt, color: m.color, facing: -1});
    ctx.restore();
    if (m.max > 1 && !m.dying) hpPips(m.x, m.y - (m.kind === 'ghost' ? 82 : 70), m.hp, m.max);
  }
  function drawBoss(B, t) {
    const s = L.bossS;
    let sx = 1, sy = 1, alpha = 1;
    if (B.dying) { const k = Math.min(1, B.dying / 1.2); sx = 1 + k * 0.3; sy = 1 - k; alpha = 1 - k; }
    else if (B.hurt > 0) { const k = B.hurt / 0.3; sx = 1 + 0.08 * k; sy = 1 - 0.08 * k; }
    if (alpha <= 0) return;
    ctx.save(); ctx.globalAlpha = alpha; ctx.translate(B.x, B.y); ctx.scale(sx, sy);
    Art.monster(ctx, 0, 0, {kind: 'boss', t, scale: s, hurt: B.hurt, facing: -1});
    ctx.restore();
    if (!B.dying) {   // 头顶血条
      const w = 120, x = B.x - 22 * s - w / 2, y = B.y - 200 * s;
      ctx.fillStyle = '#2b2340'; Play.roundRect(ctx, x - 3, y - 3, w + 6, 16, 8); ctx.fill();
      ctx.fillStyle = '#ffe3ea'; Play.roundRect(ctx, x, y, w, 10, 5); ctx.fill();
      ctx.fillStyle = '#ff4d6d'; Play.roundRect(ctx, x, y, Math.max(0.01, w * B.hp / B.max), 10, 5); ctx.fill();
    }
  }
  function hpPips(x, y, hp, max) {
    for (let i = 0; i < max; i++) {
      const px = x + (i - (max - 1) / 2) * 16;
      ctx.fillStyle = '#2b2340'; ctx.beginPath(); ctx.arc(px, y, 7, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = i < hp ? '#ff4d6d' : '#ffe3ea'; ctx.beginPath(); ctx.arc(px, y, 4.5, 0, Math.PI * 2); ctx.fill();
    }
  }
  function bubble(x, y, str, color) {
    ctx.font = '900 18px "Baloo 2", "PingFang SC", system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const w = ctx.measureText(str).width + 18;
    ctx.fillStyle = '#fff'; ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 3;
    Play.roundRect(ctx, x - w / 2, y - 14, w, 28, 14); ctx.fill(); ctx.stroke();
    ctx.fillStyle = color; ctx.fillText(str, x, y + 1);
  }
  function drawBanner(b) {
    const W = stage.W, H = stage.H, k = b.t / b.life;
    const pop = b.t < 0.35 ? ease.outBack(b.t / 0.35) : 1, out = k > 0.82 ? 1 - (k - 0.82) / 0.18 : 1;
    ctx.save(); ctx.globalAlpha = clamp(out, 0, 1);
    ctx.translate(W / 2, H * 0.38); ctx.scale(pop, pop);
    ctx.fillStyle = 'rgba(43,35,64,.55)'; Play.roundRect(ctx, -210, -52, 420, 104, 30); ctx.fill();
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
    ctx.font = '900 52px "Baloo 2", "PingFang SC", system-ui, sans-serif';
    ctx.lineWidth = 8; ctx.strokeStyle = '#2b2340'; ctx.strokeText(b.title, 0, -12);
    ctx.fillStyle = S.wave === WAVES ? '#ff8fab' : '#ffd23f'; ctx.fillText(b.title, 0, -12);
    ctx.font = '800 20px "Baloo 2", "PingFang SC", system-ui, sans-serif'; ctx.fillStyle = '#fff'; ctx.fillText(b.sub, 0, 30);
    ctx.restore();
  }

  // ---------------------------------------------------------------- HUD（HTML，叠在画面上）
  function buildHud() {
    sh.hud.innerHTML = `<div class="df-hud">
      <div class="df-l"><span class="df-chip df-wave">🌊 <b>1</b><small>/${WAVES}</small></span><span class="df-chip df-time">⏱ <b>4:00</b></span></div>
      <div class="df-c"><div class="df-boss" hidden><span>🐉</span><i><b></b></i></div></div>
      <div class="df-r"><span class="df-chip df-castle">🏰 <i><b></b></i><em>10</em></span><span class="df-chip df-combo" hidden>🔥 <b>0</b></span></div></div>`;
    const q = s => sh.hud.querySelector(s);
    hudEl = {wave: q('.df-wave b'), time: q('.df-time b'), timeChip: q('.df-time'), boss: q('.df-boss'), bossBar: q('.df-boss b'), castle: q('.df-castle b'), castleN: q('.df-castle em'),
             castleChip: q('.df-castle'), combo: q('.df-combo'), comboN: q('.df-combo b'), last: {}};
  }
  function setIf(key, v, fn) { if (hudEl.last[key] !== v) { hudEl.last[key] = v; fn(v); } }
  function hud() {
    if (!S || S.mode !== 'play' || !hudEl.wave) return;
    setIf('wave', Math.max(1, S.wave), v => { hudEl.wave.textContent = v; });
    setIf('castle', S.castle, v => {
      hudEl.castle.style.width = (v / CASTLE_HP * 100) + '%'; hudEl.castleN.textContent = v;
      hudEl.castleChip.classList.toggle('low', v <= 3);
      hudEl.castleChip.classList.remove('bump'); void hudEl.castleChip.offsetWidth; hudEl.castleChip.classList.add('bump');
    });
    setIf('combo', S.streak, v => {
      hudEl.combo.hidden = v < 2; hudEl.comboN.textContent = v;
      hudEl.combo.classList.toggle('hot', v >= 3);
      hudEl.combo.classList.remove('bump'); void hudEl.combo.offsetWidth; hudEl.combo.classList.add('bump');
    });
    const B = S.boss;
    setIf('boss', B && !B.dying ? Math.max(0, B.hp) + '/' + B.max : '', v => {
      hudEl.boss.hidden = !v; if (v) hudEl.bossBar.style.width = (B.hp / B.max * 100) + '%';
    });
  }
  function hudTick() {
    if (!hudEl.time || S.mode !== 'play') return;
    const left = Math.max(0, Math.ceil((S.deadline - performance.now()) / 1000));
    setIf('time', left, v => { hudEl.time.textContent = `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`; hudEl.timeChip.classList.toggle('low', v <= 20); });
  }

  // ---------------------------------------------------------------- 外壳
  PlayShell.init({
    game: 'defense', dock: true, tempo: 120,
    rules: [
      '小怪兽一波波来攻城堡啦！<b>答对一题</b>，城墙上的你就放出一颗<b>星星魔法</b>。',
      '连对越多星星越强；<b>连对 3 题</b>召唤「<b>流星雨</b>」，砸中全场怪兽！',
      '答错会被<b>冻住</b>几秒，不能放魔法。看清题目再答，稳稳的最厉害。',
      '守住 5 波小怪，第 6 波打败<b>大怪兽</b>就赢！怪兽的速度会跟着你的答题节奏走。',
    ],
    keys: '电脑键盘：1–4 选答案，Enter 确定',
    setup(shell) {
      sh = shell; sh.avatar = shell.data.avatar || 'mint';
      stage = Play.stage(shell.canvas, {height: 450, minW: 480});
      ctx = stage.ctx;
      layout();
      stage.onResize = () => layout();
      S = previewState();
      loop = Play.loop(update, render);
      loop.onHide = () => loop.pause();
      document.addEventListener('visibilitychange', () => { if (!document.hidden) loop.resume(); });
      loop.start();
    },
    begin(match, q, shell) {
      quiz = q; sh.avatar = match.avatar || sh.avatar;
      fx = new Play.FX();
      layout();
      S = newState('play');
      S.deadline = performance.now() + Math.min(MAX_S, Math.max(10, match.seconds_left || MAX_S)) * 1000;
      S.readyAt = performance.now() + 300;
      buildHud();
      startWave();
      hud();
      loop.start();
    },
    onAnswer(res) { onAnswer(res); },
    onXP(res) { if (S && S.mode === 'play' && !S.result) onXP(res); },
  });
})();

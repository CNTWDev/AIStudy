/* 闪电赛跑：答对一题冲刺一段，和半透明的「上次的我」赛跑，先到终点就赢。
   答题面板常驻在画面下方（dock），答题就是操作；出题、判分、冷冻都在服务器（app/arena/）。
   跑法：一直慢跑 JOG 米/秒（不答题也会慢慢往前挪）；答对一题，把一段冲刺距离放进「冲刺池」，
   池子按比例放出（先快后慢，约 2 秒放完）；连对越多冲得越远，每连对 3 题是彩虹超级冲刺。答错 = 冰冻。
   调参（模拟过）：答对 80%、每题 6–8 秒 ≈ 94–106 秒跑完 1000 米；60% 答对 ≈ 140 秒；一题不答 180 秒也跑不完。
   每个孩子的题都按自己的水平出（目标答对率约 80%），所以快慢靠专注，不靠会的多。 */
(function () {
  const LEN = 1000;          // 赛道长度（米）
  const PX = 12;             // 1 米 = 12 个逻辑像素
  const JOG = 4;             // 慢跑速度（米/秒）
  const RELEASE = 1.4;       // 冲刺池每秒放出的比例
  const MAX_EXTRA = 45;      // 冲刺额外速度上限（米/秒）
  const HARD_CAP = 180;      // 一局最长秒数
  const GHOST_FIRST = 100;   // 第一次跑：影子小人 100 秒跑完
  const burstFor = (streak, special) => 50 + 8 * Math.min(streak - 1, 5) + (special ? 40 : 0);

  const GY = 300, MID = 356, BOT = 414;          // 赛道上沿、中线、下沿
  const LANE_G = 350, LANE_ME = 404;             // 影子、自己脚底的 y
  const RAINBOW = ['#ff5d5d', '#ff9f1c', '#ffd23f', '#5cc96b', '#4d96ff', '#9b5de5'];
  const BUNT = ['#ff6fa3', '#ffd23f', '#4d96ff', '#5cc96b', '#ff9f1c'];
  const INK = '#2b2340';
  const FONT = '"Baloo 2", "PingFang SC", system-ui, sans-serif';

  const info = JSON.parse((document.getElementById('pl') || {dataset: {}}).dataset.info || '{}');
  const lastS = info.ghost && info.ghost.finish_s;
  const ghostLabel = lastS ? '上次的我' : '影子小人';

  let sh = null, stage = null, ctx = null, loop = null, hud = null, quiz = null;
  const fx = new Play.FX();
  let S = null;            // 这一局的状态
  let clock = 0;           // 一直走的动画时钟（预览也动）
  let cloudOff = 0, clouds = null, lines = [];

  function fresh(avatar) {
    return {mode: 'preview', avatar: avatar || info.avatar || 'mint', t: 0, d: 0, pool: 0, extra: 0, frozen: 0, freezeMax: 1,
            ghostD: 0, ghostV: LEN / GHOST_FIRST, animT: 0, gAnimT: 0, streak: 0, best: 0, specialT: 0, stumble: 0,
            limit: HARD_CAP, wall0: 0, endT: 0, result: '', runnerX: 0, lead: 0, nextMark: 100, finishAt: 0, strideP: 0,
            confT: 0, sent: false, glow: 0};
  }

  // ------------------------------------------------------------ 更新
  function update(dt) {
    clock += dt;
    fx.update(dt);
    const s = S;
    if (s.mode === 'run' || s.mode === 'finish') s.t += dt;
    let v = 0;   // 这一帧自己跑的速度（米/秒）
    if (s.mode === 'run') {
      if (s.frozen > 0) { s.frozen = Math.max(0, s.frozen - dt); s.extra = 0; }
      else {
        s.extra = Math.min(s.pool * RELEASE, MAX_EXTRA);
        s.pool = Math.max(0, s.pool - s.extra * dt);
        v = JOG + s.extra;
      }
      s.d = Math.min(LEN, s.d + v * dt);
      if (s.d >= s.nextMark && s.nextMark < LEN) {   // 每 100 米叮一声
        fx.text(s.runnerX, LANE_ME - 150, s.nextMark + ' 米', {color: '#4d96ff', size: 22, rise: 40, life: 0.9});
        Play.sfx.play('coin'); s.nextMark += 100;
      }
      if (s.d >= LEN) crossLine();
      const wall = (performance.now() - s.wall0) / 1000;
      if (s.mode === 'run' && (s.t >= s.limit || wall >= s.limit)) timeUp();
    } else if (s.mode === 'finish') {   // 冲过终点：减速，再欢呼
      s.extra *= Math.pow(0.08, dt);
      v = Math.max(0, (JOG + s.extra) * Math.max(0, 1 - (s.t - s.finishAt) / 1.1));
      s.d += v * dt;
      s.confT -= dt;
      if (s.confT <= 0 && s.t - s.finishAt < 2.2) { confetti(); s.confT = 0.55; }
      if (!s.sent && s.t - s.finishAt > 2.2) { s.sent = true; report(); }
    }
    // 影子：匀速跑到终点
    if (s.mode === 'run' || s.mode === 'finish') {
      const gv = s.ghostD < LEN ? s.ghostV : Math.max(0, s.ghostV * (1 - (s.ghostD - LEN) / 25));
      s.ghostD = Math.min(LEN + 25, s.ghostD + gv * dt);
      s.gAnimT += dt * (0.5 + gv / 14);
      // 超过 / 被追上的提示（带一点缓冲，不来回闪）
      if (s.mode === 'run') {
        const gap = s.d - s.ghostD;
        if (s.lead <= 0 && gap > 3) { s.lead = 1; if (s.t > 2) { sh.toast('⚡ 超过' + ghostLabel + '啦！', 'good'); Play.sfx.play('star'); } }
        else if (s.lead >= 0 && gap < -3) s.lead = -1;
      }
    }
    // 跑步动画：越快腿越快
    const running = s.mode === 'run' && s.frozen <= 0 || (s.mode === 'finish' && v > 0.5);
    s.animT += dt * (running ? 0.62 + Math.min(1.7, (v - JOG) / 22) : 1);
    s.specialT = Math.max(0, s.specialT - dt);
    s.stumble = Math.max(0, s.stumble - dt);
    s.glow = Math.max(0, s.glow - dt);
    // 自己在屏幕上的位置：冲刺时往前挪一点，更有速度感
    const W = stage.W, want = W * (W < 760 ? 0.3 : 0.32) + Math.min(1, s.extra / MAX_EXTRA) * W * 0.09;
    s.runnerX = s.runnerX ? Play.lerp(s.runnerX, want, Math.min(1, dt * 3)) : want;
    // 脚下扬尘
    if (running) {
      const ph = Math.floor(s.animT * 16 / Math.PI);
      if (ph !== s.strideP) {
        s.strideP = ph;
        const n0 = fx.parts.length, fast = v > JOG + 6;
        fx.burst(s.runnerX - 8, LANE_ME - 2, {n: fast ? 3 : 1, colors: ['#fff6ea', '#f6d9bf', '#ffffff'], speed: fast ? 90 : 40,
          life: 0.55, size: fast ? 7 : 5, grav: -50, dir: -Math.PI * 0.85, spread: 0.7});
        for (let i = n0; i < fx.parts.length; i++) fx.parts[i].vx -= v * PX * 0.5;
      }
      if (s.specialT > 0 && Math.random() < 0.5) {   // 彩虹冲刺撒星星
        const n0 = fx.parts.length;
        fx.burst(s.runnerX - 30, LANE_ME - 50 - Math.random() * 50, {n: 1, colors: RAINBOW, shape: 'star', speed: 60, size: 6, grav: 0, life: 0.7});
        for (let i = n0; i < fx.parts.length; i++) fx.parts[i].vx -= v * PX * 0.6;
      }
    }
    // 速度线
    const sp = s.mode === 'run' ? s.extra / MAX_EXTRA : 0;
    if (sp > 0.12 && Math.random() < sp * 1.6) lines.push({x: W + 40, y: Play.rand(30, BOT + 20), len: Play.rand(60, 180), v: Play.rand(1400, 2200), w: Play.rand(2, 4)});
    for (const l of lines) l.x -= l.v * dt;
    lines = lines.filter(l => l.x + l.len > -10);
    cloudOff += dt * (10 + v * PX * 0.04);
    updateHud();
  }

  function crossLine() {
    const s = S;
    s.d = LEN; s.mode = 'finish'; s.finishAt = s.t; s.endT = s.t;
    lockQuiz();
    const ghostTime = LEN / s.ghostV;
    s.result = s.t < ghostTime ? 'win' : 'lose';
    s.extra = Math.max(s.extra, 12);
    fx.flash('#fff', 0.15); fx.shake(5, 0.3);
    fx.text(s.runnerX, LANE_ME - 170, s.result === 'win' ? '🏆 第一名！' : '🏁 到终点啦！', {color: s.result === 'win' ? '#ff7a00' : '#7b5cff', size: 38, life: 2, rise: 50});
    Play.sfx.play(s.result === 'win' ? 'crit' : 'star');
    confetti(); confetti();
    s.confT = 0.5;
  }

  function timeUp() {
    const s = S;
    s.mode = 'done'; s.endT = s.t;
    lockQuiz();
    s.result = s.d >= s.ghostD ? 'win' : 'lose';
    sh.toast('⏰ 时间到！', 'warn');
    setTimeout(report, 900);
  }

  function lockQuiz() {   // 冲过终点 / 时间到：答题面板停住，不再出新题（欢呼的这两秒也不能答）
    if (!quiz) return;
    quiz.stop();
    quiz.el.classList.add('wait');
  }

  function report() {
    const s = S;
    const stats = {distance: Math.round(Math.min(LEN, s.d)), combo: s.best};
    let title;
    if (s.mode === 'finish' || s.d >= LEN) {
      stats.finish_s = Math.max(1, Math.round(s.finishAt));
      title = s.result === 'win' ? `⚡ 跑赢了${ghostLabel}！<small class="rc-sub">${stats.finish_s} 秒跑完 ${LEN} 米</small>`
        : `💪 差一点，下次再快一点<small class="rc-sub">${stats.finish_s} 秒跑完，${ghostLabel}用了 ${Math.round(LEN / s.ghostV)} 秒</small>`;
    } else {
      title = s.result === 'win' ? `⏰ 时间到，你领先！<small class="rc-sub">跑了 ${stats.distance} 米</small>`
        : `⏰ 时间到！下次答快一点<small class="rc-sub">跑了 ${stats.distance} 米</small>`;
    }
    sh.finish(s.result, stats, title);
  }

  function confetti() {
    const W = stage.W;
    for (let i = 0; i < 4; i++) {
      fx.burst(Play.rand(W * 0.1, W * 0.9), -10, {n: 16, colors: ['#ffd23f', '#ff6fa3', '#4d96ff', '#5cc96b', '#ff9f1c', '#9b5de5'],
        shape: 'confetti', speed: 260, grav: 380, life: 2.2, size: 12, dir: Math.PI / 2, spread: 2.2});
    }
  }

  // ------------------------------------------------------------ 答题 → 跑
  function onAnswer(res) {
    const s = S;
    if (!s || s.mode !== 'run') return;
    const x = s.runnerX, y = LANE_ME;
    if (res.correct) {
      s.streak = res.streak || s.streak + 1;
      s.best = Math.max(s.best, s.streak);
      s.pool += burstFor(s.streak, res.special);
      s.glow = 0.6;
      Play.sfx.play('whoosh');
      fx.ring(x, y - 60, {color: res.special ? '#ffd23f' : '#fff', width: 5, grow: 420});
      fx.burst(x - 20, y - 4, {n: 10, colors: ['#fff', '#fff3d6', '#ffe08a'], speed: 220, dir: Math.PI, spread: 1.1, grav: -80, life: 0.6, size: 7});
      if (res.special) {
        s.specialT = 3;
        fx.shake(8, 0.45); fx.flash('#fffbe0', 0.12);
        fx.burst(x, y - 70, {n: 28, colors: RAINBOW, shape: 'star', speed: 340, size: 9, grav: 200, life: 1});
        fx.text(x + 20, y - 170, '🌈 超级冲刺！', {color: '#ff3d7f', size: 36, life: 1.3});
        Play.sfx.play('power');
      } else {
        fx.text(x + 10, y - 160, s.streak >= 2 ? `🔥 连对 ${s.streak}！` : Play.pick(['冲呀！', '加速！', '嗖——']), {color: '#ff7a00', size: 28});
      }
    } else {
      s.streak = 0;
      s.frozen = s.freezeMax = Math.max(1, (res.freeze_ms || 5000) / 1000);
      s.stumble = 0.5; s.specialT = 0; s.pool *= 0.5;
      fx.burst(x, y - 70, {n: 18, colors: ['#ffffff', '#cdeeff', '#8fd3ff'], shape: 'star', speed: 240, size: 6, grav: 420, life: 0.8});
      fx.text(x, y - 175, '❄️ 冻住了！', {color: '#2f8fdc', size: 28});
      fx.shake(3, 0.2);
    }
  }
  function onXP(res) {
    if (res.crit && S && S.mode === 'run') fx.text(S.runnerX + 60, LANE_ME - 130, '暴击 ×2！', {color: '#ff3d7f', size: 24});
  }

  // ------------------------------------------------------------ 画面
  function render() {
    const W = stage.W, H = stage.H, s = S;
    stage.begin();
    const cam = s.d * PX - (s.runnerX || W * 0.32);
    const sx = m => m * PX - cam;
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    drawSky(W, H);
    Play.scene.hills(ctx, W, H, GY + 6, 'day', cam * 0.5);
    drawTrees(W, cam);
    drawFence(W, cam, sx);
    drawTrack(W, cam, sx);
    drawGate(sx(LEN), false);
    drawGhost(sx(s.ghostD), LANE_G, s);
    drawMe(s);
    drawGate(sx(LEN), true);
    drawFrontGrass(W, H, cam);
    fx.draw(ctx);
    drawLines();
    ctx.restore();
    drawGhostArrow(W, sx(s.ghostD), s);
    if (s.mode === 'run' && s.frozen > 0) {   // 冰冻时四周结霜
      const k = Math.min(1, s.frozen / 0.4) * 0.55;
      const g = ctx.createRadialGradient(W / 2, H / 2, H * 0.35, W / 2, H / 2, W * 0.7);
      g.addColorStop(0, 'rgba(200,235,255,0)'); g.addColorStop(1, `rgba(200,235,255,${k})`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }
    fx.drawFlash(ctx, W, H);
  }

  function drawSky(W, H) {
    Play.scene.sky(ctx, W, H, 'day');
    // 笑眯眯的太阳
    const x = W - (W < 760 ? 70 : 110), y = W < 760 ? 150 : 140, r = W < 760 ? 26 : 32;
    ctx.save(); ctx.translate(x, y); ctx.rotate(clock * 0.3);
    ctx.fillStyle = 'rgba(255,214,90,.5)';
    for (let i = 0; i < 12; i++) { ctx.rotate(Math.PI / 6); ctx.beginPath(); ctx.moveTo(r + 6, -6); ctx.lineTo(r + 22, 0); ctx.lineTo(r + 6, 6); ctx.fill(); }
    ctx.restore();
    ctx.fillStyle = '#ffd23f'; ctx.strokeStyle = '#f5a623'; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.lineCap = 'round';
    for (const d of [-1, 1]) { ctx.beginPath(); ctx.arc(x + d * 11, y - 3, 5, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke(); }
    ctx.beginPath(); ctx.arc(x, y + 6, 9, Math.PI * 0.15, Math.PI * 0.85); ctx.stroke();
    ctx.fillStyle = 'rgba(255,120,140,.5)'; ctx.beginPath(); ctx.ellipse(x - 20, y + 8, 5, 3, 0, 0, Math.PI * 2); ctx.ellipse(x + 20, y + 8, 5, 3, 0, 0, Math.PI * 2); ctx.fill();
    // 云
    if (!clouds) clouds = Array.from({length: 7}, (_, i) => ({x: i * 230 + Play.rand(0, 100), y: Play.rand(40, 170), s: Play.rand(0.55, 1.1), k: Play.rand(0.5, 1)}));
    const span = W + 320;
    ctx.globalAlpha = 0.92;
    for (const c of clouds) {
      const x0 = (((c.x - cloudOff * c.k) % span) + span) % span - 160;
      Play.scene.cloud(ctx, x0, c.y, c.s, '#fff');
    }
    ctx.globalAlpha = 1;
  }

  function drawTrees(W, cam) {   // 远处一排圆圆的树（视差 0.7）
    const k = 0.7, gap = 150, off = cam * k;
    const i0 = Math.floor((off - 80) / gap), i1 = Math.ceil((off + W + 80) / gap);
    for (let i = i0; i <= i1; i++) {
      const h = Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1;   // 每棵树固定的随机数
      const x = i * gap + h * 60 - off, base = GY - 2, sz = 26 + h * 18;
      if (h < 0.25) continue;
      ctx.fillStyle = '#a8724a'; ctx.fillRect(x - 4, base - sz - 10, 8, sz + 10);
      ctx.fillStyle = h > 0.6 ? '#58b86a' : '#6ccb7a';
      ctx.beginPath(); ctx.arc(x, base - sz - 18, sz, 0, Math.PI * 2); ctx.arc(x - sz * 0.6, base - sz - 6, sz * 0.7, 0, Math.PI * 2); ctx.arc(x + sz * 0.6, base - sz - 6, sz * 0.7, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,.22)'; ctx.beginPath(); ctx.arc(x - sz * 0.35, base - sz - 28, sz * 0.35, 0, Math.PI * 2); ctx.fill();
      if (h > 0.8) { ctx.fillStyle = '#ff6b81'; for (let j = 0; j < 3; j++) { ctx.beginPath(); ctx.arc(x - 10 + j * 10, base - sz - 14 - (j % 2) * 12, 3.5, 0, Math.PI * 2); ctx.fill(); } }
    }
    // 赛道后面的草地
    ctx.fillStyle = '#86d77f'; ctx.fillRect(0, GY - 8, W, 12);
  }

  function drawFence(W, cam, sx) {   // 赛道后沿的栏杆 + 彩旗 + 每 100 米的路牌
    const gap = 96, top = GY - 30;
    const i0 = Math.floor((cam - 40) / gap), i1 = Math.ceil((cam + W + 40) / gap);
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(0, top + 8); ctx.lineTo(W, top + 8); ctx.stroke();
    for (let i = i0; i <= i1; i++) {
      const x = i * gap - cam;
      ctx.fillStyle = '#fff'; ctx.strokeStyle = 'rgba(43,35,64,.35)'; ctx.lineWidth = 2;
      Play.roundRect(ctx, x - 3, top, 6, 30, 3); ctx.fill(); ctx.stroke();
      // 彩旗绳
      const x2 = x + gap, sag = 14, ty = top - 20;
      ctx.strokeStyle = 'rgba(43,35,64,.5)'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x, ty); ctx.quadraticCurveTo(x + gap / 2, ty + sag * 2, x2, ty); ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.fillRect(x - 1.5, ty, 3, 20);
      for (let j = 1; j < 6; j++) {
        const u = j / 6, px = x + gap * u, py = ty + sag * 4 * u * (1 - u), wob = Math.sin(clock * 4 + i + j) * 1.5;
        ctx.fillStyle = BUNT[(i * 5 + j) % BUNT.length];
        ctx.beginPath(); ctx.moveTo(px - 6, py); ctx.lineTo(px + 6, py); ctx.lineTo(px + wob, py + 13); ctx.closePath(); ctx.fill();
      }
    }
    // 路牌：每 100 米
    for (let m = 100; m < LEN; m += 100) {
      const x = sx(m);
      if (x < -60 || x > W + 60) continue;
      ctx.fillStyle = '#8a5a3b'; ctx.fillRect(x - 3, GY - 70, 6, 66);
      ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      Play.roundRect(ctx, x - 34, GY - 98, 68, 34, 10); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.font = `900 19px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(m + ' 米', x, GY - 80);
    }
  }

  function drawTrack(W, cam, sx) {
    const g = ctx.createLinearGradient(0, GY, 0, BOT);
    g.addColorStop(0, '#ff9a6b'); g.addColorStop(1, '#f2774d');
    ctx.fillStyle = g; ctx.fillRect(0, GY, W, BOT - GY);
    // 跑道纹理
    ctx.fillStyle = 'rgba(255,255,255,.12)';
    const t0 = ((cam % 64) + 64) % 64;
    for (let x = -t0; x < W + 64; x += 64) { ctx.fillRect(x, GY + 16, 18, 3); ctx.fillRect(x + 30, GY + 74, 22, 3); ctx.fillRect(x + 12, BOT - 12, 14, 3); }
    // 白色跑道线
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, GY, W, 4); ctx.fillRect(0, BOT - 4, W, 4);
    const dash = ((cam % 40) + 40) % 40;
    for (let x = -dash; x < W; x += 40) ctx.fillRect(x, MID - 2, 24, 4);
    // 每 10 米一个小刻度，每 100 米一条横线 + 地上的数字
    const m0 = Math.max(0, Math.floor((cam - 20) / PX / 10) * 10), m1 = Math.min(LEN, (cam + W + 20) / PX);
    for (let m = m0; m <= m1; m += 10) {
      const x = sx(m);
      ctx.fillStyle = 'rgba(255,255,255,.75)';
      if (m % 100 === 0 && m > 0 && m < LEN) {
        ctx.fillRect(x - 2, GY, 4, BOT - GY);
        ctx.save(); ctx.globalAlpha = 0.6; ctx.font = `900 22px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(String(m), x + 30, BOT - 22); ctx.restore();
      } else ctx.fillRect(x - 1, BOT - 10, 2, 6);
    }
    // 起点线
    const xs = sx(0);
    if (xs > -60 && xs < W + 60) {
      ctx.fillStyle = '#fff'; ctx.fillRect(xs - 4, GY, 8, BOT - GY);
      ctx.save(); ctx.translate(xs - 26, (GY + BOT) / 2); ctx.rotate(-Math.PI / 2);
      ctx.font = `900 20px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.globalAlpha = 0.85; ctx.fillText('起 点', 0, 0); ctx.restore();
    }
    // 终点格子带
    const xf = sx(LEN);
    if (xf > -60 && xf < W + 60) {
      const sq = 10;
      for (let r = 0; r * sq < BOT - GY; r++) for (let c = 0; c < 3; c++) {
        ctx.fillStyle = (r + c) % 2 ? INK : '#fff';
        ctx.fillRect(xf - 15 + c * sq, GY + r * sq, sq, Math.min(sq, BOT - GY - r * sq));
      }
    }
    // 赛道下沿的阴影
    ctx.fillStyle = 'rgba(120,50,30,.25)'; ctx.fillRect(0, BOT, W, 4);
  }

  function flag(x, y, dir) {   // 一面飘动的格子旗
    const w = 34, h = 22, sq = w / 4;
    ctx.save(); ctx.translate(x, y);
    for (let r = 0; r < 3; r++) for (let c = 0; c < 4; c++) {
      const wave = Math.sin(clock * 6 + c * 0.9) * 3 * (c / 4);
      ctx.fillStyle = (r + c) % 2 ? INK : '#fff';
      ctx.fillRect(dir * c * sq - (dir < 0 ? sq : 0), r * h / 3 + wave, sq + 0.5, h / 3 + 0.5);
    }
    ctx.restore();
  }

  function drawGate(x, front) {   // 终点拱门：后面的柱子先画，前面的柱子 + 横幅最后画（人从中间穿过）
    if (x < -160 || x > stage.W + 160) return;
    const topY = 150;
    const pole = (px, py0) => {
      ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      Play.roundRect(ctx, px - 6, topY, 12, py0 - topY, 5); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#ff5d5d'; for (let y = topY + 14; y < py0 - 10; y += 30) ctx.fillRect(px - 4.5, y, 9, 12);
    };
    if (!front) { pole(x + 34, GY + 4); flag(x + 40, topY - 26, 1); return; }
    pole(x - 34, BOT + 6);
    flag(x - 40, topY - 26, -1);
    ctx.fillStyle = INK; ctx.fillRect(x - 40, topY - 26, 3, 30);
    // 横幅
    const bw = 170, bh = 50, by = topY - 6;
    ctx.save(); ctx.translate(x, by + Math.sin(clock * 2) * 2);
    ctx.fillStyle = '#ffd23f'; ctx.strokeStyle = INK; ctx.lineWidth = 4;
    Play.roundRect(ctx, -bw / 2, -bh / 2, bw, bh, 12); ctx.fill(); ctx.stroke();
    ctx.save(); Play.roundRect(ctx, -bw / 2 + 2, -bh / 2 + 2, bw - 4, bh - 4, 10); ctx.clip();
    for (let c = 0; c * 8 < bw; c++) for (const yy of [-bh / 2 + 2, bh / 2 - 10]) { ctx.fillStyle = (c + (yy > 0 ? 1 : 0)) % 2 ? INK : '#fff'; ctx.fillRect(-bw / 2 + c * 8, yy, 8, 8); }
    ctx.restore();
    ctx.font = `900 24px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.lineWidth = 5; ctx.strokeStyle = '#fff'; ctx.strokeText('🏁 终点', 0, 2); ctx.fillStyle = '#e0457b'; ctx.fillText('🏁 终点', 0, 2);
    ctx.restore();
  }

  // 影子：先画到小画布上，染成淡蓝色，再半透明贴上去
  let gOff = null, gSc = 0;
  const GW = 210, GH = 230, GFOOT = 216;
  function drawGhost(x, y, s) {
    if (x < -120 || x > stage.W + 120) return;
    const sc = stage.scale;
    if (!gOff || gSc !== sc) { gOff = document.createElement('canvas'); gOff.width = Math.ceil(GW * sc); gOff.height = Math.ceil(GH * sc); gSc = sc; }
    const g = gOff.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, gOff.width, gOff.height);
    g.setTransform(sc, 0, 0, sc, 0, 0); g.globalCompositeOperation = 'source-over'; g.globalAlpha = 1;
    const moving = (s.mode === 'run' || s.mode === 'finish') && s.ghostD < LEN + 20;
    const pose = moving ? 'run' : s.mode === 'preview' ? 'idle' : 'win';
    Art.hero(g, GW / 2, GFOOT, {avatar: s.avatar, pose, t: moving ? s.gAnimT : clock, scale: 0.86, seed: 3, mood: moving ? 'normal' : 'happy'});
    g.globalCompositeOperation = 'source-atop'; g.fillStyle = 'rgba(140,195,255,.55)'; g.fillRect(0, 0, GW, GH);
    g.globalCompositeOperation = 'source-over';
    ctx.save();
    ctx.globalAlpha = 0.55 + 0.08 * Math.sin(clock * 3);
    ctx.drawImage(gOff, x - GW / 2, y - GFOOT, GW, GH);
    ctx.restore();
    // 闪闪的小星星
    ctx.fillStyle = 'rgba(255,255,255,.85)';
    for (let i = 0; i < 4; i++) {
      const a = clock * 2 + i * 1.6, r = 3 + 2 * Math.sin(clock * 5 + i);
      Play.starPath(ctx, x - 20 + Math.cos(a) * 34 - (moving ? i * 10 : 0), y - 60 + Math.sin(a * 1.3) * 40, Math.max(1, r), a); ctx.fill();
    }
    nameTag(x, y - 140, ghostLabel, 'rgba(255,255,255,.85)', '#4d6fb3');
  }

  function nameTag(x, y, text, bg, fg) {
    ctx.font = `800 15px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const w = ctx.measureText(text).width + 18;
    ctx.fillStyle = bg; ctx.strokeStyle = fg; ctx.lineWidth = 2;
    Play.roundRect(ctx, x - w / 2, y - 12, w, 24, 12); ctx.fill(); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - 5, y + 12); ctx.lineTo(x, y + 18); ctx.lineTo(x + 5, y + 12); ctx.fill();
    ctx.fillStyle = fg; ctx.fillText(text, x, y + 1);
  }

  function drawMe(s) {
    const x = s.runnerX || stage.W * 0.32, y = LANE_ME;
    // 彩虹尾巴
    if (s.specialT > 0) {
      const k = Math.min(1, s.specialT / 0.6), len = 260 * k;
      for (let i = 0; i < RAINBOW.length; i++) {
        const yy = y - 96 + i * 7;
        const g = ctx.createLinearGradient(x - len, 0, x, 0);
        g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(1, RAINBOW[i]);
        ctx.fillStyle = g; ctx.globalAlpha = 0.85;
        ctx.beginPath(); ctx.moveTo(x - 10, yy);
        for (let px = 0; px <= len; px += 20) ctx.lineTo(x - 10 - px, yy + Math.sin(clock * 10 - px / 30) * 5 * (px / len));
        for (let px = len; px >= 0; px -= 20) ctx.lineTo(x - 10 - px, yy + 7 + Math.sin(clock * 10 - px / 30) * 5 * (px / len));
        ctx.closePath(); ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    // 冲刺光晕
    if (s.extra > 8 && s.mode === 'run') {
      const a = Math.min(0.5, s.extra / MAX_EXTRA * 0.6);
      const g = ctx.createRadialGradient(x, y - 60, 10, x, y - 60, 90);
      g.addColorStop(0, `rgba(255,240,150,${a})`); g.addColorStop(1, 'rgba(255,240,150,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y - 60, 90, 0, Math.PI * 2); ctx.fill();
    }
    let pose = 'run', t = s.animT, mood;
    if (s.mode === 'preview') { pose = 'idle'; t = clock; }
    else if (s.mode === 'run' && s.frozen > 0) pose = 'freeze';
    else if (s.mode === 'finish' && s.t - s.finishAt > 1.0) { pose = s.result === 'win' ? 'win' : 'idle'; t = clock; mood = 'happy'; }
    else if (s.mode === 'done') { pose = s.result === 'win' ? 'win' : 'idle'; t = clock; mood = 'happy'; }
    ctx.save();
    if (s.stumble > 0) {   // 答错：往后一个趔趄
      const k = s.stumble / 0.5;
      ctx.translate(x - 16 * Math.sin(k * Math.PI), y); ctx.rotate(-0.18 * Math.sin(k * Math.PI)); ctx.translate(-x, -y);
    }
    Art.hero(ctx, x, y, {avatar: s.avatar, pose, t, scale: 1, seed: 1, mood, lift: pose === 'run' && s.extra > 20 ? 4 : 0});
    ctx.restore();
    if (s.mode === 'run' && s.frozen > 0) {   // 冰块上的倒计时
      ctx.font = `900 22px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#fff'; ctx.strokeStyle = '#2f8fdc'; ctx.lineWidth = 3;
      Play.roundRect(ctx, x - 34, y - 166, 68, 30, 15); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#2f8fdc'; ctx.fillText('❄ ' + Math.ceil(s.frozen), x, y - 150);
    } else if (s.mode !== 'preview') {
      nameTag(x, y - 150 - (pose === 'win' ? 12 : 0), '我', '#ff6fa3', '#fff');
    }
  }

  function drawFrontGrass(W, H, cam) {
    ctx.fillStyle = '#7cd27a'; ctx.fillRect(0, BOT + 4, W, H - BOT);
    ctx.fillStyle = '#6cc46b'; ctx.fillRect(0, H - 10, W, 10);
    const gap = 70, off = cam * 1.15;   // 前景比赛道动得快一点
    const i0 = Math.floor(off / gap) - 1, i1 = Math.ceil((off + W) / gap) + 1;
    for (let i = i0; i <= i1; i++) {
      const h = Math.abs(Math.sin(i * 78.233) * 43758.5453) % 1, x = i * gap + h * 40 - off, y = BOT + 18 + h * 18;
      if (h > 0.55) {   // 小花
        ctx.fillStyle = ['#fff', '#ffd23f', '#ff8fab'][i % 3];
        for (let p = 0; p < 5; p++) { const a = p * Math.PI * 2 / 5; ctx.beginPath(); ctx.arc(x + Math.cos(a) * 4.5, y + Math.sin(a) * 4.5, 3.5, 0, Math.PI * 2); ctx.fill(); }
        ctx.fillStyle = '#ff9f1c'; ctx.beginPath(); ctx.arc(x, y, 2.6, 0, Math.PI * 2); ctx.fill();
      } else {          // 草叶
        ctx.fillStyle = '#5bb85d';
        ctx.beginPath(); ctx.moveTo(x - 6, y + 6); ctx.lineTo(x - 2, y - 8); ctx.lineTo(x, y + 6); ctx.lineTo(x + 3, y - 10); ctx.lineTo(x + 6, y + 6); ctx.fill();
      }
    }
  }

  function drawLines() {
    ctx.lineCap = 'round';
    for (const l of lines) {
      const g = ctx.createLinearGradient(l.x, 0, l.x + l.len, 0);
      g.addColorStop(0, 'rgba(255,255,255,.85)'); g.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.strokeStyle = g; ctx.lineWidth = l.w;
      ctx.beginPath(); ctx.moveTo(l.x, l.y); ctx.lineTo(l.x + l.len, l.y); ctx.stroke();
    }
  }

  function drawGhostArrow(W, gx, s) {   // 影子跑出画面时，在边上提示差多远
    if (s.mode === 'preview' || (gx > -40 && gx < W + 40)) return;
    const ahead = gx > W, gap = Math.abs(Math.round(s.ghostD - s.d));
    const text = ahead ? `${ghostLabel} 领先 ${gap} 米 ▶` : `◀ 甩开${ghostLabel} ${gap} 米`;
    ctx.font = `800 16px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const w = ctx.measureText(text).width + 22, x = ahead ? W - w / 2 - 10 : w / 2 + 10, y = ahead ? GY - 62 : GY - 115;
    ctx.globalAlpha = 0.92;
    ctx.fillStyle = ahead ? '#ffffff' : '#e6fbe9'; ctx.strokeStyle = ahead ? '#4d6fb3' : '#2f9b5e'; ctx.lineWidth = 2.5;
    Play.roundRect(ctx, x - w / 2, y - 15, w, 30, 15); ctx.fill(); ctx.stroke();
    ctx.fillStyle = ahead ? '#4d6fb3' : '#2f9b5e'; ctx.fillText(text, x, y + 1);
    ctx.globalAlpha = 1;
  }

  // ------------------------------------------------------------ HUD（HTML，叠在画面上）
  function buildHud() {
    sh.hud.innerHTML = `<div class="rc-hud">
      <div class="rc-bar"><div class="rc-track"><i class="rc-fill"></i>
        <span class="rc-dot rc-g" title="${ghostLabel}">影</span><span class="rc-dot rc-me" title="我">我</span><span class="rc-flag">🏁</span></div>
        <div class="rc-m"><b class="rc-dist">0</b> / ${LEN} 米</div></div>
      <div class="rc-chips"><span class="rc-chip rc-time">⏱ 0:00</span><span class="rc-chip rc-combo">🔥 0</span></div></div>`;
    const q = c => sh.hud.querySelector(c);
    hud = {fill: q('.rc-fill'), me: q('.rc-me'), g: q('.rc-g'), dist: q('.rc-dist'), time: q('.rc-time'), combo: q('.rc-combo'), last: {}};
    const av = Art.avatar(S.avatar);
    hud.me.style.background = av.main;
  }
  function updateHud() {
    if (!hud || !S) return;
    const s = S, L = hud.last;
    const pm = Math.min(1, s.d / LEN), pg = Math.min(1, s.ghostD / LEN);
    const kp = Math.round(pm * 1000), kg = Math.round(pg * 1000);
    if (L.kp !== kp) { L.kp = kp; hud.me.style.left = (pm * 100) + '%'; hud.fill.style.width = (pm * 100) + '%'; hud.dist.textContent = Math.floor(Math.min(LEN, s.d)); }
    if (L.kg !== kg) { L.kg = kg; hud.g.style.left = (pg * 100) + '%'; }
    const left = Math.max(0, Math.ceil(s.limit - s.t)), sec = Math.floor(s.endT || s.t);
    const tt = `⏱ ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
    if (L.tt !== tt) { L.tt = tt; hud.time.textContent = tt; }
    const warn = s.mode === 'run' && left <= 15;
    if (L.warn !== warn) { L.warn = warn; hud.time.classList.toggle('warn', warn); }
    if (L.st !== s.streak) {
      L.st = s.streak; hud.combo.textContent = `🔥 ${s.streak}`;
      hud.combo.classList.toggle('hot', s.streak >= 2);
      hud.combo.classList.remove('bump'); void hud.combo.offsetWidth; if (s.streak) hud.combo.classList.add('bump');
    }
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = Play.stage(sh.canvas, {height: 450, minW: 360, maxW: 1400});
    ctx = stage.ctx;
    stage.onResize = () => { lines = []; if (S) S.runnerX = 0; };
    S = fresh(sh.data.avatar);
    loop = Play.loop(update, render);
    loop.start();
    document.addEventListener('visibilitychange', () => {   // 切到后台就暂停（时间、影子都停）
      if (document.hidden) loop.pause();
      else { loop.resume(); if (S.mode === 'run' && (performance.now() - S.wall0) / 1000 >= S.limit) timeUp(); }
    });
    // 「开始」「再来一局」：倒计时期间回到起跑线
    for (const b of sh.root.querySelectorAll('.pl-go, .pl-again')) b.addEventListener('click', () => { S = fresh(sh.data.avatar); fx.parts = []; fx.texts = []; lines = []; sh.hud.innerHTML = ''; hud = null; });
  }

  function begin(match, q, shell) {
    sh = shell; quiz = q;
    S = fresh(match.avatar || sh.data.avatar);
    const g = match.ghost && match.ghost.finish_s;
    S.ghostV = LEN / Play.clamp(g || GHOST_FIRST, 40, HARD_CAP);
    S.limit = Math.max(10, Math.min(HARD_CAP, match.seconds_left || HARD_CAP));
    S.wall0 = performance.now();
    S.mode = 'run';
    fx.parts = []; fx.texts = []; lines = [];
    buildHud();
    sh.toast(g ? `${ghostLabel}跑了 ${g} 秒，这次更快！` : '第一次跑：追上影子小人！', 'good');
  }

  const rules = [
    '答对一题，就<b>冲刺</b>一段！连对越多，冲得越远',
    '连对 3 题 = <b>🌈 彩虹超级冲刺</b>',
    '答错会被<b>冰冻</b>几秒，看清题目再答更快',
    lastS ? `和<b>「上次的我」</b>赛跑：上次用了 <b>${lastS} 秒</b>，这次能更快吗？` : '和半透明的<b>影子小人</b>赛跑，先到终点就赢！',
  ];

  PlayShell.init({
    game: 'race', dock: true, tempo: 140, rules,
    keys: '电脑键盘：1–4 选答案，Enter 确定',
    setup: boot, begin,
    onAnswer: res => onAnswer(res), onXP: res => onXP(res),
  });
})();

/* 句子补洞：小路断成几截，每补对一个句子里的洞，就在一个缺口上搭好一块木板，小人往前走一格，走到旗子就过关。
   答题面板常驻在画面下方（dock），题只出句子补洞（服务器 app/arena/games/cloze.py 的 ask，app/words.py 的 cloze_item）。
   答错不扣命、不冰冻：看清是哪个词，点下一题，这个词隔几道还会再来。词本身就是玩法，没有别的操作。 */
(function () {
  const GROUND = 330;        // 路面的 y
  const PLAT = 120;          // 一截路多宽
  const GAP = 64;            // 一格缺口多宽（关卡里的 gaps：1 或 2 格）
  const INK = '#2b2340';
  const FONT = '"Baloo 2", "PingFang SC", system-ui, sans-serif';

  let sh = null, stage = null, ctx = null, loop = null, c = null, hud = null;
  const fx = new Play.FX();
  let S = null, clock = 0;

  // 一关的路：第 i 截路从 xs[i] 开始，后面跟着第 i 个缺口
  function layout(lv) {
    const gaps = (lv.gaps || []).slice(0, lv.holes || 6);
    while (gaps.length < (lv.holes || 6)) gaps.push(1);
    const xs = [], gx = [];
    let x = 60;
    for (let i = 0; i <= gaps.length; i++) {
      xs.push(x); x += PLAT;
      if (i < gaps.length) { gx.push([x, GAP * gaps[i]]); x += GAP * gaps[i]; }
    }
    return {xs, gx, end: x, flag: x - 40};
  }

  function fresh(lv, avatar) {
    const L = layout(lv || {holes: 6});
    return {mode: 'preview', lv: lv || {holes: 6}, L, filled: 0, planks: [], heroX: L.xs[0] + PLAT / 2, toX: L.xs[0] + PLAT / 2,
            hop: 0, hurt: 0, streak: 0, best: 0, avatar: avatar || 'mint', cam: 0, limit: 600, t0: 0, sent: false, winT: 0};
  }

  // ------------------------------------------------------------ 更新
  function update(dt) {
    clock += dt;
    fx.update(dt);
    const s = S;
    if (!s) return;
    const dx = s.toX - s.heroX;
    s.heroX += Math.sign(dx) * Math.min(Math.abs(dx), dt * 260);
    s.hop = Math.abs(dx) > 1 ? s.hop + dt : 0;
    s.hurt = Math.max(0, s.hurt - dt);
    for (const p of s.planks) p.t = Math.min(1, p.t + dt * 3);
    const W = stage.W, want = Play.clamp(s.heroX - W * 0.35, 0, Math.max(0, s.L.end + 40 - W));
    s.cam = Play.lerp(s.cam, want, Math.min(1, dt * 4));
    if (s.mode === 'win') {
      s.winT += dt;
      if (s.winT > 0.3 && Math.random() < 0.25) fx.burst(s.L.flag - s.cam, GROUND - 120, {n: 4, shape: 'confetti', speed: 240});
      if (!s.sent && s.winT > 1.8) { s.sent = true; report('win'); }
    } else if (s.mode === 'play' && c && c.elapsed() >= s.limit) {
      s.mode = 'done'; lockQuiz(); sh.toast('⏰ 时间到！', 'warn'); setTimeout(() => report('lose'), 900);
    }
    updateHud();
  }

  // ------------------------------------------------------------ 画面
  function render() {
    const W = stage.W, H = stage.H, s = S, theme = (s.lv.world && s.lv.world.scene) || 'day';
    stage.begin();
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    Play.scene.sky(ctx, W, H, theme);
    Play.scene.drawClouds(ctx, W, theme, clock * 2);
    Play.scene.hills(ctx, W, H, GROUND, theme, s.cam * 0.5);
    ctx.save(); ctx.translate(-s.cam, 0);
    // 河水（缺口底下）
    ctx.fillStyle = '#5db8f0'; ctx.fillRect(s.cam - 10, GROUND + 26, W + 20, H - GROUND);
    ctx.fillStyle = 'rgba(255,255,255,.4)';
    for (let x = Math.floor(s.cam / 40) * 40; x < s.cam + W + 40; x += 40) ctx.fillRect(x + (clock * 30 % 40), GROUND + 40 + (x / 40 % 2) * 14, 18, 3);
    // 一截一截的路
    const th = Play.scene.themes[theme] || Play.scene.themes.day;
    for (const x of s.L.xs) {
      ctx.fillStyle = th.soil; Play.roundRect(ctx, x, GROUND, PLAT, H - GROUND + 10, 10); ctx.fill();
      ctx.fillStyle = th.ground; Play.roundRect(ctx, x - 4, GROUND - 6, PLAT + 8, 20, 9); ctx.fill();
      ctx.strokeStyle = INK; ctx.lineWidth = 3; Play.roundRect(ctx, x - 4, GROUND - 6, PLAT + 8, 20, 9); ctx.stroke();
    }
    // 缺口：还没补的画一个问号，补好的搭一块木板（从上面落下来）
    s.L.gx.forEach(([x, w], i) => {
      const p = s.planks[i];
      if (p) {
        const y = GROUND - 6 - (1 - Play.ease.outBack(p.t)) * 80;
        ctx.fillStyle = '#c98a4b'; Play.roundRect(ctx, x - 6, y, w + 12, 16, 5); ctx.fill();
        ctx.strokeStyle = INK; ctx.lineWidth = 3; Play.roundRect(ctx, x - 6, y, w + 12, 16, 5); ctx.stroke();
        ctx.fillStyle = '#a86f38'; for (let k = x + 10; k < x + w; k += 22) ctx.fillRect(k, y + 3, 3, 10);
      } else {
        const next = i === s.filled, bob = next ? Math.sin(clock * 4) * 4 : 0;
        ctx.font = `900 ${next ? 34 : 24}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.lineWidth = 6; ctx.strokeStyle = '#fff'; ctx.strokeText('?', x + w / 2, GROUND - 40 + bob);
        ctx.fillStyle = next ? '#ff7a00' : 'rgba(43,35,64,.45)'; ctx.fillText('?', x + w / 2, GROUND - 40 + bob);
      }
    });
    // 终点旗子
    const fxp = s.L.flag;
    ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(fxp, GROUND - 6); ctx.lineTo(fxp, GROUND - 110); ctx.stroke();
    ctx.fillStyle = '#ff5d8f'; ctx.beginPath(); ctx.moveTo(fxp, GROUND - 110); ctx.lineTo(fxp + 46, GROUND - 96 + Math.sin(clock * 5) * 3); ctx.lineTo(fxp, GROUND - 80); ctx.fill();
    ctx.stroke();
    // 小人
    const walking = Math.abs(s.toX - s.heroX) > 1;
    const lift = walking ? Math.abs(Math.sin(s.hop * 9)) * 18 : 0;
    Art.hero(ctx, s.heroX, GROUND - 6, {avatar: s.avatar, t: clock, scale: 0.7, lift,
      pose: s.mode === 'win' ? 'win' : s.hurt > 0 ? 'hurt' : walking ? 'walk' : 'idle'});
    ctx.restore();
    fx.draw(ctx);
    ctx.restore();
    fx.drawFlash(ctx, W, H);
  }

  // ------------------------------------------------------------ 状态条：补了几个洞、连着补对几个
  function buildHud() {
    sh.hud.innerHTML = `<div class="cz-hud"><span class="cz-chip cz-holes"></span><span class="cz-chip cz-combo"></span></div>`;
    hud = {holes: sh.hud.querySelector('.cz-holes'), combo: sh.hud.querySelector('.cz-combo'), last: {}};
  }
  function updateHud() {
    if (!hud || !S) return;
    const k = `${S.filled}/${S.L.gx.length}`;
    if (hud.last.k !== k) { hud.last.k = k; hud.holes.textContent = `🧩 ${S.filled} / ${S.L.gx.length}`; }
    if (hud.last.st !== S.streak) { hud.last.st = S.streak; hud.combo.textContent = `🔥 ${S.streak}`; hud.combo.classList.toggle('hot', S.streak >= 2); }
  }

  // ------------------------------------------------------------ 答题
  function onAnswer(res) {
    const s = S;
    if (!s || s.mode !== 'play') return;
    if (!res.correct) {
      s.streak = 0; s.hurt = 0.6;
      sh.toast(res.dont_know ? '看看是哪个词，它一会儿还会回来' : '差一点！这个词一会儿还会回来', 'warn');
      return;
    }
    s.streak = res.streak || s.streak + 1; s.best = Math.max(s.best, s.streak);
    const i = s.filled;
    if (i >= s.L.gx.length) return;
    s.planks[i] = {t: 0}; s.filled++;
    const [gx, gw] = s.L.gx[i];
    Play.sfx.play('coin');
    fx.burst(gx + gw / 2 - s.cam, GROUND - 10, {n: 12, colors: ['#ffd23f', '#fff', '#c98a4b'], speed: 200});
    if (res.word) fx.text(gx + gw / 2 - s.cam, GROUND - 90, res.word, {color: '#ff7a00', size: 26});
    s.toX = s.filled > s.L.gx.length - 1 ? s.L.flag - 30 : s.L.xs[s.filled] + PLAT / 2;
    if (s.filled >= s.L.gx.length) { s.mode = 'win'; lockQuiz(); Play.sfx.play('star'); sh.toast('🎉 路通了！', 'good'); }
  }

  function lockQuiz() { if (c && c.quiz) { c.quiz.stop(); c.quiz.el.classList.add('wait'); } }

  function report(result) {
    const s = S, secs = Math.round(c ? c.elapsed() : 0);
    const title = result === 'win' ? `🧩 补好了 ${s.L.gx.length} 个洞，路通了！<small class="cz-sub">${secs} 秒 · 最多连着补对 ${s.best} 个</small>`
      : `⏰ 时间到，补好了 ${s.filled} 个洞<small class="cz-sub">下次接着补</small>`;
    c.finish(result, {seconds: secs, score: s.best}, title);
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = sh.stage || Play.stage(sh.canvas, {height: 450, minW: 360, maxW: 1400});
    ctx = stage.ctx;
    S = fresh(null, sh.data.avatar);
    loop = Play.loop(update, render);
    loop.start();
  }
  function ready() { S = fresh(S && S.lv, sh.data.avatar); fx.parts = []; fx.texts = []; sh.hud.innerHTML = ''; hud = null; }
  function begin(cx) {
    c = cx;
    S = fresh(cx.level, (cx.match && cx.match.avatar) || sh.data.avatar);
    S.limit = Math.max(30, Math.min(600, (cx.match && !cx.match.unlimited && cx.match.seconds_left) || 600));
    S.mode = 'play';
    buildHud();
    sh.toast(`这一段有 ${S.L.gx.length} 个洞，找对的词补上！`, 'good');
  }

  const rules = [
    '句子里有一个<b>洞</b>，从下面挑一个词补上',
    '补对一个，缺口上就搭好<b>一块木板</b>，小人往前走一格',
    '补错了也不扣命：看清是哪个词，它<b>一会儿还会回来</b>',
    '把所有的洞补好，走到<b>🚩 旗子</b>就过关',
  ];

  PlayShell.init({
    quiz: 'dock', tempo: 110, rules,
    keys: '电脑键盘：1–4 选词，Enter 确定',
    setup: boot, ready, begin,
    end() { lockQuiz(); },
    preview(shell, lv) { if (!S || S.mode === 'preview') S = fresh(lv, shell.data.avatar); },
    onAnswer: res => onAnswer(res),
    quizOpts: {
      nextDelay: 1500,   // 补好的整句和中文多看一会儿
      rightHtml: r => `<div class="aq-xp">补上了！</div>` + (r.zh ? `<div class="small">${esc(r.zh)}</div>` : ''),
    },
  });
})();

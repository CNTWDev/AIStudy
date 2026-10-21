/* 乒乓球对战（家里两个人各用一台设备）。规则和让球在 app/arena/games/pingpong.py。
   - 从上往下看的球台，每个人看到的都是自己在左边；上下拖手指（或鼠标、↑↓ 键）移动球拍。
   - 球打在球拍中间又快又直，打在边上斜着飞出去；挥拍的方向也会把球带偏一点。连续对打越久，球越快。
   - 球归接球的那台设备管：飞到我这边时，我的设备判断接没接到（没有网络延迟），接到了把球的位置和速度发给对方（'hit'），
     没接到就告诉对方得分（'pt'）。球飞到对方那边时，我这边把球停在对方球拍那里，等对方说接到还是没接到。
   - 比分两台设备各算一份（得分消息按顺序编号，不会算两次）；一局打完，输的人倒下答题（外壳 ctx.die()，和所有游戏一样答对 1 道），
     答完两边一起开始下一局。整场打完由开房间的人报结果（谁赢、局分）。
   - 刷新 / 换设备：比分在存档和对方那里（saveWorld），回来后这一分重新发球。 */
(function () {
  const L = 274, TW = 152, PX = 20, YMIN = -32, YMAX = TW + 32;   // 球台长、宽（厘米），球拍在球台两头外面 PX 处来回动
  const PH = 19, BALL_R = 3.4, S0 = 235, SERVE = 0.82, MAX_RAMP = 1.5;
  const KEY_SPEED = 330, DRAG_SPEED = 560, AUTO_SERVE = 4;
  const C_ME = '#e8453c', C_PEER = '#2f7de1';

  let sh, mp, loop, fx, lv = {}, t = 0, ended = true, pointerReady = false;
  let mySide = 0, me = null, peer = null, ball = null, phase = 'idle', phaseT = 0, msg = '';
  let score = null, hand = null, aim = null, view = null;

  // ---------------------------------------------------------------- 比分（两台设备一样算）
  const ids = () => [String(mp.me.id), String(mp.peer.id)];
  const hostId = () => String(mp.host ? mp.me.id : mp.peer.id);
  const other = id => ids().find(x => x !== String(id));
  const nameOf = id => String(id) === String(mp.me.id) ? mp.me.name : mp.peer.name;
  function newScore() { const [a, b] = ids(); return {games: {[a]: 0, [b]: 0}, pts: {[a]: 0, [b]: 0}, g: 1, seq: 0, boost: '', quiz: '', over: ''}; }
  function server() {   // 谁发球：每 2 分换一次，6 平以后每分换；单数局开房间的人先发
    const [a, b] = ids(), pa = score.pts[a], pb = score.pts[b], n = pa + pb;
    const first = score.g % 2 ? hostId() : other(hostId());
    const k = pa >= lv.points - 1 && pb >= lv.points - 1 ? n : Math.floor(n / 2);
    return k % 2 ? other(first) : first;
  }
  function point(winner) {   // 这一分 winner 得了
    score.seq++;
    score.pts[winner]++;
    const lose = other(winner), pw = score.pts[winner], pl = score.pts[lose];
    const mine = winner === String(mp.me.id);
    Play.sfx.play(mine ? 'coin' : 'hurt');
    if (mine) fx.text(view.W / 2 - 36, 100, '+1', {size: 24, color: '#ffd23f', rise: 26});
    if (pw >= lv.points && (pw - pl >= 2 || pw >= lv.cap)) {
      score.games[winner]++;
      score.pts = {[winner]: 0, [lose]: 0};
      if (score.games[winner] >= lv.to_win) {
        score.over = winner; setPhase('over', 2);
        msg = `${nameOf(winner)} 赢了这一场！`;
        if (mine) { fx.burst(view.W / 2, view.H / 2, {n: 50, shape: 'confetti', speed: 420}); Play.sfx.play('win'); }
        return;
      }
      score.quiz = lose; setPhase('gameover', 1.8);
      msg = mine ? `这一局你赢了！等 ${nameOf(lose)} 答对一道题` : `这一局 ${nameOf(winner)} 赢了，答对一道题再接着打`;
      if (mine) { Play.sfx.play('star'); mp.needs[lose] = 1; }
      return;
    }
    setPhase('point', 1.1);
  }
  function nextGame() {   // 输的人答完题：下一局
    const loser = score.quiz;
    score.quiz = ''; score.g++; score.boost = loser;
    if (loser === String(mp.me.id)) mp.toast('答完题，这一局你的球拍变大了！', 'good');
    else mp.toast(`第 ${score.g} 局开始！`, 'good');
    serveReady();
  }

  // ---------------------------------------------------------------- 球拍、球
  const assist = id => +((lv.assist || {})[String(id)] || 0);
  const half = id => PH * (1 + 0.6 * assist(id)) * (score && score.boost === String(id) ? 1.2 : 1);
  const slow = id => 1 - 0.28 * assist(id);       // 飞向这个人的球慢多少
  const lineX = side => side ? L + PX : -PX;
  const sideOf = id => String(id) === String(mp.me.id) ? mySide : 1 - mySide;
  function setPhase(p, dur) { phase = p; phaseT = dur || 0; }
  function serveReady() { ball = null; setPhase('serve', 0); msg = ''; }
  function launch(fromSide, y, vy, speed, rally) {   // 从 fromSide 那头打出去
    const dir = fromSide ? -1 : 1, vx = dir * speed;
    const xl = fromSide ? L * 0.22 : L * 0.78;   // 落在对方那半边
    const tl = (xl - lineX(fromSide)) / vx;
    const land = Math.max(8, Math.min(TW - 8, y + vy * tl));
    vy = (land - y) / tl;
    return {x: lineX(fromSide), y, vx, vy, x0: lineX(fromSide), xl, to: 1 - fromSide, held: false, out: false, rally, bounced: false};
  }
  function hitBack(py, pv) {   // 我接到了：打回去
    const off = Math.max(-1, Math.min(1, (ball.y - py) / half(mp.me.id)));
    const rally = ball.rally + 1, good = Math.abs(off) < 0.25;
    const speed = S0 * Math.min(MAX_RAMP, 1 + 0.035 * rally) * slow(mp.peer.id) * (good ? 1.15 : 1);
    const ang = off * 0.62 + Math.max(-0.25, Math.min(0.25, pv / 1800));   // 打在哪头往哪头偏，挥拍带一点
    ball = launch(mySide, ball.y, Math.tan(ang) * speed, speed, rally);
    send(ball);
    Play.sfx.play('pop');
    const [x, y] = scr(ball.x, ball.y);
    fx.burst(x, y, {n: good ? 14 : 7, colors: good ? ['#ffd23f', '#fff'] : ['#fff'], speed: 160, size: 3, grav: 0});
    if (good && rally > 2) fx.text(x + (mySide ? -30 : 30), y - 20, '好球！', {size: 20, color: '#ffd23f'});
  }
  function serve() {
    const vy = (Math.random() - 0.5) * 60 + me.v * 0.08;
    ball = launch(mySide, me.y, vy, S0 * SERVE * slow(mp.peer.id), 0);
    send(ball); Play.sfx.play('pop');
  }
  const send = b => mp.net.send({t: 'hit', x: +b.x.toFixed(1), y: +b.y.toFixed(1), vx: +b.vx.toFixed(1), vy: +b.vy.toFixed(1), xl: b.xl, r: b.rally, seq: score.seq});

  // ---------------------------------------------------------------- 每一帧
  function step(dt) {
    t += dt; fx.update(dt);
    if (ended || !me) return;
    control(dt);
    const p = peer.track.get(['y']); if (p) peer.y = p.y;
    if (phaseT > 0) phaseT -= dt;
    if (phase === 'point' && phaseT <= 0) serveReady();
    if (phase === 'gameover' && phaseT <= 0) {
      setPhase('quiz');
      if (score.quiz === String(mp.me.id) && !mp.reviving) mp.die();
    }
    if (phase === 'quiz' && score.quiz && score.quiz !== String(mp.me.id) && mp.needs[score.quiz] === 0) nextGame();
    if (phase === 'over' && phaseT <= 0 && !mp.finishing) {
      phase = 'done';
      const w = score.over, l = other(w);
      if (mp.host) mp.finish('win', {winner: +w, won: score.games[w], lost: score.games[l], score: score.seq});
    }
    if (phase === 'serve') {
      if (server() === String(mp.me.id)) {
        phaseT -= dt;
        if (Play.input.take('serve') || tapped || phaseT < -AUTO_SERVE) { setPhase('rally'); serve(); }
      }
      tapped = false;
      return;
    }
    tapped = false;
    if (!ball || ball.held) return;
    const px = ball.x;
    ball.x += ball.vx * dt; ball.y += ball.vy * dt;
    if (!ball.bounced && (ball.vx > 0 ? ball.x >= ball.xl : ball.x <= ball.xl)) { ball.bounced = true; Play.sfx.play('tick'); }
    if (ball.out) return;
    const line = lineX(ball.to), crossed = ball.vx > 0 ? px < line && ball.x >= line : px > line && ball.x <= line;
    if (!crossed) return;
    if (ball.to === mySide) {   // 飞到我这边：接没接到我说了算
      const y = ball.y, lo = Math.min(me.y, me.py) - half(mp.me.id) - BALL_R, hi = Math.max(me.y, me.py) + half(mp.me.id) + BALL_R;
      if (y >= lo && y <= hi) hitBack(Math.max(lo, Math.min(hi, me.y)), me.v);
      else {
        ball.out = true;
        mp.net.send({t: 'pt', seq: score.seq});
        point(String(mp.peer.id));
      }
    } else {   // 飞到对方那边：停在那里等对方说
      ball.x = line; ball.held = true;
    }
  }
  let tapped = false;
  function control(dt) {
    me.py = me.y;
    const I = Play.input, frozen = mp.reviving || phase === 'quiz' || phase === 'gameover';
    let want = null;
    if (aim !== null && !frozen) want = aim;
    const k = (I.keys.down ? 1 : 0) - (I.keys.up ? 1 : 0);
    if (k && !frozen) want = me.y + k * (mySide ? -1 : 1) * KEY_SPEED * dt * 4;
    if (want !== null) {
      const max = (k ? KEY_SPEED : DRAG_SPEED) * dt, d = Math.max(-max, Math.min(max, want - me.y));
      me.y = Math.max(YMIN, Math.min(YMAX, me.y + d));
    }
    me.v = me.v * 0.7 + (me.y - me.py) / dt * 0.3;
  }

  // ---------------------------------------------------------------- 收消息
  function listen(net) {
    net.off('p', 'hit', 'pt', 'resync');
    net.on('p', m => peer.track.push({y: +m.y || 0}));
    net.on('hit', m => {   // 对方打过来了（或发球）
      if (m.seq !== score.seq) return;
      if (phase === 'quiz' && score.quiz && mp.needs[score.quiz] === 0) nextGame();   // 对方答完题马上发了球
      if (phase !== 'rally' && phase !== 'serve' && phase !== 'point') return;          // 我这边还在看上一分
      ball = {x: m.x, y: m.y, vx: m.vx, vy: m.vy, xl: m.xl, x0: m.x, to: mySide, held: false, out: false, rally: m.r || 0, bounced: false};
      setPhase('rally');
      Play.sfx.play('pop');
    });
    net.on('pt', m => {   // 对方没接到
      if (m.seq !== score.seq) return;
      if (ball) { ball.held = false; ball.out = true; }
      point(String(mp.me.id));
    });
    net.on('resync', () => { if (phase === 'rally' || phase === 'point') serveReady(); });   // 对方刚回来：这一分重新发球
  }

  // ---------------------------------------------------------------- 画
  function layout() {
    const st = sh.stage, W = st.W, H = st.H;
    const top = 84, room = H - top - 34, span = YMAX - YMIN + 2 * PH * 1.4;   // 上面留比分和名字，下面留提示条
    const k = Math.min((W - 30) / (L + 2 * PX + 50), room / span);
    const ox = (W - L * k) / 2, oy = top + (room - span * k) / 2 + (PH * 1.4 - YMIN) * k;
    view = {W, H, k, ox, oy};
  }
  const vx = x => mySide ? L - x : x, vy = y => mySide ? TW - y : y;
  const scr = (x, y, z) => [view.ox + vx(x) * view.k, view.oy + vy(y) * view.k - (z || 0) * view.k];
  function height(b) {   // 球离桌面多高（画影子用）：飞出去落在对方半边，再弹起来
    const a = b.x0 ?? lineX(1 - b.to), span1 = b.xl - a, p1 = span1 ? (b.x - a) / span1 : 1;
    if (p1 < 1) return 9 + 16 * Math.sin(Math.PI * Math.max(0, p1)) - 9 * p1;
    const span2 = lineX(b.to) - b.xl, p2 = span2 ? (b.x - b.xl) / span2 : 1;
    return Math.max(0, 20 * Math.sin(Math.PI * Math.min(1, p2) * 0.55));
  }
  function render() {
    const st = sh.stage, c = st.ctx;
    st.begin(); layout();
    const {W, H, k} = view;
    const g = c.createLinearGradient(0, 0, 0, H); g.addColorStop(0, '#24345c'); g.addColorStop(1, '#3b2f57');
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    c.fillStyle = 'rgba(255,255,255,.04)'; for (let i = 0; i < 9; i++) c.fillRect(i * W / 8 - 20, 60, 40, H);
    const [shx, shy] = fx.offset();
    c.save(); c.translate(shx, shy);
    // 球台
    const [x0, y0] = scr(mySide ? L : 0, mySide ? TW : 0), w = L * k, h = TW * k;
    c.fillStyle = 'rgba(0,0,0,.35)'; Play.roundRect(c, x0 + 6, y0 + 10, w, h, 6); c.fill();
    c.fillStyle = '#1f7a5a'; Play.roundRect(c, x0, y0, w, h, 6); c.fill();
    c.fillStyle = '#1b6b4f'; c.fillRect(x0 + w / 2, y0, w / 2, h);
    c.strokeStyle = '#fff'; c.lineWidth = Math.max(2, k * 1.6); c.strokeRect(x0 + 2, y0 + 2, w - 4, h - 4);
    c.lineWidth = Math.max(1, k * 0.8); c.beginPath(); c.moveTo(x0, y0 + h / 2); c.lineTo(x0 + w, y0 + h / 2); c.stroke();
    // 球网
    c.fillStyle = 'rgba(0,0,0,.25)'; c.fillRect(x0 + w / 2 + 2, y0 - 6 * k, 4, h + 12 * k);
    c.fillStyle = 'rgba(255,255,255,.85)'; c.fillRect(x0 + w / 2 - 2, y0 - 8 * k, 4, h + 16 * k);
    c.fillStyle = '#333'; c.fillRect(x0 + w / 2 - 4, y0 - 10 * k, 8, 5); c.fillRect(x0 + w / 2 - 4, y0 + h + 6 * k, 8, 5);
    if (me && mp) {
      drawPaddle(c, mySide, me.y, half(mp.me.id), C_ME, mp.me.name);
      drawPaddle(c, 1 - mySide, peer.y, half(mp.peer.id), C_PEER, mp.peer.name);
      drawBall(c);
    }
    fx.draw(c);
    c.restore();
    if (score && mp) scoreboard(c);
    banner(c);
  }
  function drawPaddle(c, side, y, hh, col, name) {
    const [x, y1] = scr(lineX(side), y - hh), [, y2] = scr(lineX(side), y + hh);
    const top = Math.min(y1, y2), len = Math.abs(y2 - y1), wd = Math.max(7, 6 * view.k), out = (vx(lineX(side)) < L / 2 ? -1 : 1);
    c.fillStyle = 'rgba(0,0,0,.3)'; Play.roundRect(c, x - wd / 2 + 4, top + 6, wd, len, wd / 2); c.fill();
    c.fillStyle = '#c99a5b'; c.fillRect(x + out * wd / 2, top + len / 2 - 4, out * 14, 8);
    c.fillStyle = col; Play.roundRect(c, x - wd / 2, top, wd, len, wd / 2); c.fill();
    c.fillStyle = 'rgba(255,255,255,.35)'; Play.roundRect(c, x - wd / 2 + 2, top + 3, 2.5, len - 6, 1.5); c.fill();
    c.font = '800 13px "PingFang SC", system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
    const tw = c.measureText(name).width + 12, ny = top - 14;
    c.fillStyle = 'rgba(0,0,0,.45)'; Play.roundRect(c, x - tw / 2, ny - 9, tw, 18, 9); c.fill();
    c.fillStyle = '#fff'; c.fillText(name, x, ny);
  }
  function drawBall(c) {
    let b = ball;
    if (phase === 'serve') {   // 等着发球：球在发球的人球拍前
      const s = score && server() === String(mp.me.id);
      const side = s ? mySide : 1 - mySide, y = s ? me.y : peer.y;
      b = {x: lineX(side) + (side ? -8 : 8), y, held: true, to: 1 - side, xl: 0, x0: 0};
      const [x, yy] = scr(b.x, b.y, 14 + Math.abs(Math.sin(t * 4)) * 10);
      ballAt(c, x, yy, scr(b.x, b.y));
      return;
    }
    if (!b) return;
    const z = b.held ? 10 : height(b);
    ballAt(c, ...scr(b.x, b.y, z), scr(b.x, b.y));
  }
  function ballAt(c, x, y, [gx, gy]) {
    const r = Math.max(4, BALL_R * view.k);
    c.fillStyle = 'rgba(0,0,0,.3)'; c.beginPath(); c.ellipse(gx + 2, gy + 2, r, r * 0.6, 0, 0, 7); c.fill();
    c.fillStyle = '#fff6e0'; c.beginPath(); c.arc(x, y, r, 0, 7); c.fill();
    c.fillStyle = '#ffb347'; c.beginPath(); c.arc(x - r * 0.3, y - r * 0.3, r * 0.4, 0, 7); c.fill();
  }
  function scoreboard(c) {
    const W = view.W, me_ = String(mp.me.id), pe = String(mp.peer.id);
    c.fillStyle = 'rgba(0,0,0,.4)'; Play.roundRect(c, W / 2 - 170, 8, 340, 50, 14); c.fill();
    c.textBaseline = 'middle'; c.textAlign = 'center';
    c.font = '900 30px "Baloo 2", system-ui, sans-serif';
    c.fillStyle = C_ME; c.fillText(score.pts[me_], W / 2 - 36, 34);
    c.fillStyle = '#fff'; c.fillText(':', W / 2, 32);
    c.fillStyle = C_PEER; c.fillText(score.pts[pe], W / 2 + 36, 34);
    c.font = '700 12px "PingFang SC", system-ui, sans-serif'; c.fillStyle = 'rgba(255,255,255,.8)';
    c.fillText(`第 ${score.g} 局`, W / 2, 50);
    const dots = (x, n, col, dir) => { for (let i = 0; i < lv.to_win; i++) { c.fillStyle = i < n ? col : 'rgba(255,255,255,.2)'; c.beginPath(); c.arc(x + dir * i * 14, 22, 5, 0, 7); c.fill(); } };
    dots(W / 2 - 150, score.games[me_], C_ME, 1); dots(W / 2 + 150, score.games[pe], C_PEER, -1);
    c.font = '800 13px "PingFang SC", system-ui, sans-serif';
    c.fillStyle = '#fff'; c.textAlign = 'left'; c.fillText(mp.me.name, W / 2 - 156, 42);
    c.textAlign = 'right'; c.fillText(mp.peer.name, W / 2 + 156, 42);
  }
  function banner(c) {
    if (!mp || !score) return;
    let text = '';
    if (phase === 'serve') text = server() === String(mp.me.id) ? (matchMedia('(pointer:coarse)').matches ? '点一下发球' : '点一下或按空格发球') : `${nameOf(server())} 发球`;
    else if (phase === 'gameover' || phase === 'over' || phase === 'done') text = msg;
    else if (phase === 'quiz' && score.quiz !== String(mp.me.id)) {
      const n = mp.needs[score.quiz];
      text = `${nameOf(score.quiz)} 在答题${n > 0 ? `：还要答对 ${n} 道` : '…'}`;
    }
    if (!text) return;
    c.font = '800 18px "PingFang SC", system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
    const w = c.measureText(text).width + 30, y = view.H - 26;
    c.fillStyle = 'rgba(0,0,0,.55)'; Play.roundRect(c, view.W / 2 - w / 2, y - 17, w, 34, 17); c.fill();
    c.fillStyle = '#fff'; c.fillText(text, view.W / 2, y);
  }

  // ---------------------------------------------------------------- 手指、鼠标
  function pointerSetup(canvas) {
    const toTable = e => {
      const r = canvas.getBoundingClientRect(), ly = (e.clientY - r.top) / r.height * sh.stage.H;
      const y = (ly - view.oy) / view.k;
      return mySide ? TW - y : y;
    };
    let held = false;
    canvas.addEventListener('pointerdown', e => {
      if (ended || !view) return;
      held = true; aim = toTable(e); tapped = true;
      if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    canvas.addEventListener('pointermove', e => { if (!ended && view && (held || e.pointerType === 'mouse')) aim = toTable(e); });
    const up = e => { held = false; if (e.pointerType !== 'mouse') aim = null; };
    canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
    canvas.addEventListener('pointerleave', () => { if (!held) aim = null; });
    canvas.style.touchAction = 'none';
  }

  // ---------------------------------------------------------------- 存档、重连
  function loadWorld(w) {
    if (!w || !w.games || !score) return;
    const ok = ids().every(i => i in w.games && i in w.pts);
    if (!ok) return;
    Object.assign(score, {games: {...w.games}, pts: {...w.pts}, g: w.g || 1, seq: w.seq || 0, boost: w.boost || '', quiz: w.quiz || '', over: w.over || ''});
    if (score.over) { setPhase('over', 0.5); msg = `${nameOf(score.over)} 赢了这一场！`; }
    else if (score.quiz) { setPhase('quiz'); if (score.quiz === String(mp.me.id) && !mp.reviving) mp.die(); else if (score.quiz !== String(mp.me.id) && mp.needs[score.quiz] == null) mp.needs[score.quiz] = 1; }
    else serveReady();
  }
  const saveWorld = () => score && {games: score.games, pts: score.pts, g: score.g, seq: score.seq, boost: score.boost, quiz: score.quiz, over: score.over};

  PlayShell.init({
    quiz: 'revive', tempo: 132, revive: {verb: '接着打', done: '🎉 答对了，接着打！'},
    noQuiz: '这一场我一局都没输，一道题都不用答！',
    rules: ['和家里人打乒乓球：<b>每局 7 分</b>（6 平以后要多赢 2 分，最多打到 10 分），<b>三局两胜</b>。',
            '上下拖手指（或移动鼠标、按 ↑ ↓）移动球拍；轮到你发球时点一下。',
            '球打在球拍中间又快又直，打在边上会斜着飞；挥拍的方向也会带偏球。对打越久球越快。',
            '<b>输了一局答对一道题</b>，就接着打下一局（答错了换一道接着答）。答对了，下一局你的球拍会变大。',
            '年纪小的球拍大一些、球飞过去慢一些；谁老赢，下次就难一点，老输就容易一点。'],
    keys: '电脑键盘：↑ ↓ 或 W S 移动球拍，空格 发球；也可以直接移动鼠标',
    input: {up: ['ArrowUp', 'w'], down: ['ArrowDown', 's'], serve: [' ', 'Enter']},
    preview(s) {
      sh = s; fx = fx || new Play.FX();
      if (!ended) return;
      mp = null; score = null; render();
    },
    verdict(res) {
      const won = res.winner === mp.me.id, a = won ? res.won : res.lost, b = won ? res.lost : res.won;
      return won ? {result: 'win', title: '🏆 你赢了这一场！', why: `局分 ${a} : ${b}`}
                 : {result: 'lose', title: '💪 差一点！再来一场', why: `局分 ${a} : ${b}`};
    },
    begin(ctx) {
      mp = ctx; sh = ctx.sh; fx = new Play.FX(); lv = ctx.level || {}; t = 0; ended = false; aim = null; tapped = false;
      lv.points = lv.points || 7; lv.to_win = lv.to_win || 2; lv.cap = lv.cap || lv.points + 3;
      if (!pointerReady) { pointerSetup(sh.canvas); pointerReady = true; }
      mySide = ctx.host ? 0 : 1;
      me = {y: TW / 2, py: TW / 2, v: 0};
      peer = {y: TW / 2, track: new MpNet.Track(80)};
      score = newScore(); layout(); serveReady();
      listen(ctx.net);
      const rs = ctx.resume;
      if (rs && rs.me && typeof rs.me.y === 'number') me.y = me.py = rs.me.y;
      if (rs && rs.world) loadWorld(rs.world);
      const a = assist(ctx.me.id), b = assist(ctx.peer.id);
      if (!rs && Math.abs(a - b) >= 0.2) ctx.toast(a > b ? '让球：你的球拍大一些，球飞过来慢一些' : `让球：${ctx.peer.name}的球拍大一些，球飞过去慢一些`, 'good');
      ctx.net.stream(() => me && !ended ? {t: 'p', y: Math.round(me.y)} : null, 20);
      if (loop) loop.stop();
      loop = Play.loop(step, render);
      loop.start();
    },
    onRevive() { if (score && score.quiz === String(mp.me.id)) { Play.sfx.play('power'); nextGame(); } },
    save: () => me && {y: Math.round(me.y)},
    saveWorld,
    loadWorld(w) { loadWorld(w); if (mp) mp.net.send({t: 'resync'}); },
    end() { ended = true; aim = null; if (loop) loop.stop(); if (mp) mp.net.stopStreams(); },
  });
})();

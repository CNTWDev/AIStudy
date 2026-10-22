/* 单词跑酷：三条跑道往前冲，← → 换道、↑ 跳，按顺序撞上对的字母把词拼出来；石头要躲开，栏杆要跳过去。
   拼哪个词、给几档提示由服务器按这个人的单词等级定（app/arena/games/runner.py、sources.WordSource._run）：
     hint 0 整个词淡淡地写在上面照着拼（新词）；1 只给中文和首字母；2 只给中文和读音（🔊 可以再听）。
   一个词拼完报一次作答：一路没撞错字母算拼对；撞错过报撞到的那串字母；对的字母错过两次、系统把它亮出来了，算「不会」。
   撞错字母只减速、亮出要的那个字母，不算摔；撞上石头、没跳过栏杆才摔倒：外壳盖上题目（就是正在拼的这个词），答对 1 道就起来。
   画面是往远处收的三条跑道（近大远小）：z 是离角色多远，越远越小、越靠近地平线。 */
(function () {
  const INK = '#2b2340';
  const FONT = '"Baloo 2", "PingFang SC", system-ui, sans-serif';
  const ZFAR = 46, ROW = 8.5, F = 7;          // 看得到多远、一排和一排隔多远、透视的焦距
  const BASE_SPEED = 11;                       // 第 1 关每秒跑多远
  const HIT_Z = 0.9;                           // 离角色这么近算撞上
  const SIMILAR = {b: 'dp', d: 'bq', p: 'qb', q: 'pd', m: 'nw', n: 'mh', u: 'vn', v: 'uw', w: 'vm', i: 'lj', l: 'it', e: 'ao', a: 'eo', o: 'ae', c: 'ek', k: 'ch', g: 'qj', s: 'zc', z: 's', r: 'n', t: 'f', f: 't', h: 'n', y: 'v', j: 'i'};
  const ROAD = {day: '#7b7f92', candy: '#d79bbd', sunset: '#9a7f86', night: '#41457e', lava: '#5a3a35'};

  let sh = null, stage = null, ctx = null, loop = null, c = null, hud = null;
  const fx = new Play.FX();
  let S = null, clock = 0, swipe = null;

  function fresh(lv, avatar) {
    lv = lv || {words: 4, speed: 1, rocks: 0.25, bars: 0};
    return {mode: 'preview', lv, avatar: avatar || 'mint', objs: [], lastRow: ZFAR - ROW, rowN: 0, scroll: 0,
            lane: 1, x: 1, jumpT: 0, jumpV: 0, h: 0, slow: 0, safe: 0, fallT: 0,
            item: null, word: '', got: 0, typed: '', slip: false, helped: false, miss: 0, reveal: 0, loading: false,
            done: 0, clean: 0, combo: 0, best: 0, coins: 0, falls: 0, limit: 600, winT: 0, sent: false};
  }
  const theme = () => (S.lv.world && S.lv.world.scene) || 'day';
  const speed = () => BASE_SPEED * (S.lv.speed || 1) * (1 + Math.min(0.2, S.combo * 0.04)) * (S.slow > 0 ? 0.55 : 1);

  // ------------------------------------------------------------ 透视：z 远近、lane 第几条道（0 1 2，可以是小数）→ 画面上的位置和大小
  function proj(z, lane) {
    const W = stage.W, H = stage.H, hz = H * 0.28, py = H - 52;
    const s = F / (F + Math.max(-0.5, z));
    const half = Math.min(W * 0.44, 300);   // 角色那儿路面一半多宽
    return {x: W / 2 + (lane - 1) * (half * 2 / 3) * s, y: hz + (py - hz) * s, s: s * half / 230};
  }

  // ------------------------------------------------------------ 出词：服务器按单词等级给（这一题也是摔倒时要答的那道）
  async function nextWord() {
    if (!c || S.loading || S.mode !== 'play') return;
    S.loading = true;
    try {
      const r = await fetch(`/api/arena/${c.match.match_id}/q`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || d.detail || '出错了');
      setWord(d.item);
    } catch (e) {
      S.mode = 'done'; sh.toast(e.message, 'warn');
      setTimeout(() => c.finish('quit', {why: e.message}, `🏃 ${esc(e.message)}`), 1200);
    } finally { S.loading = false; }
  }
  function setWord(it) {
    if (!it.run) return;   // 不会发生：跑酷只出 run 题
    S.item = it; S.word = it.run.w; S.got = 0; S.typed = ''; S.slip = false; S.helped = false; S.miss = 0;
    S.hint = it.retry ? 0 : it.run.hint;   // 刚错过、又回来的词：整个词写出来照着拼
    S.reveal = S.hint === 0 ? S.word.length : S.hint === 1 ? 1 : 0;   // 上面亮出来几个字母
    S.objs = S.objs.filter(o => o.kind !== 'letter');   // 上一个词还没撞到的字母收走
    buildWord();
    if (S.hint === 2 || S.hint === 0) say();
  }
  function say() { if (S.word && typeof Speak !== 'undefined') Speak.play(S.word, 'en', 'word'); }
  const need = () => S.word[S.got] ? S.word[S.got].toLowerCase() : '';

  async function finishWord() {
    const it = S.item, typed = S.typed, slip = S.slip, helped = S.helped;
    S.item = null; S.word = '';
    const body = !slip && helped ? {item_id: it.id, dont_know: true} : {item_id: it.id, answer: slip ? typed : it.run.w};
    let res = null;
    try { res = await api(`/api/arena/${c.match.match_id}/a`, body); } catch (e) { /* 过期了（摔倒时被换了一道）：接着跑 */ }
    S.done++;
    const ok = res ? res.correct : !slip && !helped;
    if (ok) { S.clean++; S.combo++; S.best = Math.max(S.best, S.combo); } else S.combo = 0;
    const p = proj(0, S.x);
    Play.sfx.play(ok ? 'star' : 'pop');
    fx.burst(p.x, p.y - 80, {n: ok ? 22 : 10, shape: ok ? 'star' : 'circle', speed: 260});
    fx.text(stage.W / 2, stage.H * 0.42, `${it.run.w}  ${it.run.zh}`, {color: ok ? '#ff7a00' : '#6a5a8a', size: 30, life: 1.6, rise: 40});
    say();
    if (ok && S.combo >= 2) sh.toast(`🔥 连着拼对 ${S.combo} 个，加速！`, 'good');
    else if (!ok) sh.toast('这个词一会儿还会回来，下次一口气拼对！', 'warn');
    buildWord(); updateHud();
    if (S.done >= S.lv.words) { S.mode = 'win'; Play.sfx.play('win'); sh.toast('🏁 冲过终点！', 'good'); return; }
    nextWord();
  }

  // ------------------------------------------------------------ 一排一排地摆：字母排（要的字母 + 混淆的字母）、障碍排（石头、栏杆、金币）
  function spawnRow(z) {
    const n = S.rowN++, lanes = [0, 1, 2].sort(() => Math.random() - 0.5);
    if (S.word && S.got < S.word.length && n % 2 === 0) {
      const want = need(), k = (S.lv.words > 6 || S.hint > 0) ? 2 : 1;   // 新词只放一个混淆的字母
      S.objs.push({kind: 'letter', z, lane: lanes[0], ch: want, ok: true});
      const pool = (SIMILAR[want] || '') + S.word.toLowerCase().replace(want, '') + 'aeiourstnl';
      const used = new Set([want]);
      for (let i = 1; i <= k; i++) {
        let ch = '';
        for (let tries = 0; tries < 12 && (!ch || used.has(ch)); tries++) ch = pool[Math.floor(Math.random() * pool.length)];
        if (!ch || used.has(ch)) continue;
        used.add(ch); S.objs.push({kind: 'letter', z, lane: lanes[i], ch, ok: false});
      }
      return;
    }
    if (S.mode !== 'play' || n < 2) { S.objs.push({kind: 'coin', z, lane: lanes[0]}); return; }
    if (Math.random() < S.lv.bars) {   // 栏杆：横着一两条道，跳过去
      const w = Math.random() < 0.4 ? 3 : 1;
      for (let i = 0; i < w; i++) S.objs.push({kind: 'bar', z, lane: lanes[i]});
      if (w < 3) S.objs.push({kind: 'coin', z, lane: lanes[2]});
    } else if (Math.random() < S.lv.rocks + 0.35) {   // 石头：最多两条道，总有一条能过
      const w = Math.random() < S.lv.rocks ? 2 : 1;
      for (let i = 0; i < w; i++) S.objs.push({kind: 'rock', z, lane: lanes[i]});
      S.objs.push({kind: 'coin', z, lane: lanes[2]});
    } else {
      S.objs.push({kind: 'coin', z, lane: lanes[0]}, {kind: 'coin', z: z + 1.2, lane: lanes[0]});
    }
  }

  // ------------------------------------------------------------ 更新
  function update(dt) {
    clock += dt; fx.update(dt);
    const s = S;
    if (!s || s.mode === 'preview') { if (s) s.scroll += dt * 4; return; }
    if (s.mode === 'fall') { s.fallT += dt; return; }   // 摔倒了：等答题起来（外壳盖着题目）
    // 操作
    if (s.mode === 'play') {
      if (Play.input.take('left')) moveLane(-1);
      if (Play.input.take('right')) moveLane(1);
      if (Play.input.take('jump')) jump();
    }
    s.x += Math.sign(s.lane - s.x) * Math.min(Math.abs(s.lane - s.x), dt * 9);
    if (s.jumpT > 0) { s.jumpT += dt; s.h = Math.max(0, Math.sin(Math.min(1, s.jumpT / 0.62) * Math.PI)); if (s.jumpT >= 0.62) { s.jumpT = 0; s.h = 0; } }
    s.slow = Math.max(0, s.slow - dt); s.safe = Math.max(0, s.safe - dt);
    const v = speed() * (s.mode === 'win' ? 1.3 : 1), dz = v * dt;
    s.scroll += dz;
    for (const o of s.objs) o.z -= dz;
    s.lastRow -= dz;
    while (s.lastRow < ZFAR - ROW) { s.lastRow += ROW; if (s.mode === 'play') spawnRow(s.lastRow); }
    // 撞上什么
    const myLane = Math.round(s.x);
    for (const o of s.objs) {
      if (o.gone || o.z > HIT_Z || o.z < -0.6) continue;
      if (o.lane !== myLane) { if (o.kind === 'letter' && o.ok && !o.passed && o.z < 0) { o.passed = true; missed(); } continue; }
      if (o.kind === 'coin') { o.gone = true; s.coins++; Play.sfx.play('coin'); const p = proj(0, s.x); fx.burst(p.x, p.y - 60, {n: 6, colors: ['#ffd23f', '#fff'], speed: 140}); }
      else if (o.kind === 'letter') { o.gone = true; o.ok ? rightLetter(o) : wrongLetter(o); }
      else if (s.safe <= 0 && s.mode === 'play' && !(o.kind === 'bar' && s.h > 0.35)) { o.gone = true; fall(o); return; }
    }
    s.objs = s.objs.filter(o => !o.gone && o.z > -1.5);
    if (s.mode === 'win') {
      s.winT += dt;
      if (Math.random() < 0.3) fx.burst(Math.random() * stage.W, stage.H * 0.3, {n: 4, shape: 'confetti', speed: 240});
      if (!s.sent && s.winT > 1.6) { s.sent = true; report('win'); }
    } else if (s.mode === 'play' && c && c.elapsed() >= s.limit) {
      s.mode = 'done'; sh.toast('⏰ 时间到！', 'warn'); setTimeout(() => report('lose'), 900);
    }
    updateHud();
  }
  function moveLane(d) { const to = Play.clamp(S.lane + d, 0, 2); if (to !== S.lane) { S.lane = to; Play.sfx.play('swing'); } }
  function jump() { if (S.jumpT === 0) { S.jumpT = 0.001; Play.sfx.play('jump'); } }

  function rightLetter(o) {
    const s = S;
    s.typed += o.ch; s.got++; s.miss = 0;
    if (s.reveal < s.got) s.reveal = s.got;
    Play.sfx.play('coin');
    const p = proj(0, s.x);
    fx.burst(p.x, p.y - 70, {n: 12, colors: ['#ffd23f', '#7bdff2', '#fff'], speed: 220});
    fx.text(p.x, p.y - 110, o.ch.toUpperCase(), {color: '#2fa84f', size: 34});
    buildWord();
    if (s.got >= s.word.length) finishWord();
  }
  function wrongLetter(o) {
    const s = S;
    s.typed += o.ch; s.slip = true; s.slow = 1.1; s.combo = 0;
    s.reveal = Math.max(s.reveal, s.got + 1);   // 亮出要的那个字母
    Play.sfx.play('wrong'); fx.shake(5, 0.2); fx.flash('rgba(255,120,120,.35)', 0.15);
    const p = proj(0, s.x);
    fx.text(p.x, p.y - 110, `要的是 ${need().toUpperCase()}`, {color: '#e0475b', size: 26});
    buildWord();
  }
  function missed() {   // 要的字母从旁边过去了：再来一次；错过两次就亮出来（这个词算「不会」）
    const s = S;
    s.miss++;
    if (s.miss >= 2 && s.reveal <= s.got) { s.reveal = s.got + 1; s.helped = true; sh.toast(`要找的是 ${need().toUpperCase()}`, 'warn'); buildWord(); }
  }
  function fall(o) {
    const s = S;
    s.mode = 'fall'; s.fallT = 0; s.falls++; s.combo = 0;
    Play.sfx.play('hit'); fx.shake(9, 0.35);
    const p = proj(0, s.x); fx.burst(p.x, p.y - 30, {n: 16, colors: ['#b0a8c0', '#fff', '#ffd23f'], speed: 260, shape: o.kind === 'bar' ? 'confetti' : 'circle'});
    if (c.quiz) c.quiz.item = null;   // 题目就是正在拼的这个词（服务器那边还挂着它）
    c.die();
  }

  // ------------------------------------------------------------ 画面
  function render() {
    const W = stage.W, H = stage.H, s = S, th = theme();
    stage.begin();
    const [ox, oy] = fx.offset();
    ctx.save(); ctx.translate(ox, oy);
    Play.scene.sky(ctx, W, H, th);
    Play.scene.drawClouds(ctx, W, th, clock * 3);
    const t = Play.scene.themes[th] || Play.scene.themes.day, hz = H * 0.28;
    Play.scene.hills(ctx, W, H, hz + 6, th, s.scroll * 2);
    ctx.fillStyle = t.ground; ctx.fillRect(0, hz, W, H - hz);
    // 路面（梯形）和路边的条纹
    const far = proj(ZFAR, -0.5), farR = proj(ZFAR, 2.5), nl = proj(-0.5, -0.5), nr = proj(-0.5, 2.5);
    ctx.fillStyle = ROAD[th] || ROAD.day;
    ctx.beginPath(); ctx.moveTo(far.x, far.y); ctx.lineTo(farR.x, farR.y); ctx.lineTo(nr.x + 400, nr.y + 220); ctx.lineTo(nl.x - 400, nl.y + 220); ctx.fill();
    for (let z = -((s.scroll) % 4); z < ZFAR; z += 4) {   // 分道线、路边的格子，跟着往后退
      const a = proj(z, 0), b = proj(Math.min(ZFAR, z + 1.8), 0);
      for (const ln of [0.5, 1.5]) {
        const p1 = proj(z, ln), p2 = proj(Math.min(ZFAR, z + 1.8), ln);
        ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = Math.max(1, 6 * p1.s); ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
      }
      for (const [ln, col] of [[-0.55, '#ffffff'], [2.55, '#ffffff']]) {
        const p1 = proj(z, ln), p2 = proj(Math.min(ZFAR, z + 1.8), ln);
        ctx.strokeStyle = (Math.floor((z + s.scroll) / 4) % 2) ? '#ff5d5d' : col; ctx.lineWidth = Math.max(2, 12 * p1.s);
        ctx.beginPath(); ctx.moveTo(p1.x, p1.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
      }
      if (Math.floor((z + s.scroll) / 4) % 3 === 0) { tree(proj(z, -1.6)); tree(proj(z, 3.6)); }
      void a; void b;
    }
    // 东西：远的先画
    const objs = s.objs.slice().sort((a, b) => b.z - a.z);
    let heroDrawn = false;
    for (const o of objs) {
      if (!heroDrawn && o.z < 0) { hero(); heroDrawn = true; }
      const p = proj(o.z, o.lane);
      if (o.kind === 'letter') tile(p, o.ch);
      else if (o.kind === 'rock') rock(p);
      else if (o.kind === 'bar') bar(p);
      else coin(p);
    }
    if (!heroDrawn) hero();
    if (s.mode === 'win') {   // 终点线
      ctx.font = `900 46px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.lineWidth = 8; ctx.strokeStyle = '#fff'; ctx.strokeText('🏁 终点！', W / 2, H * 0.4); ctx.fillStyle = '#ff6a00'; ctx.fillText('🏁 终点！', W / 2, H * 0.4);
    }
    ctx.restore();
    fx.draw(ctx);
    fx.drawFlash(ctx, W, H);
  }
  function hero() {
    const s = S, p = proj(0, s.x), lift = s.h * 90;
    const pose = s.mode === 'fall' ? 'hurt' : s.mode === 'win' ? 'win' : s.h > 0 ? 'jump' : s.mode === 'preview' ? 'idle' : 'run';
    if (s.safe > 0 && Math.floor(clock * 10) % 2) ctx.globalAlpha = 0.5;
    Art.hero(ctx, p.x, p.y, {avatar: s.avatar, pose, t: clock, scale: 0.8 * p.s, lift});
    ctx.globalAlpha = 1;
  }
  function tile(p, ch) {   // 浮在路上的字母牌：要的和混淆的长得一样，靠自己认
    const sz = 58 * p.s, y = p.y - 60 * p.s + Math.sin(clock * 4 + p.x) * 4 * p.s;
    ctx.fillStyle = 'rgba(30,20,60,.18)'; ctx.beginPath(); ctx.ellipse(p.x, p.y, sz * 0.45, sz * 0.12, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#fff'; Play.roundRect(ctx, p.x - sz / 2, y - sz / 2, sz, sz, sz * 0.22); ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = Math.max(1.5, 4 * p.s); ctx.stroke();
    ctx.fillStyle = '#ffe9a8'; Play.roundRect(ctx, p.x - sz / 2 + 3 * p.s, y + sz * 0.22, sz - 6 * p.s, sz * 0.22, sz * 0.12); ctx.fill();
    ctx.font = `900 ${Math.round(sz * 0.72)}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = INK; ctx.fillText(ch.toUpperCase(), p.x, y + sz * 0.04);
  }
  function rock(p) {
    const r = 34 * p.s, y = p.y - r * 0.7;
    ctx.fillStyle = '#8b8798'; ctx.beginPath(); ctx.ellipse(p.x, y, r * 1.15, r, 0, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = Math.max(1.5, 4 * p.s); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.beginPath(); ctx.ellipse(p.x - r * 0.35, y - r * 0.35, r * 0.35, r * 0.2, -0.5, 0, Math.PI * 2); ctx.fill();
  }
  function bar(p) {
    const w = 120 * p.s, h = 20 * p.s, y = p.y - 34 * p.s;
    ctx.strokeStyle = INK; ctx.lineWidth = Math.max(1.5, 4 * p.s);
    ctx.beginPath(); ctx.moveTo(p.x - w / 2 + 6 * p.s, p.y); ctx.lineTo(p.x - w / 2 + 6 * p.s, y); ctx.moveTo(p.x + w / 2 - 6 * p.s, p.y); ctx.lineTo(p.x + w / 2 - 6 * p.s, y); ctx.stroke();
    ctx.save(); Play.roundRect(ctx, p.x - w / 2, y - h, w, h, h * 0.4); ctx.clip();
    ctx.fillStyle = '#fff'; ctx.fillRect(p.x - w / 2, y - h, w, h);
    ctx.fillStyle = '#ff4d5e'; for (let i = -2; i < 8; i++) { ctx.beginPath(); const x0 = p.x - w / 2 + i * w / 6; ctx.moveTo(x0, y); ctx.lineTo(x0 + w / 12, y); ctx.lineTo(x0 + w / 6, y - h); ctx.lineTo(x0 + w / 12, y - h); ctx.fill(); }
    ctx.restore();
    Play.roundRect(ctx, p.x - w / 2, y - h, w, h, h * 0.4); ctx.stroke();
  }
  function coin(p) {
    const r = 14 * p.s, y = p.y - 40 * p.s, sq = Math.abs(Math.cos(clock * 4 + p.x * 0.05));
    ctx.fillStyle = '#ffd23f'; ctx.beginPath(); ctx.ellipse(p.x, y, r * (0.3 + 0.7 * sq), r, 0, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#c98a00'; ctx.lineWidth = Math.max(1, 3 * p.s); ctx.stroke();
  }
  function tree(p) {
    const r = 40 * p.s;
    ctx.fillStyle = '#8a5a3b'; ctx.fillRect(p.x - r * 0.12, p.y - r * 1.2, r * 0.24, r * 1.2);
    ctx.fillStyle = theme() === 'candy' ? '#ff9ccf' : theme() === 'lava' ? '#5a2a2a' : theme() === 'night' ? '#3a5a8a' : '#4fae5a';
    ctx.beginPath(); ctx.arc(p.x, p.y - r * 1.5, r * 0.8, 0, Math.PI * 2); ctx.fill();
  }

  // ------------------------------------------------------------ 上面一条：中文、字母格子（拼到哪了、亮出来几个）、第几个词、金币、连击
  function buildHud() {
    sh.hud.innerHTML = `<div class="rn-hud"><span class="rn-chip rn-coins"></span>
      <div class="rn-word"><div class="rn-zh"><span></span><button type="button" class="rn-say" aria-label="再听一遍">🔊</button></div><div class="rn-slots"></div></div>
      <span class="rn-side"><span class="rn-chip rn-count"></span><span class="rn-chip rn-combo"></span></span></div>`;
    hud = {coins: sh.hud.querySelector('.rn-coins'), count: sh.hud.querySelector('.rn-count'), combo: sh.hud.querySelector('.rn-combo'),
           zh: sh.hud.querySelector('.rn-zh span'), slots: sh.hud.querySelector('.rn-slots'), word: sh.hud.querySelector('.rn-word'), last: {}};
    const b = sh.hud.querySelector('.rn-say'); b.onclick = say; b.style.pointerEvents = 'auto';
    buildWord();
  }
  function buildWord() {
    if (!hud) return;
    const s = S;
    hud.word.classList.toggle('on', !!s.word);
    if (!s.word) { hud.slots.innerHTML = ''; hud.zh.textContent = s.loading ? '…' : ''; return; }
    hud.zh.textContent = s.item.run.zh;
    hud.slots.innerHTML = [...s.word].map((ch, i) => {
      const cls = i < s.got ? 'got' : i === s.got ? 'next' : '';
      const show = i < s.got || i < s.reveal;
      return `<span class="rn-slot ${cls}${show && i >= s.got ? ' ghost' : ''}">${show ? esc(ch.toUpperCase()) : ''}</span>`;
    }).join('');
  }
  function updateHud() {
    if (!hud || !S) return;
    const k = `${S.done}/${S.lv.words}|${S.coins}|${S.combo}`;
    if (hud.last.k === k) return;
    hud.last.k = k;
    hud.count.textContent = `🏁 ${S.done} / ${S.lv.words}`;
    hud.coins.textContent = `🪙 ${S.coins}`;
    hud.combo.textContent = `🔥 ${S.combo}`; hud.combo.classList.toggle('hot', S.combo >= 2);
  }

  function report(result) {
    const s = S, secs = Math.round(c ? c.elapsed() : 0);
    const title = result === 'win'
      ? `🏃 拼好了 ${s.done} 个词，冲过终点！<small class="rn-sub">${secs} 秒 · 一次拼对 ${s.clean} 个 · 🪙 ${s.coins}</small>`
      : `⏰ 时间到，拼好了 ${s.done} 个词<small class="rn-sub">下次接着跑</small>`;
    c.finish(result, {seconds: secs, score: s.clean}, title);
  }

  // ------------------------------------------------------------ 手机：左右滑换道、往上滑跳（屏幕下面也有按钮）
  function swipeSetup(cv) {
    cv.addEventListener('pointerdown', e => { swipe = {x: e.clientX, y: e.clientY, t: performance.now()}; Play.sfx.unlock(); });
    cv.addEventListener('pointerup', e => {
      if (!swipe || Play.input.blocked) { swipe = null; return; }
      const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y; swipe = null;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 24) return;
      if (Math.abs(dx) > Math.abs(dy)) Play.input.pressed[dx < 0 ? 'left' : 'right'] = true;
      else if (dy < 0) Play.input.pressed.jump = true;
    });
  }

  // ------------------------------------------------------------ 开始
  function boot(shell) {
    sh = shell;
    stage = Play.stage(sh.canvas, {height: 450, minW: 300, maxW: 960});
    ctx = stage.ctx;
    S = fresh(null, sh.data.avatar);
    for (let z = 4; z < ZFAR; z += ROW) S.objs.push({kind: 'coin', z, lane: 1});
    swipeSetup(sh.canvas);
    loop = Play.loop(update, render);
    loop.start();
  }
  function ready() { S = fresh(S && S.lv, sh.data.avatar); fx.parts = []; fx.texts = []; sh.hud.innerHTML = ''; hud = null; }
  function begin(cx) {
    c = cx;
    S = fresh(cx.level, (cx.match && cx.match.avatar) || sh.data.avatar);
    S.limit = Math.max(30, Math.min(600, (cx.match && !cx.match.unlimited && cx.match.seconds_left) || 600));
    S.mode = 'play';
    Play.input.clear();
    buildHud(); updateHud();
    sh.toast(`拼好 ${S.lv.words} 个词就到终点！`, 'good');
    nextWord();
  }

  const rules = [
    '上面写着要拼的词（新词整个写出来，熟了只给中文和首字母，再熟只给中文和读音）',
    '<b>← →</b> 换道，<b>↑</b> 跳；手机上<b>左右滑</b>换道、<b>往上滑</b>跳',
    '按顺序<b>撞上对的字母</b>把词拼出来；撞错了会减速，再去找对的那个',
    '<b>石头</b>要躲开，<b>栏杆</b>要跳过去；撞上就摔一跤，答对 1 道就爬起来',
    '拼好这一关的词，冲过<b>🏁 终点</b>就过关；连着一口气拼对还会加速',
  ];

  PlayShell.init({
    quiz: 'revive', tempo: 140, rules,
    keys: '电脑键盘：← → 或 A D 换道，↑ / W / 空格 跳',
    input: {left: ['ArrowLeft', 'a'], right: ['ArrowRight', 'd'], jump: ['ArrowUp', 'w', ' ']},
    touch: [['left', '◀'], ['jump', '⤒ 跳', 'big'], ['right', '▶']], touchSplit: 1,
    revive: {verb: '爬起来接着跑', done: '🎉 爬起来啦！'},
    setup: boot, ready, begin,
    end() { if (S) S.mode = S.mode === 'win' ? 'win' : 'done'; },
    onRevive() {   // 爬起来：前面的障碍收走，闪一会儿不怕撞；摔倒时那个词已经在题里答过了，换下一个
      const s = S; if (!s) return;
      s.mode = 'play'; s.safe = 1.6; s.objs = s.objs.filter(o => o.z > 10);
      s.item = null; s.word = ''; buildWord();
      nextWord();
    },
    pause(on) { if (on) Play.input.clear(); },
  });
})();

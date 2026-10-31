/* 乐园首页：角色会动、游戏卡片上的小预览、选角色、新贴纸提示。 */
(function () {
  const me = document.getElementById('ah-me');
  const avs = [...document.querySelectorAll('.ah-av canvas')];
  const previews = [...document.querySelectorAll('[data-preview]')];

  function preview(cv, t) {
    const dpr = Math.min(2, window.devicePixelRatio || 1), r = cv.getBoundingClientRect();
    if (!r.width) return;
    if (cv.width !== Math.round(r.width * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr); }
    const ctx = cv.getContext('2d'), H = 150, s = r.height / H, W = r.width / s;
    ctx.setTransform(s * dpr, 0, 0, s * dpr, 0, 0);
    const av = me.dataset.avatar, g = cv.dataset.preview, ground = 128;
    const theme = {stickman: 'sunset', race: 'day', defense: 'candy', runner: 'day', helix: 'day', jump: 'day'}[g] || 'day';
    if (multi(ctx, g, W, H, t, av)) return;
    Play.scene.sky(ctx, W, H, theme);
    Play.scene.drawClouds(ctx, W, theme, t * (g === 'race' ? 4 : 1));
    Play.scene.hills(ctx, W, H, ground, theme, g === 'race' ? t * 60 : 0);
    Play.scene.ground(ctx, W, H, ground, theme, g === 'race' ? t * 160 : 0);
    if (g === 'stickman') {
      const k = Math.sin(t * 2.2) > 0.6;
      Art.hero(ctx, W * 0.36, ground, {avatar: av, pose: k ? 'punch' : 'idle', t, scale: 0.72, weapon: 'sword'});
      Art.hero(ctx, W * 0.64, ground, {avatar: 'rival', pose: k ? 'hurt' : 'idle', t: t + 1, scale: 0.72, facing: -1});
    } else if (g === 'race') {
      Art.hero(ctx, W * 0.32, ground, {avatar: av, pose: 'run', t: t * 1.4, scale: 0.7, alpha: 0.35, seed: 3});
      Art.hero(ctx, W * 0.5 + Math.sin(t) * 20, ground, {avatar: av, pose: 'run', t: t * 1.6, scale: 0.72});
      ctx.fillStyle = '#2b2340'; for (let i = 0; i < 4; i++) for (let j = 0; j < 2; j++) if ((i + j) % 2) ctx.fillRect(W - 40 + j * 10, 40 + i * 10, 10, 10);
      ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 3; ctx.strokeRect(W - 40, 40, 20, 40); ctx.beginPath(); ctx.moveTo(W - 40, 40); ctx.lineTo(W - 40, ground); ctx.stroke();
    } else if (g === 'runner') {   // 单词跑酷：往远处收的跑道，字母牌迎面过来
      const hz = 46, cx = W / 2;
      ctx.fillStyle = '#7b7f92'; ctx.beginPath(); ctx.moveTo(cx - 14, hz); ctx.lineTo(cx + 14, hz); ctx.lineTo(cx + W * 0.42, H); ctx.lineTo(cx - W * 0.42, H); ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,.8)'; ctx.lineWidth = 2;
      for (const k of [-1, 1]) { ctx.beginPath(); ctx.moveTo(cx + k * 5, hz); ctx.lineTo(cx + k * W * 0.14, H); ctx.stroke(); }
      const k = (t * 0.6) % 1, y = hz + (H - 40 - hz) * k, sz = 10 + 26 * k;
      ['C', 'A', 'T'].forEach((ch, i) => {
        const x = cx + (i - 1) * W * 0.09 * (0.3 + k);
        ctx.fillStyle = '#fff'; Play.roundRect(ctx, x - sz / 2, y - sz, sz, sz, sz * 0.2); ctx.fill();
        ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 2; ctx.stroke();
        ctx.fillStyle = '#2b2340'; ctx.font = `900 ${Math.round(sz * 0.7)}px "Baloo 2", system-ui`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(ch, x, y - sz / 2);
      });
      Art.hero(ctx, cx, H - 6, {avatar: av, pose: 'run', t: t * 1.5, scale: 0.55});
    } else if (g === 'helix') {   // 向下跳：柱子上的圆盘转着，球在上面弹
      const cx = W / 2, R = Math.min(70, W * 0.3), rot = t * 0.8;
      ctx.fillStyle = '#fff6e0'; ctx.fillRect(cx - 12, 0, 24, H);
      for (const [y, gap] of [[60, 0], [112, 2.2]]) {
        for (let j = 0; j < 12; j++) {
          const a0 = rot + gap + j * Math.PI / 6;
          if (j < 2) continue;
          ctx.beginPath();
          for (let k = 0; k <= 4; k++) { const a = a0 + Math.PI / 6 * k / 4; ctx.lineTo(cx + R * Math.cos(a), y + R * 0.3 * Math.sin(a)); }
          for (let k = 4; k >= 0; k--) { const a = a0 + Math.PI / 6 * k / 4; ctx.lineTo(cx + 12 * Math.cos(a), y + 3.6 * Math.sin(a)); }
          ctx.fillStyle = j === 7 ? '#ff4d5a' : j % 2 ? '#ffc93c' : '#ffd45e'; ctx.fill(); ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 1.5; ctx.stroke();
        }
      }
      const by = 52 - Math.abs(Math.sin(t * 5)) * 26;
      ctx.fillStyle = Art.avatar(av).main; ctx.beginPath(); ctx.arc(cx, by, 9, 0, 7); ctx.fill(); ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 2; ctx.stroke();
    } else if (g === 'jump') {   // 往上跳：小蜜蜂在几块板之间往上弹，背景慢慢往下沉
      const sink = (t * 18) % 60;
      for (const [x, y, sp] of [[0.3, 120, 0], [0.62, 84, 1], [0.38, 48, 0], [0.7, 12, 0]]) {
        const px = W * x, py = y + sink;
        ctx.fillStyle = '#e8a300'; ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 2; Play.roundRect(ctx, px - 24, py, 48, 12, 6); ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#ffc21a'; Play.roundRect(ctx, px - 24, py, 48, 8, 5); ctx.fill(); ctx.stroke();
        if (sp) { ctx.fillStyle = '#ff5a4f'; Play.roundRect(ctx, px + 4, py - 12, 16, 6, 3); ctx.fill(); ctx.stroke(); ctx.beginPath(); ctx.moveTo(px + 8, py); ctx.lineTo(px + 16, py - 4); ctx.lineTo(px + 8, py - 8); ctx.stroke(); }
      }
      const k = (t * 0.9) % 1, bx = W * (0.3 + 0.32 * k), by = 112 - Math.sin(k * Math.PI) * 70;
      ctx.fillStyle = '#ffcf2e'; ctx.strokeStyle = '#2b2340'; ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.ellipse(bx, by, 10, 12, 0, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#2b2340'; ctx.fillRect(bx - 10, by + 3, 20, 3);
      ctx.fillStyle = '#eaf7ff'; ctx.beginPath(); ctx.ellipse(bx - 8, by - 12, 6, 8, -0.5, 0, 7); ctx.ellipse(bx + 8, by - 12, 6, 8, 0.5, 0, 7); ctx.fill(); ctx.stroke();
    } else if (g === 'defense') {
      Art.castle(ctx, 70, ground, 0.55, 1, t);
      Art.hero(ctx, 70, ground - 66, {avatar: av, pose: 'charge', t, scale: 0.5});
      Art.monster(ctx, W * 0.62 - (t * 20) % 60, ground, {kind: 'slime', t, scale: 0.8});
      Art.monster(ctx, W * 0.85 - (t * 14) % 50, ground, {kind: 'bat', t: t + 1, scale: 0.7});
      const k = (t * 0.8) % 1;
      Art.orb(ctx, 90 + (W * 0.6 - 90) * k, ground - 70 - Math.sin(k * Math.PI) * 40, 8, '#ffd23f', t);
    }
  }

  /* 联网游戏的小预览（双人闯关、泡泡堂、贪吃蛇）：自己画，不用草地背景。 */
  function multi(ctx, g, W, H, t, av) {
    if (g === 'duo') {
      ctx.fillStyle = '#3c5a45'; ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = '#8d8a6a'; ctx.fillRect(0, 118, W, 32); ctx.fillStyle = '#7fb24a'; ctx.fillRect(0, 118, W, 4);
      const pool = (x, c) => { ctx.fillStyle = c; ctx.beginPath(); ctx.moveTo(x, 132); for (let i = 0; i <= 8; i++) ctx.lineTo(x + i * 6, 121 + Math.sin(t * 4 + i) * 2); ctx.lineTo(x + 48, 132); ctx.fill(); };
      pool(W * 0.42, '#ff6b3d'); pool(W * 0.62, '#3d9bff');
      const kid = (x, fire) => {
        const y = 118 - Math.abs(Math.sin(t * 3 + (fire ? 0 : 1))) * 14;
        ctx.fillStyle = fire ? '#ff7a2f' : '#3a8fff'; Play.roundRect(ctx, x - 9, y - 22, 18, 20, 6); ctx.fill();
        ctx.fillStyle = fire ? '#ffb347' : '#7cc8ff'; ctx.beginPath(); ctx.arc(x, y - 30, 11, 0, 7); ctx.fill();
        ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x - 4, y - 30, 3, 0, 7); ctx.arc(x + 4, y - 30, 3, 0, 7); ctx.fill();
      };
      kid(W * 0.28, true); kid(W * 0.36, false);
      ctx.fillStyle = '#c4532e'; Play.roundRect(ctx, W - 70, 62, 22, 56, 9); ctx.fill();
      ctx.fillStyle = '#2f6fdc'; Play.roundRect(ctx, W - 42, 62, 22, 56, 9); ctx.fill();
      return true;
    }
    if (g === 'bubble') {
      const T = 30;
      for (let r = 0; r * T < H; r++) for (let c = 0; c * T < W; c++) { ctx.fillStyle = (r + c) % 2 ? '#ffe8f2' : '#fff4f9'; ctx.fillRect(c * T, r * T, T, T); }
      ctx.fillStyle = '#ff8fbf';
      for (let r = 1; r * T < H; r += 2) for (let c = 1; c * T < W; c += 2) { Play.roundRect(ctx, c * T + 2, r * T + 2, T - 4, T - 4, 6); ctx.fill(); }
      const k = (t * 0.6) % 1, bx = W * 0.5, by = 2 * T + T / 2;
      if (k < 0.7) { ctx.globalAlpha = 0.75; ctx.fillStyle = '#4d8dff'; ctx.beginPath(); ctx.arc(bx, by, 11 + Math.sin(t * 12) * 1.5, 0, 7); ctx.fill(); ctx.globalAlpha = 1; }
      else { ctx.fillStyle = '#5ad1ff'; ctx.fillRect(bx - 2.5 * T, by - 10, 5 * T, 20); ctx.fillRect(bx - 10, by - 2.5 * T, 20, 5 * T); }
      Art.hero(ctx, W * 0.3, 2 * T + 28, {avatar: av, pose: 'run', t: t * 1.5, scale: 0.22});
      Art.monster(ctx, W * 0.75, 2 * T + 26, {kind: 'slime', t, scale: 0.45});
      return true;
    }
    if (g === 'snake') {
      ctx.fillStyle = '#b8eb9f'; ctx.fillRect(0, 0, W, H);
      const dots = ['#ff6b6b', '#ffd23f', '#7bdff2', '#c3a6ff'];
      for (let i = 0; i < 18; i++) { ctx.fillStyle = dots[i % 4]; ctx.beginPath(); ctx.arc((i * 73) % W, 15 + (i * 41) % (H - 30), 4, 0, 7); ctx.fill(); }
      const worm = (x0, y0, ph, c1, c2) => {
        for (let i = 16; i >= 0; i--) { ctx.fillStyle = i % 4 < 2 ? c1 : c2; ctx.beginPath(); ctx.arc(x0 - i * 9, y0 + Math.sin(t * 3 + ph - i * 0.45) * 14, i ? 8 : 10, 0, 7); ctx.fill(); }
        ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x0 + 3, y0 + Math.sin(t * 3 + ph) * 14 - 4, 3, 0, 7); ctx.fill();
      };
      worm(W * 0.55 + Math.sin(t) * 10, 55, 0, '#3fcf6e', '#1f9e4c'); worm(W * 0.8 + Math.sin(t + 1) * 10, 100, 1.5, '#ff7fb8', '#e04b8f');
      return true;
    }
    if (g === 'pingpong') {
      ctx.fillStyle = '#2a3a63'; ctx.fillRect(0, 0, W, H);
      const x0 = 40, y0 = 30, w = W - 80, h = H - 60;
      ctx.fillStyle = '#1f7a5a'; Play.roundRect(ctx, x0, y0, w, h, 5); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.strokeRect(x0 + 2, y0 + 2, w - 4, h - 4);
      ctx.beginPath(); ctx.moveTo(x0, y0 + h / 2); ctx.lineTo(x0 + w, y0 + h / 2); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.fillRect(x0 + w / 2 - 2, y0 - 6, 4, h + 12);
      const k = (t * 0.7) % 2, p = k < 1 ? k : 2 - k, by = y0 + h / 2 + Math.sin(t * 1.7) * h * 0.35;
      const pad = (x, y, c) => { ctx.fillStyle = c; Play.roundRect(ctx, x - 4, y - 18, 8, 36, 4); ctx.fill(); };
      pad(x0 - 14, y0 + h / 2 + Math.sin(t * 1.7 + (k < 1 ? 0 : 0.4)) * h * 0.3, '#e8453c');
      pad(x0 + w + 14, y0 + h / 2 + Math.sin(t * 1.7 - 0.3) * h * 0.3, '#2f7de1');
      const bx = x0 - 6 + p * (w + 12);
      ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.beginPath(); ctx.ellipse(bx + 2, by + 3, 4, 2.5, 0, 0, 7); ctx.fill();
      ctx.fillStyle = '#fff6e0'; ctx.beginPath(); ctx.arc(bx, by - 8 * Math.sin(Math.PI * p), 4.5, 0, 7); ctx.fill();
      return true;
    }
    return false;
  }

  let t0 = performance.now();
  function frame(now) {
    const t = (now - t0) / 1000;
    Art.portrait(me, me.dataset.avatar, 'idle');
    previews.forEach(cv => preview(cv, t));
    requestAnimationFrame(frame);
  }
  avs.forEach(cv => Art.portrait(cv, cv.dataset.avatar, 'idle'));
  requestAnimationFrame(frame);

  document.querySelectorAll('.ah-av').forEach(b => b.onclick = async () => {
    if (b.classList.contains('lock')) { Play.sfx.play('wrong'); return; }
    try {
      await api('/api/arena/avatar', {avatar: b.dataset.av});
      document.querySelectorAll('.ah-av').forEach(x => x.classList.toggle('on', x === b));
      me.dataset.avatar = b.dataset.av; Play.sfx.play('pop');
      Art.portrait(b.querySelector('canvas'), b.dataset.av, 'win');
    } catch (e) { alert(e.message); }
  });
  const nick = document.getElementById('ah-nick');   // 昵称：就地改，改好了马上换上
  if (nick) {
    const show = nick.querySelector('.ah-nick-show'), row = nick.querySelector('.ah-nick-row'), err = nick.querySelector('.ah-nick-err'), inp = nick.nickname;
    const open = on => { show.hidden = on; row.hidden = !on; err.hidden = true; if (on) { inp.focus(); inp.select(); } };
    nick.querySelector('.ah-nick-edit').onclick = () => { Play.sfx.play('click'); open(true); };
    nick.querySelector('.ah-nick-back').onclick = () => open(false);
    nick.onsubmit = async e => {
      e.preventDefault();
      try {
        const v = (await api('/api/arena/nickname', {nickname: inp.value})).nickname;
        show.querySelector('b').textContent = inp.value = v;
        nick.querySelector('.ah-nick-edit').textContent = '✏️ 改一改';
        open(false); Play.sfx.play('pop');
        show.classList.remove('done'); void show.offsetWidth; show.classList.add('done');
      } catch (x) { err.textContent = x.message; err.hidden = false; Play.sfx.play('wrong'); }
    };
  }
  me.onclick = () => document.querySelector('.ah-avatars').scrollIntoView({behavior: 'smooth', block: 'center'});

  if (window.NEW_STICKERS && NEW_STICKERS.length) {
    const pop = document.createElement('div'); pop.className = 'pl-sticker-pop';
    pop.innerHTML = `<div class="pl-sticker-card"><div class="pl-label">得到新贴纸！</div>${NEW_STICKERS.map(s =>
      `<div class="pl-sticker"><span class="ico">${s.icon}</span><b>${esc(s.name)}</b><small>${esc(s.how)}</small></div>`).join('')}
      <button type="button" class="btn lg block">太棒了！</button></div>`;
    document.body.appendChild(pop);
    pop.querySelector('button').onclick = () => { pop.remove(); location.reload(); };
  }
})();

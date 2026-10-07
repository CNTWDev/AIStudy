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
    const theme = {stickman: 'sunset', race: 'day', defense: 'candy'}[g] || 'day';
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
    } else if (g === 'defense') {
      Art.castle(ctx, 70, ground, 0.55, 1, t);
      Art.hero(ctx, 70, ground - 66, {avatar: av, pose: 'charge', t, scale: 0.5});
      Art.monster(ctx, W * 0.62 - (t * 20) % 60, ground, {kind: 'slime', t, scale: 0.8});
      Art.monster(ctx, W * 0.85 - (t * 14) % 50, ground, {kind: 'bat', t: t + 1, scale: 0.7});
      const k = (t * 0.8) % 1;
      Art.orb(ctx, 90 + (W * 0.6 - 90) * k, ground - 70 - Math.sin(k * Math.PI) * 40, 8, '#ffd23f', t);
    }
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

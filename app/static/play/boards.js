/* 排行榜页的小动画：领奖台上的角色在欢呼、数字从 0 涨上来、有进步就撒彩带。系统设置了「减少动画」就都不动。 */
(function () {
  const still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cvs = [...document.querySelectorAll('.bd-podium canvas[data-av]')];

  function draw() { cvs.forEach((cv, i) => Art.portrait(cv, cv.dataset.av, cv.dataset.pose || 'idle', {t: performance.now() / 1000 + i})); }
  if (window.Art) {
    draw();
    if (!still) (function frame() { draw(); requestAnimationFrame(frame); })();
  }

  if (!still) for (const el of document.querySelectorAll('[data-count]')) {
    const to = +el.dataset.count, t0 = performance.now(), dur = 700;
    if (!(to > 1)) continue;
    (function tick(now) {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(to * e);
      if (k < 1) requestAnimationFrame(tick);
    })(t0);
  }

  const nick = document.getElementById('bd-nick');   // 还没起昵称：起好就刷新，榜上马上换成新昵称
  if (nick) nick.addEventListener('submit', async e => {
    e.preventDefault();
    const err = nick.querySelector('.bd-nick-err');
    const r = await fetch('/api/arena/nickname', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({nickname: nick.nickname.value})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { err.textContent = d.error || d.detail || '这个昵称不行，换一个试试'; return; }
    location.reload();
  });

  // 起过昵称：点榜上自己的名字（带「我」的那个），弹出来就地改
  document.querySelectorAll('.bd-nick-me').forEach(btn => btn.onclick = () => {
    const old = btn.firstChild.textContent.trim();
    const pop = document.createElement('div');
    pop.className = 'bd-pop';
    pop.innerHTML = `<form class="bd-card bd-nick bd-pop-card" autocomplete="off">
      <div class="bd-nick-h">😎 改个昵称</div>
      <p>榜上和一起玩的小伙伴只看到昵称，别用自己的真名哦。</p>
      <div class="bd-nick-row"><input name="nickname" maxlength="10" required aria-label="新昵称"><button type="submit" class="btn">就叫这个</button></div>
      <p class="bd-nick-err" aria-live="polite"></p>
      <button type="button" class="btn ghost sm bd-pop-x">算了</button></form>`;
    document.body.appendChild(pop);
    const f = pop.querySelector('form');
    f.nickname.value = old; f.nickname.focus(); f.nickname.select();
    const close = () => pop.remove();
    pop.querySelector('.bd-pop-x').onclick = close;
    pop.onclick = e => { if (e.target === pop) close(); };
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    f.onsubmit = async e => {
      e.preventDefault();
      try { await api('/api/arena/nickname', {nickname: f.nickname.value}); }
      catch (x) { f.querySelector('.bd-nick-err').textContent = x.message; return; }
      location.reload();
    };
  });

  if (window.BD_PARTY && !still) {
    const box = document.createElement('div'), colors = ['#ffd23f', '#ff6b9a', '#4dc3ff', '#6bdb7a', '#b388ff', '#ff9f1c'];
    box.className = 'bd-confetti';
    for (let i = 0; i < 40; i++) {
      const p = document.createElement('i');
      p.style.left = Math.random() * 100 + '%';
      p.style.background = colors[i % colors.length];
      p.style.setProperty('--x', (Math.random() * 160 - 80) + 'px');
      p.style.setProperty('--r', (Math.random() * 720 - 360) + 'deg');
      p.style.animationDelay = (Math.random() * 0.5) + 's';
      box.appendChild(p);
    }
    document.body.appendChild(box);
    setTimeout(() => box.remove(), 3200);
  }
})();

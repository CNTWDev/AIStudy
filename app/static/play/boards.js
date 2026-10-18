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

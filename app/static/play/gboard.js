/* 游戏页上这款游戏的榜（arena_game.html + _gboard.html，docs/DESIGN.md 5.55）：
   榜在眼前、页面在前台时每 EVERY 毫秒拉一次服务器画好的那一段（/arena/<游戏>/board），按 data-id 对上每个人：
   - 数字从原来的往上数，旁边飘一个「+N」；
   - 名次变了：这一行从原来的位置滑到新位置（FLIP），名次牌翻一下，往前走的那一行亮一下；
   - 新上榜的淡入。
   榜看不见（滚走了、切到别的页面）就不拉；拉不到就慢慢放长间隔。系统设了「减少动态效果」就只换数字不动。 */
(() => {
  const box = document.getElementById('gboard');
  if (!box) return;
  const EVERY = 4000, MAX = 60000;
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let seen = true, wait = EVERY, timer = 0, last = '';

  box.addEventListener('click', e => {
    const t = e.target.closest('[data-show]'), m = e.target.closest('[data-more]');
    if (t) { for (const x of box.querySelectorAll('[data-show]')) x.classList.toggle('on', x === t);
             for (const x of box.querySelectorAll('.gb-tab')) x.classList.toggle('on', x.dataset.tab === t.dataset.show); }
    if (m) { m.closest('.gb-tab').classList.add('all'); m.remove(); }
  });

  // 数字往上数：差得多也不超过 0.9 秒
  function count(el, from, to) {
    const t0 = performance.now(), ms = Math.min(900, 150 + 60 * (to - from));
    const step = now => {
      const k = Math.min(1, (now - t0) / ms);
      el.textContent = Math.round(from + (to - from) * (1 - (1 - k) ** 3));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function plus(row, n) {
    const p = document.createElement('i');
    p.className = 'gb-plus';
    p.textContent = '+' + n;
    row.querySelector('.bv').append(p);
    p.addEventListener('animationend', () => p.remove());
  }

  function patch(html) {
    if (html === last) return;
    last = html;
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    for (const fresh of tmp.querySelectorAll('.gb-tab')) {
      const old = box.querySelector(`.gb-tab[data-tab="${fresh.dataset.tab}"]`);
      if (!old) continue;
      const on = old.classList.contains('on');
      fresh.classList.toggle('on', on);   // 孩子选的那一页、展开过的前 50 名都留着
      if (old.classList.contains('all')) { fresh.classList.add('all'); fresh.querySelector('[data-more]')?.remove(); }
      const before = new Map();
      for (const r of old.querySelectorAll('.brow[data-id]')) {
        const rc = r.getBoundingClientRect();
        before.set(r.dataset.id, { top: rc.top, h: rc.height, v: +r.dataset.v, rank: +r.dataset.rank });
      }
      old.replaceWith(fresh);
      if (!on || !before.size) continue;   // 没看着的那一页、原来空着的榜：直接换上
      for (const r of fresh.querySelectorAll('.brow[data-id]')) {
        const b = before.get(r.dataset.id), v = +r.dataset.v, rank = +r.dataset.rank;
        if (!b) { if (!calm) r.classList.add('gb-new'); continue; }
        if (v > b.v) {
          if (calm) continue;
          count(r.querySelector('.bv b'), b.v, v);
          plus(r, v - b.v);
        }
        if (calm || rank === b.rank) continue;
        const now = r.getBoundingClientRect(), dy = b.top - now.top;
        if (dy && b.h && now.height)
          r.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }], { duration: 650, easing: 'cubic-bezier(.2,.8,.2,1)' });
        r.querySelector('.bk').animate([{ transform: 'rotateX(90deg)' }, { transform: 'none' }], { duration: 450, delay: 250, fill: 'backwards' });
        if (rank < b.rank) r.classList.add('gb-up');
      }
    }
  }

  async function tick() {
    timer = 0;
    if (document.hidden || !seen) return;   // 看见了再接着拉（下面两个监听会叫醒）
    try {
      const res = await fetch(box.dataset.live, { credentials: 'same-origin', cache: 'no-store' });
      if (res.redirected || res.status === 401 || res.status === 403 || res.status === 404) return;   // 登录过期、游戏没了：停
      if (!res.ok) throw new Error(res.status);
      patch(await res.text());
      wait = EVERY;
    } catch (e) {
      wait = Math.min(MAX, wait * 2);
    }
    schedule();
  }

  function schedule(now) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(tick, now ? 0 : wait);
  }

  new IntersectionObserver(es => {
    const was = seen;
    seen = es.some(e => e.isIntersecting);
    if (seen && !was) schedule(true);
  }).observe(box);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(true); });
  schedule();
})();

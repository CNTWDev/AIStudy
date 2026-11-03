/* 过关仪式（DESIGN 5.64）：所有乐园游戏共用，shell.js 赢了才叫它。总共不到 2.5 秒，点一下（或按空格、回车）就跳到结算面板。
 *
 *   const fx = Fanfare.play(stageEl, {scene, until: endEl});   // ① 定格：一碰到终点就开始（不等服务器）
 *   fx.go({tier, stamp, badge, finale});                       // ② 庆祝：服务器回来、知道是哪一档了再演（最早 0.6 秒）
 *   await fx.ready;                                             // 章盖完（或者点了跳过）→ 出结算面板
 *   fx.stars(endEl, n);                                         // ③ 收获：面板上的星星一颗颗亮，一颗一个音，满 3 颗全屏一闪金光
 *
 * ① 定格 0～0.6 秒：游戏慢放到 0.2 倍（Play.slow），四周压暗，一声上扬的「嗖」
 * ② 庆祝 0.6～1.6 秒：一道光扫过、白光一闪、这个世界颜色的光芒慢慢转，「过关！」从大到小砸下来，画面震一下；
 *    两边喷彩带 + 中间一圈蜂蜜，撒的东西按世界换（晴空花瓣、糖果、黄昏落叶、星空星星、火山火星、冰雪雪花、海底泡泡、山洞宝石）；
 *    游戏可以另外给一段自己世界里的收尾：def.finale(sh, {tier, scene})，外壳在这一拍叫它
 * ③ 收获 1.6 秒起：结算面板浮在光芒上，星星一颗颗亮
 * 四档，越难得越隆重：win 过关 → 3 颗星（第三颗全屏金光 + 和弦）→ 新纪录（章下面一枚「新纪录！」，面板上旧纪录划掉）
 *    → world 打通一个世界 / all 全部打通（烟花、小助手出来欢呼、多一段音乐、满屏彩带）
 * 全部放在游戏画面（.pl-stage，本身 overflow:hidden）里面，不放在能滚动的结算面板里（DESIGN 5.62）。
 * 一直动的只有慢慢转的光芒，转一会儿就停；彩带撒一阵就落完。结算面板一关（下一关、再来、回房间）全部收掉。
 * 成人：安静的版本，只有定格和星星。系统设了「减少动态效果」：只有淡入（压暗、章、面板），不撒、不转、不震。
 */
(function () {
  const F = window.Fanfare = {};
  const J = window.Juice || {sfx: {play() {}}, buzz() {}, who: () => 'kid', still: () => false};
  const INK = '#2b2340';

  // 每个世界的样子：光芒颜色、撒什么、什么颜色（场景名见 app/arena/games/*.py 的 worlds）
  const LOOKS = {
    day:    {ray: '255,236,150', bits: ['petal', 'confetti'], colors: ['#ffd23f', '#ff8fb8', '#7fd3ff', '#7ee08a', '#ffffff']},
    candy:  {ray: '255,190,220', bits: ['candy', 'confetti'], colors: ['#ff6fa3', '#ffb3d1', '#8ee3ff', '#ffe066', '#b98cff']},
    sunset: {ray: '255,190,120', bits: ['leaf', 'confetti'], colors: ['#ff9f43', '#ff6b6b', '#ffd23f', '#c56cf0']},
    night:  {ray: '190,170,255', bits: ['star', 'star', 'confetti'], colors: ['#fff6c2', '#ffd23f', '#b8a6ff', '#8fd8ff']},
    lava:   {ray: '255,170,90', bits: ['ember', 'confetti'], colors: ['#ff7a1a', '#ffd23f', '#ff3d3d', '#ffb15c']},
    ice:    {ray: '200,240,255', bits: ['flake', 'confetti'], colors: ['#ffffff', '#bfe9ff', '#8fd8ff', '#b8a6ff']},
    sea:    {ray: '180,240,255', bits: ['bubble', 'confetti'], colors: ['#7fe3ff', '#ffffff', '#5fd3b5', '#ffd23f']},
    forest: {ray: '220,255,170', bits: ['leaf', 'confetti'], colors: ['#7ee08a', '#3fbf8f', '#ffd23f', '#c1f07a']},
    cave:   {ray: '215,195,255', bits: ['gem', 'confetti'], colors: ['#b98cff', '#7fe3ff', '#ff8fd8', '#ffffff']},
    desert: {ray: '255,224,160', bits: ['confetti', 'petal'], colors: ['#ffd23f', '#ffb347', '#ff6fa3', '#7fd3ff']},
  };
  Object.assign(LOOKS, {snow: LOOKS.ice, volcano: LOOKS.lava, jungle: LOOKS.forest});
  const PLAIN = {ray: '255,236,150', bits: ['confetti'], colors: ['#ffd23f', '#ff6fa3', '#4d96ff', '#3fbf8f', '#7b5cff']};
  const GOLD = ['#ffd23f', '#ffe98a', '#ffb31a', '#ffffff'], HONEY = ['#ffc21a', '#ffb000', '#ffd23f'];
  const FREEZE = 600, STAMP = 1000;   // 毫秒：定格多久；章从砸下来到收起多久
  F.looks = LOOKS;

  let live = null;   // 同一时间只有一场

  F.play = function (stage, o) {
    o = o || {};
    if (live) live.stop();
    const look = LOOKS[o.scene] || PLAIN, calm = J.still ? J.still() : false, quiet = J.who() === 'adult';
    const t0 = performance.now();
    let alive = true, running = false, last = t0, raf = 0, tier = 'win', went = false, skipped = false;
    const timers = [];
    const later = (ms, f) => timers.push(setTimeout(() => { if (alive) f(); }, ms));

    // ---------------------------------------------------------------- 后面一层：压暗、光扫、白光、光芒
    const back = el('div', 'fx-back' + (calm ? ' calm' : ''));
    back.style.setProperty('--ray', look.ray);
    back.innerHTML = '<i class="fx-rays"></i><i class="fx-sweep"></i><i class="fx-flash"></i>';
    // ---------------------------------------------------------------- 前面一层：画布、章、小助手
    const front = el('div', 'fx-front');
    const cv = el('canvas', 'fx-cv'), g = cv.getContext('2d');
    front.appendChild(cv);
    stage.appendChild(back); stage.appendChild(front);
    if (o.until) o.until.classList.add('fx-win');

    // ① 定格：慢放、压暗、嗖
    if (window.Play && !calm) { Play.slow = 0.2; later(FREEZE, () => { Play.slow = 1; }); }
    J.sfx.play('rise');

    let done;
    const ready = new Promise(r => { done = r; });
    function skip(e) {   // 点一下：不等章盖完，马上出面板（按钮还是要再点一下，不会误按到）
      if (skipped || (e && e.type === 'keydown' && !/^( |Enter|Escape)$/.test(e.key))) return;
      skipped = true; if (e && e.type === 'keydown') e.preventDefault();
      if (window.Play) Play.slow = 1;
      front.querySelectorAll('.fx-stamp,.fx-pal').forEach(s => s.classList.add('out'));
      unhook(); done();
    }
    const unhook = () => { stage.removeEventListener('pointerdown', skip, true); removeEventListener('keydown', skip, true); };
    stage.addEventListener('pointerdown', skip, true); addEventListener('keydown', skip, true);

    // ② 庆祝
    function go(p) {
      if (went || !alive) return; went = true;
      const wait = skipped ? 0 : Math.max(0, FREEZE - (performance.now() - t0));
      if (wait) later(wait, () => celebrate(p || {})); else celebrate(p || {});
    }
    function celebrate(p) {
      tier = p.tier || 'win';
      const big = tier === 'world' || tier === 'all';
      if (p.finale) try { p.finale({tier, scene: o.scene || ''}); } catch (e) { /* 游戏自己的收尾坏了，照样庆祝 */ }
      J.sfx.play('fanfare', 0, big ? 2 : 1);
      if (J.buzz) J.buzz('done');
      if (skipped) return;
      if (quiet) { later(300, () => { unhook(); done(); }); return; }
      if (tier === 'all') back.classList.add('gold');
      back.classList.add('go');
      const stamp = el('div', 'fx-stamp' + (calm ? ' calm' : ''));
      stamp.innerHTML = `<b>${esc(p.stamp || '过关！')}</b>${p.badge ? `<span>${esc(p.badge)}</span>` : ''}<i>✦</i><i>✦</i><i>✦</i>`;
      front.appendChild(stamp);
      if (big) pal();
      if (!calm) {
        stage.classList.remove('fx-shake'); void stage.offsetWidth; stage.classList.add('fx-shake');
        cannon(true, big ? 64 : 44); cannon(false, big ? 64 : 44);
        later(120, () => ring(W() / 2, H() * 0.42, 26, HONEY, Math.min(W(), 700) * 0.85, 'honey'));
        later(160, () => ring(W() / 2, H() * 0.42, 30, GOLD, Math.min(W(), 700) * 1.1, 'spark'));
        if (big) for (let i = 0; i < (tier === 'all' ? 7 : 4); i++) later(350 + i * (tier === 'all' ? 300 : 380), rocket);
        ambientFrom = (performance.now() - t0) / 1000 + 0.4;
        ambientEnd = ambientFrom + (tier === 'all' ? 5 : big ? 4 : 2.6);
        kick();
      }
      later(calm ? 400 : STAMP, () => { stamp.classList.add('out'); unhook(); done(); });
      later(9000, () => back.classList.add('rest'));   // 光芒转一会儿就停
    }
    function pal() {   // 打通世界：小助手从左下角跳出来欢呼（页面上 <template id="fx-pal"> 里放着它开心的样子）
      const tpl = document.getElementById('fx-pal');
      if (!tpl) return;
      const p = el('div', 'fx-pal' + (calm ? ' calm' : ''));
      p.appendChild(tpl.content.cloneNode(true));
      p.insertAdjacentHTML('beforeend', '<em>太厉害了！</em>');
      front.appendChild(p);
      later(STAMP + 900, () => p.classList.add('out'));
    }

    // ---------------------------------------------------------------- 粒子
    const parts = [], W = () => stage.clientWidth, H = () => stage.clientHeight;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const fit = () => { cv.width = Math.round(W() * dpr); cv.height = Math.round(H() * dpr); };
    fit();
    const pick = a => a[Math.floor(Math.random() * a.length)];
    const rnd = (a, b) => a + Math.random() * (b - a);
    function bit(kind, x, y, vx, vy, color, extra) {
      parts.push(Object.assign({kind, x, y, vx, vy, color, rot: rnd(0, 6.28), spin: rnd(-8, 8), flip: rnd(0, 6.28),
                                size: rnd(6, 11), life: 0, max: rnd(3.2, 4.6), sway: rnd(0, 6.28)}, extra || {}));
    }
    function cannon(fromLeft, n) {   // 从左下、右下两角往中间上方喷
      const w = W(), h = H();
      for (let i = 0; i < n; i++) {
        const a = -Math.PI / 2 + (fromLeft ? 1 : -1) * rnd(0.18, 0.85), sp = rnd(0.85, 1.3) * Math.max(h, 300);
        bit(pick(look.bits), fromLeft ? -6 : w + 6, h * 0.92, Math.cos(a) * sp, Math.sin(a) * sp, pick(look.colors));
      }
    }
    function ring(x, y, n, colors, speed, kind) {   // 一圈炸开
      for (let i = 0; i < n; i++) {
        const a = (i / n) * 6.283 + rnd(-0.12, 0.12), sp = speed * rnd(0.55, 1);
        bit(kind, x, y, Math.cos(a) * sp, Math.sin(a) * sp, pick(colors),
            kind === 'honey' ? {max: rnd(1.4, 2), size: rnd(7, 11), rot: a + 1.5708, spin: 0} : {max: rnd(0.7, 1.2), size: rnd(2, 3.6)});
      }
      kick();
    }
    function rocket() {   // 烟花：从底下升上去，到顶炸开
      const w = W(), h = H(), x = rnd(w * 0.15, w * 0.85);
      bit('rocket', x, h + 4, rnd(-40, 40), -rnd(1.25, 1.5) * h, '#fff7d0',
          {max: 9, top: rnd(h * 0.12, h * 0.38), colors: Math.random() < 0.5 ? GOLD : look.colors});
    }
    let ambientFrom = 0, ambientEnd = 0, ambientAcc = 0;
    function ambient(dt, t) {   // 喷完一阵后，上面还慢慢飘下来一些（火星、泡泡从下面往上飘）
      if (t > ambientEnd || t < ambientFrom) return;
      ambientAcc += dt * (tier === 'all' ? 16 : tier === 'world' ? 11 : 6);
      while (ambientAcc >= 1) {
        ambientAcc -= 1;
        const gold = tier === 'all' && Math.random() < 0.5, kind = gold ? 'confetti' : pick(look.bits), up = kind === 'ember' || kind === 'bubble';
        bit(kind, rnd(0, W()), up ? H() + 10 : -14, rnd(-20, 20), up ? -rnd(60, 120) : rnd(20, 70), gold ? pick(GOLD) : pick(look.colors), {max: 6});
      }
    }
    function kick() {   // 画布没在跑就让它跑起来
      if (running || !alive || calm) return;
      if (!cv.isConnected) front.insertBefore(cv, front.firstChild);
      running = true; last = performance.now(); raf = requestAnimationFrame(frame);
    }
    function frame(now) {
      if (!alive) return;
      const dt = Math.min(0.05, (now - last) / 1000), t = (now - t0) / 1000; last = now;
      if (cv.width !== Math.round(W() * dpr)) fit();
      ambient(dt, t);
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W(), H());
      for (let i = parts.length - 1; i >= 0; i--) {
        const p = parts[i];
        p.life += dt;
        if (step(p, dt)) { parts.splice(i, 1); continue; }
        draw(g, p);
      }
      if (parts.length || t < ambientEnd) raf = requestAnimationFrame(frame);
      else { running = false; cv.remove(); }
    }
    function step(p, dt) {
      if (p.kind === 'rocket') {
        p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 260 * dt;
        if (Math.random() < 0.8) bit('spark', p.x, p.y + 4, rnd(-20, 20), rnd(20, 60), '#ffe9a8', {max: 0.4, size: 1.8});
        if (p.y <= p.top || p.vy >= 0) {
          ring(p.x, p.y, 42, p.colors, rnd(180, 260), 'spark');
          ring(p.x, p.y, 14, ['#ffffff'], 90, 'spark');
          J.sfx.play('burst');
          return true;
        }
        return false;
      }
      if (p.kind === 'spark') {
        p.vx *= Math.pow(0.12, dt); p.vy *= Math.pow(0.12, dt); p.vy += 140 * dt;
      } else if (p.kind === 'honey') {   // 蜂蜜：甩出去，再往下掉
        p.vx *= Math.pow(0.2, dt); p.vy = p.vy * Math.pow(0.2, dt) + 900 * dt;
        p.rot = Math.atan2(p.vy, p.vx) - 1.5708;
      } else if (p.kind === 'ember' || p.kind === 'bubble') {   // 往上飘
        p.vx *= Math.pow(0.3, dt); p.vy = p.vy * Math.pow(0.3, dt) + (-90 - p.vy) * 0.02;
        p.x += Math.sin(p.life * 3 + p.sway) * (p.kind === 'bubble' ? 30 : 18) * dt;
      } else {   // 彩带、花瓣、叶子：冲上去，再晃晃悠悠飘下来
        p.vx *= Math.pow(0.25, dt); p.vy = p.vy * Math.pow(0.25, dt) + 520 * dt;
        if (p.vy > 110) p.vy = 110;
        p.x += Math.sin(p.life * 2.4 + p.sway) * 26 * dt;
      }
      p.x += p.vx * dt; p.y += p.vy * dt; p.rot += p.spin * dt; p.flip += dt * 7;
      return p.life > p.max || p.y > H() + 30 || p.y < -60;
    }

    // ③ 收获：面板上亮起的星星跟着 CSS 的 animation-delay，一颗一个音、炸一圈；满 3 颗时第三颗全屏一闪金光
    function stars(root) {
      if (!root || !alive) return;
      const on = [...root.querySelectorAll('.ps-stars span.on')], full = on.length >= 3;
      on.forEach((s, i) => {
        const d = parseFloat(getComputedStyle(s).animationDelay) || 0;
        later((d + 0.28) * 1000, () => {
          J.sfx.play('starN', 0, i + 1);
          if (full && i === 2) { J.sfx.play('chord', 0.08, 10); flash('fx-gold'); }
          if (calm) return;
          const a = s.getBoundingClientRect(), b = stage.getBoundingClientRect();
          ring(a.left + a.width / 2 - b.left, a.top + a.height / 2 - b.top, full && i === 2 ? 30 : 18, GOLD, full && i === 2 ? 220 : 150, 'spark');
        });
      });
    }
    function flash(cls) { const f = el('i', cls); back.appendChild(f); later(900, () => f.remove()); }

    // 结算面板一关，全部收掉
    let mo = null;
    if (o.until) {
      mo = new MutationObserver(() => { if (o.until.hidden && (went || skipped)) stop(); });
      mo.observe(o.until, {attributes: true, attributeFilter: ['hidden']});
    }
    function stop() {
      if (!alive) return;
      alive = false; running = false; cancelAnimationFrame(raf); timers.forEach(clearTimeout); unhook();
      if (mo) mo.disconnect();
      if (window.Play) Play.slow = 1;
      stage.classList.remove('fx-shake');
      if (o.until) o.until.classList.remove('fx-win');
      back.remove(); front.remove(); done();
      if (live === api) live = null;
    }
    const api = {ready, go, stop, stars, get tier() { return tier; }};
    live = api;
    return api;
  };
  F.stop = () => { if (live) live.stop(); };

  // ---------------------------------------------------------------- 画一个粒子
  function draw(g, p) {
    const fade = Math.min(1, (p.max - p.life) / 0.5);
    g.save(); g.globalAlpha = Math.max(0, fade); g.translate(p.x, p.y);
    const s = p.size;
    switch (p.kind) {
      case 'spark': case 'rocket': {
        const r = p.kind === 'rocket' ? 3 : s;
        g.globalCompositeOperation = 'lighter'; g.fillStyle = p.color;
        g.beginPath(); g.arc(0, 0, r, 0, 6.283); g.fill();
        g.globalAlpha *= 0.35; g.beginPath(); g.arc(0, 0, r * 2.6, 0, 6.283); g.fill();
        break;
      }
      case 'honey':   // 一滴蜂蜜：尖头朝飞的方向，描边
        g.rotate(p.rot); g.fillStyle = p.color; g.strokeStyle = INK; g.lineWidth = 2;
        g.beginPath(); g.moveTo(0, -s * 1.1); g.bezierCurveTo(s * 0.75, -s * 0.1, s * 0.6, s * 0.7, 0, s * 0.7);
        g.bezierCurveTo(-s * 0.6, s * 0.7, -s * 0.75, -s * 0.1, 0, -s * 1.1); g.fill(); g.stroke();
        g.fillStyle = 'rgba(255,255,255,.75)'; g.beginPath(); g.arc(-s * 0.2, s * 0.1, s * 0.16, 0, 6.283); g.fill();
        break;
      case 'ember':
        g.globalCompositeOperation = 'lighter';
        g.globalAlpha *= 0.6 + 0.4 * Math.sin(p.life * 14 + p.sway);
        g.fillStyle = p.color; g.beginPath(); g.arc(0, 0, s * 0.35, 0, 6.283); g.fill();
        g.globalAlpha *= 0.4; g.beginPath(); g.arc(0, 0, s * 0.9, 0, 6.283); g.fill();
        break;
      case 'bubble':
        g.strokeStyle = p.color; g.lineWidth = 2; g.beginPath(); g.arc(0, 0, s * 0.7, 0, 6.283); g.stroke();
        g.fillStyle = 'rgba(255,255,255,.8)'; g.beginPath(); g.arc(-s * 0.25, -s * 0.25, s * 0.18, 0, 6.283); g.fill();
        break;
      case 'star':
        g.rotate(p.rot); g.scale(1, 0.6 + 0.4 * Math.abs(Math.cos(p.flip)));
        g.fillStyle = p.color; g.strokeStyle = INK; g.lineWidth = 1.5; starPath(g, s * 0.85); g.fill(); g.stroke();
        break;
      case 'flake':
        g.rotate(p.rot); g.strokeStyle = p.color; g.lineWidth = 2; g.lineCap = 'round'; g.beginPath();
        for (let i = 0; i < 3; i++) { g.rotate(1.047); g.moveTo(-s * 0.7, 0); g.lineTo(s * 0.7, 0); }
        g.stroke();
        break;
      case 'petal':
        g.rotate(p.rot); g.scale(1, Math.cos(p.flip));
        g.fillStyle = p.color; g.beginPath(); g.ellipse(0, 0, s * 0.75, s * 0.4, 0, 0, 6.283); g.fill();
        break;
      case 'leaf':
        g.rotate(p.rot); g.scale(1, Math.cos(p.flip)); g.fillStyle = p.color; g.beginPath();
        g.moveTo(-s, 0); g.quadraticCurveTo(0, -s * 0.7, s, 0); g.quadraticCurveTo(0, s * 0.7, -s, 0); g.fill();
        g.strokeStyle = 'rgba(43,35,64,.35)'; g.lineWidth = 1; g.beginPath(); g.moveTo(-s * 0.8, 0); g.lineTo(s * 0.8, 0); g.stroke();
        break;
      case 'candy':
        g.rotate(p.rot); g.fillStyle = p.color; g.strokeStyle = INK; g.lineWidth = 1.5;
        for (const d of [-1, 1]) { g.beginPath(); g.moveTo(d * s * 0.45, 0); g.lineTo(d * s * 1.05, -s * 0.4); g.lineTo(d * s * 1.05, s * 0.4); g.closePath(); g.fill(); g.stroke(); }
        g.beginPath(); g.arc(0, 0, s * 0.5, 0, 6.283); g.fill(); g.stroke();
        g.fillStyle = 'rgba(255,255,255,.75)'; g.beginPath(); g.arc(-s * 0.15, -s * 0.18, s * 0.14, 0, 6.283); g.fill();
        break;
      case 'gem':
        g.rotate(p.rot); g.scale(Math.cos(p.flip), 1); g.fillStyle = p.color; g.strokeStyle = INK; g.lineWidth = 1.5;
        g.beginPath(); g.moveTo(0, -s * 0.8); g.lineTo(s * 0.55, 0); g.lineTo(0, s * 0.8); g.lineTo(-s * 0.55, 0); g.closePath(); g.fill(); g.stroke();
        break;
      default:   // confetti：翻着飘的小纸片
        g.rotate(p.rot); g.scale(1, Math.cos(p.flip));
        g.fillStyle = p.color; g.fillRect(-s / 2, -s / 4, s, s / 2);
    }
    g.restore();
  }
  function starPath(g, r) {
    g.beginPath();
    for (let i = 0; i < 10; i++) { const a = -1.5708 + i * 0.6283, rr = i % 2 ? r * 0.45 : r; g.lineTo(Math.cos(a) * rr, Math.sin(a) * rr); }
    g.closePath();
  }
  function el(tag, cls) { const e = document.createElement(tag); e.className = cls; return e; }
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]); }
})();

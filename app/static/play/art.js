/* 乐园的角色和怪兽画法（游戏原型素材）：全部用代码画矢量图，不用图片，任何屏幕都清楚，深色模式也好看。
   风格：圆头大眼的「可爱火柴人」——粗粗的圆头四肢、贴纸一样的深色描边、会眨眼、有表情。
   Art.hero(ctx, x, y, o)     x, y 是脚底中点；o = {avatar, pose, t, facing, scale, weapon, shield, alpha, tint}
     pose: idle 站 / walk 走 / run 跑 / jump 跳 / punch 出拳 / hurt 挨打 / charge 答题充能 / win 欢呼 / lose 难过 / freeze 冷冻
   Art.monster(ctx, x, y, o)  o = {kind: slime | bat | ghost | boss, t, scale, hurt, color, facing}
   Art.portrait(canvas, avatar, pose)  在小画布上画一个角色（乐园首页、选角色用）
   角色列表和解锁条件在服务器 app/arena/awards.py（AVATARS），这里只管样子。 */
(function () {
  const Art = window.Art = {};
  const INK = '#2b2340';

  Art.AVATARS = {
    mint: {main: '#3fbf8f', head: '#fff4e3', acc: 'sprout'},
    sunny: {main: '#ff9f1c', head: '#fff1dc', acc: 'spikes', hair: '#ffcf33'},
    berry: {main: '#ff6fa3', head: '#fff0ea', acc: 'bow'},
    sky: {main: '#4d96ff', head: '#fff3e6', acc: 'band'},
    cap: {main: '#ef476f', head: '#fff1e2', acc: 'cap'},
    ninja: {main: '#3a3a5c', head: '#fff1e2', acc: 'ninja'},
    cat: {main: '#f4a259', head: '#fff6ea', acc: 'cat'},
    astro: {main: '#e8ecf5', head: '#fff2e4', acc: 'astro'},
    wizard: {main: '#7b5cff', head: '#fff2e8', acc: 'wizard'},
    dino: {main: '#5cc96b', head: '#fff3e0', acc: 'dino'},
    crown: {main: '#c0392b', head: '#fff2e2', acc: 'crown'},
    robot: {main: '#8a9bb3', head: '#e6eef8', acc: 'robot'},
    rival: {main: '#ff5d5d', head: '#fff0e6', acc: 'band'},
  };
  Art.avatar = id => Art.AVATARS[id] || Art.AVATARS.mint;

  function shade(hex, k) {   // k < 0 变暗，k > 0 变亮
    const n = parseInt(hex.slice(1), 16);
    let r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    const f = v => Math.round(k < 0 ? v * (1 + k) : v + (255 - v) * k);
    r = f(r); g = f(g); b = f(b);
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  }
  Art.shade = shade;

  function limb(ctx, ax, ay, bx, by, w, color) {
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.strokeStyle = INK; ctx.lineWidth = w + 5;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
    ctx.strokeStyle = color; ctx.lineWidth = w;
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  }
  function limb2(ctx, ax, ay, mx, my, bx, by, w, color) {   // 两节（带关节）的胳膊腿
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    for (const [c, lw] of [[INK, w + 5], [color, w]]) {
      ctx.strokeStyle = c; ctx.lineWidth = lw;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(mx, my); ctx.lineTo(bx, by); ctx.stroke();
    }
  }
  function blob(ctx, x, y, r, fill) {
    ctx.fillStyle = fill; ctx.strokeStyle = INK; ctx.lineWidth = 3.5;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }

  // 每个角色眨眼的时机错开
  const blinkAt = (t, seed) => { const p = (t + seed * 1.7) % 3.6; return p > 3.45; };

  function face(ctx, hx, hy, s, mood, t, seed, a) {
    const ex = 9 * s, ey = hy + 1;
    ctx.fillStyle = INK; ctx.strokeStyle = INK; ctx.lineCap = 'round';
    const eye = (x) => {
      if (mood === 'happy' || mood === 'win') {   // ^ ^
        ctx.lineWidth = 3.2; ctx.beginPath(); ctx.arc(x, ey + 2, 4.5, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
      } else if (mood === 'hurt') {               // > <
        ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x - 4, ey - 4); ctx.lineTo(x + 4, ey); ctx.lineTo(x - 4, ey + 4); ctx.stroke();
      } else if (blinkAt(t, seed) || mood === 'sleep') {
        ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x - 4, ey); ctx.lineTo(x + 4, ey); ctx.stroke();
      } else {
        ctx.beginPath(); ctx.ellipse(x, ey, 3.8, 5, 0, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x + 1.3, ey - 1.8, 1.5, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = INK;
      }
    };
    eye(hx - ex); eye(hx + ex);
    if (mood === 'fight') {   // 眉毛
      ctx.lineWidth = 2.6;
      ctx.beginPath(); ctx.moveTo(hx - ex - 5, ey - 9); ctx.lineTo(hx - ex + 4, ey - 6); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(hx + ex + 5, ey - 9); ctx.lineTo(hx + ex - 4, ey - 6); ctx.stroke();
    }
    // 腮红
    ctx.fillStyle = 'rgba(255,120,140,.45)';
    ctx.beginPath(); ctx.ellipse(hx - ex - 4, ey + 8, 4.5, 3, 0, 0, Math.PI * 2); ctx.ellipse(hx + ex + 4, ey + 8, 4.5, 3, 0, 0, Math.PI * 2); ctx.fill();
    // 嘴
    ctx.strokeStyle = INK; ctx.lineWidth = 2.6; ctx.beginPath();
    if (mood === 'sad' || mood === 'lose') ctx.arc(hx, ey + 15, 4.5, Math.PI * 1.15, Math.PI * 1.85);
    else if (mood === 'hurt' || mood === 'cold') { ctx.ellipse(hx, ey + 11, 3, 3.5, 0, 0, Math.PI * 2); }
    else if (mood === 'win' || mood === 'happy') { ctx.fillStyle = '#ff6b81'; ctx.arc(hx, ey + 8, 5, 0, Math.PI); ctx.closePath(); ctx.fill(); ctx.stroke(); return; }
    else if (mood === 'fight') { ctx.moveTo(hx - 4, ey + 11); ctx.lineTo(hx + 4, ey + 11); }
    else ctx.arc(hx, ey + 7, 4, Math.PI * 0.15, Math.PI * 0.85);
    ctx.stroke();
  }

  function accessory(ctx, av, hx, hy, r, facing, t) {
    const a = av.acc, m = av.main;
    ctx.lineJoin = 'round';
    if (a === 'sprout') {   // 头顶一棵小芽
      ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(hx, hy - r); ctx.lineTo(hx, hy - r - 10); ctx.stroke();
      const sw = Math.sin(t * 3) * 0.15;
      for (const d of [-1, 1]) {
        ctx.save(); ctx.translate(hx, hy - r - 9); ctx.rotate(d * (0.6 + sw));
        ctx.fillStyle = '#7ed957'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.ellipse(d * 7, 0, 8, 4.5, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); ctx.restore();
      }
    } else if (a === 'spikes') {   // 一撮翘起来的头发
      ctx.fillStyle = av.hair; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      ctx.save(); ctx.beginPath(); ctx.arc(hx, hy, r, Math.PI * 1.05, Math.PI * 1.95); ctx.quadraticCurveTo(hx, hy - r * 0.45, hx - r * 0.98, hy - r * 0.3); ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
      for (const [dx, h, lean] of [[-9, 14, -0.5], [1, 18, 0], [10, 13, 0.5]]) {
        ctx.beginPath(); ctx.moveTo(hx + dx - 6, hy - r + 4); ctx.quadraticCurveTo(hx + dx + lean * 10, hy - r - h, hx + dx + lean * 12 + 2, hy - r - h + 2);
        ctx.quadraticCurveTo(hx + dx + 2, hy - r - 2, hx + dx + 6, hy - r + 4); ctx.closePath(); ctx.fill(); ctx.stroke();
      }
    } else if (a === 'bow') {
      const bx = hx + facing * r * 0.55, by = hy - r * 0.85;
      ctx.fillStyle = '#ff3d7f'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
      for (const d of [-1, 1]) { ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(bx + d * 12, by - 8); ctx.lineTo(bx + d * 12, by + 8); ctx.closePath(); ctx.fill(); ctx.stroke(); }
      ctx.beginPath(); ctx.arc(bx, by, 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    } else if (a === 'band' || a === 'ninja') {
      ctx.fillStyle = a === 'ninja' ? '#2b2340' : m; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
      ctx.save(); ctx.beginPath(); ctx.arc(hx, hy, r, 0, Math.PI * 2); ctx.clip();
      ctx.fillRect(hx - r - 2, hy - r * 0.62, r * 2 + 4, 8);
      if (a === 'ninja') { ctx.fillRect(hx - r - 2, hy + 6, r * 2 + 4, r); }
      ctx.restore();
      const tx = hx - facing * r, ty = hy - r * 0.55 + 4, w = Math.sin(t * 8) * 4;
      ctx.fillStyle = a === 'ninja' ? '#ff4757' : m;
      ctx.beginPath(); ctx.moveTo(tx, ty - 3); ctx.lineTo(tx - facing * 16, ty - 8 + w); ctx.lineTo(tx - facing * 14, ty + 2 + w); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(tx - facing * 18, ty + 6 - w); ctx.lineTo(tx - facing * 12, ty + 10 - w); ctx.closePath(); ctx.fill(); ctx.stroke();
    } else if (a === 'cap') {
      ctx.fillStyle = m; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(hx, hy - 2, r + 1, Math.PI * 1.02, Math.PI * 1.98); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.ellipse(hx + facing * r * 0.8, hy - 4, r * 0.65, 5, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(hx, hy - r - 1, 3, 0, Math.PI * 2); ctx.fill();
    } else if (a === 'cat') {
      ctx.fillStyle = m; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      for (const d of [-1, 1]) {
        ctx.beginPath(); ctx.moveTo(hx + d * r * 0.25, hy - r * 0.92); ctx.lineTo(hx + d * r * 0.95, hy - r * 1.35); ctx.lineTo(hx + d * r * 0.9, hy - r * 0.45); ctx.closePath(); ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#ffb3c6'; ctx.beginPath(); ctx.moveTo(hx + d * r * 0.45, hy - r * 0.92); ctx.lineTo(hx + d * r * 0.85, hy - r * 1.18); ctx.lineTo(hx + d * r * 0.82, hy - r * 0.68); ctx.fill(); ctx.fillStyle = m;
      }
      ctx.lineWidth = 1.6; for (const d of [-1, 1]) for (const k of [-1, 1]) { ctx.beginPath(); ctx.moveTo(hx + d * r * 0.55, hy + 9); ctx.lineTo(hx + d * r * 1.15, hy + 9 + k * 4); ctx.stroke(); }
    } else if (a === 'wizard') {
      ctx.fillStyle = m; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(hx - r * 1.15, hy - r * 0.55); ctx.lineTo(hx - facing * 6, hy - r * 2.35); ctx.lineTo(hx + r * 1.15, hy - r * 0.55); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.ellipse(hx, hy - r * 0.55, r * 1.3, 6, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#ffd23f'; window.Play && Play.starPath(ctx, hx + 2, hy - r * 1.3, 7, t); ctx.fill(); ctx.stroke();
    } else if (a === 'crown') {
      ctx.fillStyle = '#ffd23f'; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      const y0 = hy - r * 0.78;
      ctx.beginPath(); ctx.moveTo(hx - r * 0.7, y0); ctx.lineTo(hx - r * 0.75, y0 - 18); ctx.lineTo(hx - r * 0.35, y0 - 8); ctx.lineTo(hx, y0 - 22);
      ctx.lineTo(hx + r * 0.35, y0 - 8); ctx.lineTo(hx + r * 0.75, y0 - 18); ctx.lineTo(hx + r * 0.7, y0); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#ff4d6d'; ctx.beginPath(); ctx.arc(hx, y0 - 6, 3, 0, Math.PI * 2); ctx.fill();
    } else if (a === 'dino') {
      ctx.fillStyle = m; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      ctx.save(); ctx.beginPath(); ctx.arc(hx, hy, r + 4, Math.PI * 0.95, Math.PI * 2.05); ctx.lineTo(hx + r + 4, hy - 2); ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
      ctx.fillStyle = '#ffd23f';
      for (let i = 0; i < 3; i++) { const ang = Math.PI * (1.3 + i * 0.2), px = hx + Math.cos(ang) * (r + 4), py = hy + Math.sin(ang) * (r + 4); ctx.beginPath(); ctx.moveTo(px - 5, py + 2); ctx.lineTo(px + Math.cos(ang) * 10, py + Math.sin(ang) * 10); ctx.lineTo(px + 5, py + 2); ctx.closePath(); ctx.fill(); ctx.stroke(); }
    } else if (a === 'robot') {
      ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(hx, hy - r); ctx.lineTo(hx, hy - r - 12); ctx.stroke();
      ctx.fillStyle = Math.sin(t * 6) > 0 ? '#ff4d6d' : '#ffd23f'; ctx.beginPath(); ctx.arc(hx, hy - r - 14, 4.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#9fb3cc'; for (const d of [-1, 1]) { ctx.beginPath(); ctx.arc(hx + d * r, hy, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }
    }
  }

  function astroHelmet(ctx, hx, hy, r) {
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.fillStyle = 'rgba(180,225,255,.28)';
    ctx.beginPath(); ctx.arc(hx, hy, r + 7, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(hx, hy, r + 2, Math.PI * 1.15, Math.PI * 1.45); ctx.stroke();
  }

  function weaponAt(ctx, w, x, y, facing, swing) {
    if (!w || w === 'fist') return;
    ctx.save(); ctx.translate(x, y); ctx.scale(facing, 1); ctx.rotate(swing ? 0.15 : -0.9);
    ctx.lineCap = 'round'; ctx.strokeStyle = INK; ctx.lineJoin = 'round';
    if (w === 'stick') {
      ctx.lineWidth = 11; ctx.beginPath(); ctx.moveTo(-6, 0); ctx.lineTo(46, 0); ctx.stroke();
      ctx.strokeStyle = '#b5793f'; ctx.lineWidth = 6.5; ctx.beginPath(); ctx.moveTo(-6, 0); ctx.lineTo(46, 0); ctx.stroke();
    } else if (w === 'sword') {
      ctx.fillStyle = '#e9f1fb'; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(8, -5); ctx.lineTo(60, -4); ctx.lineTo(70, 0); ctx.lineTo(60, 4); ctx.lineTo(8, 5); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#ffd23f'; Play.roundRect(ctx, 2, -12, 7, 24, 3); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#8b5cf6'; Play.roundRect(ctx, -12, -4, 15, 8, 3); ctx.fill(); ctx.stroke();
    } else if (w === 'laser') {
      ctx.rotate(swing ? -0.15 : 0.7); ctx.fillStyle = '#4d96ff'; ctx.lineWidth = 3;
      Play.roundRect(ctx, -4, -9, 34, 15, 6); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#9be7ff'; Play.roundRect(ctx, 28, -5, 10, 8, 3); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#ffd23f'; Play.roundRect(ctx, 0, 4, 8, 12, 3); ctx.fill(); ctx.stroke();
    }
    ctx.restore();
  }

  /* 角色：返回手的位置（游戏用来判断武器挥到哪） */
  Art.hero = function (ctx, x, y, o) {
    o = Object.assign({avatar: 'mint', pose: 'idle', t: 0, facing: 1, scale: 1, weapon: 'fist', shield: 0, alpha: 1, seed: 0}, o || {});
    const av = typeof o.avatar === 'string' ? Art.avatar(o.avatar) : o.avatar;
    const s = o.scale, f = o.facing, t = o.t, pose = o.pose;
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.globalAlpha *= o.alpha;
    // 影子
    ctx.fillStyle = 'rgba(30,20,60,.18)'; ctx.beginPath(); ctx.ellipse(0, 2, 30, 7, 0, 0, Math.PI * 2); ctx.fill();
    const lift = o.lift || 0;
    ctx.translate(0, -lift);
    let bob = Math.sin(t * 3) * 1.5, lean = 0, hipY = -40, legA = 9, legB = -9, kneeA = 0, kneeB = 0;
    let hand1 = [f * 20, -50], hand2 = [-f * 18, -48], elbow1 = null, elbow2 = null, mood = 'normal', headTilt = 0;
    if (pose === 'walk' || pose === 'run') {
      const sp = pose === 'run' ? 16 : 11, amp = pose === 'run' ? 24 : 15, ph = t * sp;
      legA = Math.sin(ph) * amp; legB = -legA; kneeA = Math.max(0, Math.cos(ph)) * 10; kneeB = Math.max(0, -Math.cos(ph)) * 10;
      bob = Math.abs(Math.sin(ph)) * -4; lean = pose === 'run' ? f * 0.12 : f * 0.04;
      hand1 = [f * 4 - Math.sin(ph) * 20 * f, -50 + Math.abs(Math.cos(ph)) * 4]; hand2 = [f * 4 + Math.sin(ph) * 20 * f, -50];
      if (pose === 'run') mood = 'fight';
    } else if (pose === 'jump') {
      legA = 14; legB = -6; kneeA = 14; kneeB = 16; bob = 0; hand1 = [f * 24, -92]; hand2 = [-f * 22, -88]; mood = 'happy';
    } else if (pose === 'punch') {
      lean = f * 0.12; legA = 16; legB = -14; hand1 = [f * 54, -68]; hand2 = [f * 12, -66]; elbow2 = [f * 2, -54]; mood = 'fight';
    } else if (pose === 'hurt') {
      lean = -f * 0.22; legA = -10; legB = 10; hand1 = [-f * 26, -88]; hand2 = [f * 18, -90]; mood = 'hurt'; headTilt = -f * 0.25;
    } else if (pose === 'charge') {
      hand1 = [f * 22, -60]; hand2 = [f * 8, -62]; elbow1 = [f * 10, -48]; elbow2 = [-f * 4, -50]; mood = 'normal'; bob = Math.sin(t * 6) * 2;
    } else if (pose === 'win') {
      const j = Math.abs(Math.sin(t * 6)) * 10; ctx.translate(0, -j);
      hand1 = [f * 26, -104]; hand2 = [-f * 26, -104]; legA = 8; legB = -8; mood = 'win';
    } else if (pose === 'lose') {
      lean = f * 0.1; hand1 = [f * 16, -36]; hand2 = [-f * 14, -36]; mood = 'sad'; headTilt = f * 0.2; bob = 3;
    } else if (pose === 'freeze') {
      hand1 = [f * 18, -60]; hand2 = [-f * 18, -60]; mood = 'cold'; bob = 0;
    } else {
      hand1 = [f * 24, -40 + Math.sin(t * 3) * 2]; hand2 = [-f * 22, -40 - Math.sin(t * 3) * 2];
    }
    if (o.mood) mood = o.mood;
    ctx.rotate(lean);
    const neckY = -70 + bob, hip = hipY + bob * 0.5;
    // 后面的手臂
    limb2(ctx, 0, neckY + 6, elbow2 ? elbow2[0] : (hand2[0]) * 0.55, elbow2 ? elbow2[1] : (neckY + 6 + hand2[1]) / 2 + 4, hand2[0], hand2[1] + bob, 8, shade(av.main, -0.12));
    // 腿
    const leg = (dx, knee, c) => limb2(ctx, 0, hip, dx * 0.5 + f * knee * 0.6, hip + 20 - knee, dx, 0, 9, c);
    leg(legB, kneeB, shade(av.main, -0.18));
    leg(legA, kneeA, shade(av.main, -0.05));
    // 身体（小衣服）
    limb(ctx, 0, neckY + 4, 0, hip - 2, 18, av.main);
    ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.beginPath(); ctx.arc(-3 * f, neckY + 14, 3, 0, Math.PI * 2); ctx.fill();
    // 头
    const hx = f * 2, hy = neckY - 24, r = 26;
    ctx.save(); ctx.translate(hx, hy); ctx.rotate(headTilt); ctx.translate(-hx, -hy);
    if (av.acc === 'dino' || av.acc === 'wizard' || av.acc === 'crown') { /* 帽子在后面画 */ }
    blob(ctx, hx, hy, r, av.head);
    if (av.acc === 'robot') { ctx.fillStyle = 'rgba(77,150,255,.18)'; ctx.fillRect(hx - r + 4, hy - 8, r * 2 - 8, 18); }
    face(ctx, hx + f * 4, hy + 2, 1, mood, t, o.seed);
    accessory(ctx, av, hx, hy, r, f, t);
    if (av.acc === 'astro') astroHelmet(ctx, hx, hy, r);
    ctx.restore();
    // 前面的手臂 + 武器
    const hy1 = hand1[1] + bob;
    limb2(ctx, 0, neckY + 6, elbow1 ? elbow1[0] : hand1[0] * 0.55, elbow1 ? elbow1[1] : (neckY + 6 + hy1) / 2 + 4, hand1[0], hy1, 8, av.main);
    blob(ctx, hand1[0], hy1, 5.5, av.head);
    if (pose === 'charge') {   // 捧着一本小书在答题
      ctx.save(); ctx.translate(f * 18, -64 + bob); ctx.rotate(f * -0.15);
      ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
      Play.roundRect(ctx, -14, -10, 28, 20, 3); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, -10); ctx.lineTo(0, 10); ctx.stroke();
      ctx.strokeStyle = '#4d96ff'; ctx.lineWidth = 1.5; for (const yy of [-4, 1]) { ctx.beginPath(); ctx.moveTo(-10, yy); ctx.lineTo(-3, yy); ctx.moveTo(3, yy); ctx.lineTo(10, yy); ctx.stroke(); }
      ctx.restore();
    } else weaponAt(ctx, o.weapon, hand1[0], hy1, f, pose === 'punch');
    ctx.restore();
    // 护盾、冷冻、答题光环（不跟着身体倾斜）
    ctx.save(); ctx.translate(x, y - lift * s); ctx.scale(s, s);
    if (o.shield > 0) {
      ctx.strokeStyle = 'rgba(77,150,255,.85)'; ctx.lineWidth = 4; ctx.fillStyle = 'rgba(120,190,255,.16)';
      ctx.beginPath(); ctx.ellipse(0, -60, 50, 70, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.strokeStyle = 'rgba(255,255,255,.7)'; ctx.lineWidth = 3; ctx.beginPath(); ctx.ellipse(0, -60, 44, 64, 0, Math.PI * 1.2, Math.PI * 1.45); ctx.stroke();
    }
    if (pose === 'charge') {
      ctx.globalAlpha = 0.5 + 0.3 * Math.sin(t * 8);
      ctx.strokeStyle = '#ffd23f'; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.ellipse(0, -2, 34 + Math.sin(t * 8) * 4, 9, 0, 0, Math.PI * 2); ctx.stroke();
      for (let i = 0; i < 3; i++) { const a = t * 3 + i * 2.1; ctx.fillStyle = '#ffd23f'; Play.starPath(ctx, Math.cos(a) * 38, -60 + Math.sin(a) * 50, 5, a); ctx.fill(); }
      ctx.globalAlpha = 1;
    }
    if (pose === 'freeze') {
      ctx.fillStyle = 'rgba(160,220,255,.45)'; ctx.strokeStyle = 'rgba(255,255,255,.9)'; ctx.lineWidth = 3;
      Play.roundRect(ctx, -36, -128, 72, 130, 14); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-24, -110); ctx.lineTo(-12, -96); ctx.moveTo(18, -40); ctx.lineTo(26, -28); ctx.stroke();
    }
    ctx.restore();
    return {hand: [x + hand1[0] * s, y + (hy1 - lift) * s], head: [x + hx * s, y + (hy - lift) * s]};
  };

  /* 怪兽：slime 史莱姆 / bat 蝙蝠 / ghost 小幽灵 / boss 大怪兽（小龙） */
  Art.monster = function (ctx, x, y, o) {
    o = Object.assign({kind: 'slime', t: 0, scale: 1, hurt: 0, facing: -1, color: null}, o || {});
    const s = o.scale, t = o.t;
    ctx.save(); ctx.translate(x, y); ctx.scale(s * (o.facing < 0 ? 1 : -1), s);
    if (o.hurt > 0) ctx.globalAlpha *= 0.55 + 0.45 * Math.sin(t * 40);
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    const eyes = (ex, ey, sp, r, mad) => {
      for (const d of [-1, 1]) {
        ctx.fillStyle = '#fff'; ctx.strokeStyle = INK; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.arc(ex + d * sp, ey, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(ex + d * sp - r * 0.3, ey + 1, r * 0.5, 0, Math.PI * 2); ctx.fill();
        if (mad) { ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(ex + d * sp - d * r, ey - r - 3); ctx.lineTo(ex + d * sp + d * r * 0.6, ey - r + 1); ctx.stroke(); }
      }
    };
    ctx.fillStyle = 'rgba(30,20,60,.18)'; ctx.beginPath(); ctx.ellipse(0, 2, o.kind === 'boss' ? 70 : 26, o.kind === 'boss' ? 12 : 6, 0, 0, Math.PI * 2); ctx.fill();
    if (o.kind === 'slime') {
      const sq = Math.sin(t * 7) * 0.12, c = o.color || '#7ed957';
      ctx.fillStyle = c; ctx.strokeStyle = INK; ctx.lineWidth = 3.5;
      ctx.beginPath(); ctx.moveTo(-26 * (1 + sq), 0);
      ctx.bezierCurveTo(-28 * (1 + sq), -44 * (1 - sq), 28 * (1 + sq), -44 * (1 - sq), 26 * (1 + sq), 0); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.5)'; ctx.beginPath(); ctx.ellipse(-10, -26 * (1 - sq), 5, 8, -0.5, 0, Math.PI * 2); ctx.fill();
      eyes(-4, -18 * (1 - sq), 9, 6, false);
      ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(-4, -9, 4, 0.2, Math.PI - 0.2); ctx.stroke();
    } else if (o.kind === 'bat') {
      const fl = Math.sin(t * 14), c = o.color || '#9b5de5', yy = -40 + Math.sin(t * 4) * 6;
      ctx.fillStyle = c; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      for (const d of [-1, 1]) {
        ctx.beginPath(); ctx.moveTo(d * 10, yy); ctx.quadraticCurveTo(d * 30, yy - 20 * fl - 10, d * 44, yy - 6 * fl);
        ctx.quadraticCurveTo(d * 34, yy + 4, d * 26, yy + 2); ctx.quadraticCurveTo(d * 20, yy + 10, d * 10, yy + 6); ctx.closePath(); ctx.fill(); ctx.stroke();
      }
      ctx.beginPath(); ctx.arc(0, yy, 16, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      for (const d of [-1, 1]) { ctx.beginPath(); ctx.moveTo(d * 6, yy - 13); ctx.lineTo(d * 12, yy - 24); ctx.lineTo(d * 14, yy - 10); ctx.fill(); ctx.stroke(); }
      eyes(-2, yy - 2, 6, 4.5, true);
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.moveTo(-6, yy + 7); ctx.lineTo(-4, yy + 12); ctx.lineTo(-2, yy + 7); ctx.fill();
    } else if (o.kind === 'ghost') {
      const yy = -36 + Math.sin(t * 3) * 6, c = o.color || '#e8f1ff';
      ctx.fillStyle = c; ctx.strokeStyle = INK; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(-22, yy + 22); ctx.lineTo(-22, yy - 6); ctx.arc(0, yy - 6, 22, Math.PI, 0);
      ctx.lineTo(22, yy + 22);
      for (let i = 0; i < 4; i++) ctx.quadraticCurveTo(16 - i * 11, yy + 30 + Math.sin(t * 8 + i) * 3, 11 - i * 11, yy + 22);
      ctx.closePath(); ctx.fill(); ctx.stroke();
      eyes(-3, yy - 6, 8, 5, false);
      ctx.fillStyle = INK; ctx.beginPath(); ctx.ellipse(-3, yy + 6, 4, 5, 0, 0, Math.PI * 2); ctx.fill();
    } else if (o.kind === 'boss') {   // 胖胖的小龙
      const c = o.color || '#ff7b54', b = Math.sin(t * 2.5) * 3, wing = Math.sin(t * 5) * 0.25;
      ctx.fillStyle = shade(c, -0.15); ctx.strokeStyle = INK; ctx.lineWidth = 4;
      for (const d of [1]) {   // 翅膀
        ctx.save(); ctx.translate(18 * d, -96 + b); ctx.rotate(-0.4 + wing);
        ctx.beginPath(); ctx.moveTo(0, 0); ctx.quadraticCurveTo(40, -60, 90, -40); ctx.quadraticCurveTo(70, -20, 76, 0); ctx.quadraticCurveTo(50, -6, 40, 10); ctx.quadraticCurveTo(20, 2, 0, 14); ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
      }
      ctx.fillStyle = c;   // 尾巴
      ctx.beginPath(); ctx.moveTo(40, -30); ctx.quadraticCurveTo(100, -20, 110, -60); ctx.lineTo(96, -50); ctx.quadraticCurveTo(84, -16, 36, -14); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.ellipse(10, -60 + b, 56, 58, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();   // 身体
      ctx.fillStyle = '#ffe3a3'; ctx.beginPath(); ctx.ellipse(-6, -46 + b, 32, 38, 0, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = 'rgba(43,35,64,.25)'; ctx.lineWidth = 2; for (let i = 0; i < 4; i++) { ctx.beginPath(); ctx.moveTo(-30, -66 + i * 14 + b); ctx.quadraticCurveTo(-6, -60 + i * 14 + b, 18, -66 + i * 14 + b); ctx.stroke(); }
      ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.fillStyle = c;
      ctx.beginPath(); ctx.ellipse(-22, -130 + b, 42, 36, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();   // 头
      ctx.fillStyle = '#fff3c4';
      for (const hx of [-44, -4]) { ctx.beginPath(); ctx.moveTo(hx - 6, -158 + b); ctx.lineTo(hx + 2, -184 + b); ctx.lineTo(hx + 8, -156 + b); ctx.closePath(); ctx.fill(); ctx.stroke(); }
      ctx.fillStyle = shade(c, 0.25); ctx.beginPath(); ctx.ellipse(-54, -118 + b, 18, 14, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(-60, -120 + b, 2.5, 0, Math.PI * 2); ctx.arc(-50, -122 + b, 2.5, 0, Math.PI * 2); ctx.fill();
      eyes(-24, -138 + b, 13, 9, true);
      ctx.fillStyle = '#fff'; for (const tx of [-44, -34]) { ctx.beginPath(); ctx.moveTo(tx, -106 + b); ctx.lineTo(tx + 4, -98 + b); ctx.lineTo(tx + 8, -106 + b); ctx.fill(); }
      ctx.fillStyle = shade(c, -0.15); for (const fx of [-24, 30]) { ctx.beginPath(); ctx.ellipse(fx, -4, 18, 10, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }
    }
    ctx.restore();
  };

  /* 星星魔法弹 / 能量球等小道具 */
  Art.orb = function (ctx, x, y, r, color, t) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, r * 2);
    g.addColorStop(0, '#fff'); g.addColorStop(0.35, color); g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 2, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#fff'; Play.starPath(ctx, x, y, r * 0.9, t * 6); ctx.fill();
  };

  Art.castle = function (ctx, x, y, s, hpPct, t) {
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
    ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.lineJoin = 'round';
    const wall = '#f3e9ff', roof = '#ff6fa3', dark = '#d9c7f5';
    const tower = (tx, w, h) => {
      ctx.fillStyle = wall; ctx.fillRect(tx - w / 2, -h, w, h); ctx.strokeRect(tx - w / 2, -h, w, h);
      ctx.fillStyle = roof; ctx.beginPath(); ctx.moveTo(tx - w / 2 - 8, -h); ctx.lineTo(tx, -h - w * 0.9); ctx.lineTo(tx + w / 2 + 8, -h); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#7bdff2'; Play.roundRect(ctx, tx - 8, -h + 20, 16, 22, 8); ctx.fill(); ctx.stroke();
      ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(tx, -h - w * 0.9); ctx.lineTo(tx, -h - w * 0.9 - 22); ctx.stroke();
      ctx.fillStyle = '#ffd23f'; ctx.beginPath(); ctx.moveTo(tx, -h - w * 0.9 - 22); ctx.lineTo(tx + 18 + Math.sin(t * 5) * 3, -h - w * 0.9 - 16); ctx.lineTo(tx, -h - w * 0.9 - 10); ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.lineWidth = 4;
    };
    ctx.fillStyle = dark; ctx.fillRect(-70, -110, 140, 110); ctx.strokeRect(-70, -110, 140, 110);
    for (let i = 0; i < 5; i++) { ctx.fillStyle = dark; ctx.fillRect(-70 + i * 30, -126, 20, 16); ctx.strokeRect(-70 + i * 30, -126, 20, 16); }
    ctx.fillStyle = '#b5793f'; ctx.beginPath(); ctx.moveTo(-20, 0); ctx.lineTo(-20, -40); ctx.arc(0, -40, 20, Math.PI, 0); ctx.lineTo(20, 0); ctx.closePath(); ctx.fill(); ctx.stroke();
    tower(-70, 46, 160); tower(70, 46, 160);
    if (hpPct < 0.5) { ctx.strokeStyle = INK; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(-40, -90); ctx.lineTo(-30, -74); ctx.lineTo(-38, -60); ctx.moveTo(30, -50); ctx.lineTo(40, -36); ctx.stroke(); }
    ctx.restore();
  };

  Art.portrait = function (canvas, avatar, pose, opts) {
    const dpr = Math.min(2, window.devicePixelRatio || 1), r = canvas.getBoundingClientRect();
    const w = r.width || canvas.width, h = r.height || canvas.height;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const s = Math.min(w / 120, h / 170) * ((opts && opts.zoom) || 1);
    Art.hero(ctx, w / 2, h - 8 * s, Object.assign({avatar, pose: pose || 'idle', t: performance.now() / 1000, scale: s, seed: avatar.length}, opts || {}));
  };
})();

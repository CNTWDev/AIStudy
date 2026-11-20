/* 口语「开口练」的前端（app/speaking.py，DESIGN 5.67）：录音、四步主线、复习、和 AI 聊。
   录音直接录成 16kHz 单声道 16 位 WAV：各家语音识别都收，服务器不用转码（app/stt）。 */

/* ---------------------------------------------------------------- 录音 */
const Rec = {
  ctx: null, stream: null, node: null, src: null, bufs: [], rate: 48000, t0: 0, level: 0, peak: 0, heardAt: 0,
  SILENT: 0.01,   // 最响也不到 1%：当成没录到声音（麦克风没权限、选错了设备、静音了）
  ok() { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && (window.AudioContext || window.webkitAudioContext)); },
  get device() { try { return localStorage.getItem('sk-mic') || ''; } catch (e) { return ''; } },
  set device(id) { try { localStorage.setItem('sk-mic', id || ''); } catch (e) {} },
  async devices() {
    try { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput' && d.deviceId); } catch (e) { return []; }
  },
  async start() {
    Speak.stop();
    const base = {channelCount: 1, echoCancellation: true, noiseSuppression: true};
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({audio: this.device ? {...base, deviceId: {exact: this.device}} : base});
    } catch (e) {   // 选过的麦克风拔掉了：换回系统默认
      if (!this.device || (e.name !== 'OverconstrainedError' && e.name !== 'NotFoundError')) throw e;
      this.device = '';
      this.stream = await navigator.mediaDevices.getUserMedia({audio: base});
    }
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = this.ctx || new AC();
    if (this.ctx.state === 'suspended') await this.ctx.resume();
    this.rate = this.ctx.sampleRate; this.bufs = []; this.level = 0; this.peak = 0; this.heardAt = 0;
    this.src = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    this.node.onaudioprocess = e => {
      const d = e.inputBuffer.getChannelData(0); this.bufs.push(new Float32Array(d));
      let m = 0; for (let i = 0; i < d.length; i += 16) m = Math.max(m, Math.abs(d[i]));
      this.level = m; this.peak = Math.max(this.peak, m); if (m > this.SILENT && !this.heardAt) this.heardAt = performance.now();
    };
    this.src.connect(this.node); this.node.connect(this.ctx.destination);
    this.t0 = performance.now();
  },
  stop() {
    const secs = (performance.now() - this.t0) / 1000;
    try { this.node && this.node.disconnect(); this.src && this.src.disconnect(); } catch (e) {}
    if (this.stream) this.stream.getTracks().forEach(t => t.stop());
    this.node = this.src = this.stream = null;
    return {blob: this.wav(this.down(this.bufs, this.rate, 16000), 16000), secs};
  },
  down(bufs, from, to) {
    const n = bufs.reduce((a, b) => a + b.length, 0), all = new Float32Array(n);
    let o = 0; bufs.forEach(b => { all.set(b, o); o += b.length; });
    if (from === to) return all;
    const r = from / to, out = new Float32Array(Math.floor(n / r));
    for (let i = 0; i < out.length; i++) {   // 简单平均降采样（说话够清楚）
      const a = Math.floor(i * r), b = Math.min(n, Math.floor((i + 1) * r)); let s = 0;
      for (let j = a; j < b; j++) s += all[j]; out[i] = s / Math.max(1, b - a);
    }
    return out;
  },
  wav(pcm, rate) {
    const buf = new ArrayBuffer(44 + pcm.length * 2), v = new DataView(buf);
    const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, pcm.length * 2, true);
    for (let i = 0, o = 44; i < pcm.length; i++, o += 2) { const s = Math.max(-1, Math.min(1, pcm[i])); v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
    return new Blob([buf], {type: 'audio/wav'});
  },
};

/* 没录到声音时的说明 + 选麦克风（电脑上常见：浏览器没拿到系统的麦克风权限，或用了另一个输入设备） */
const MIC_HELP = '没收到声音。Mac：系统设置 → 隐私与安全性 → 麦克风，打开浏览器；也看看下面选的是不是你在用的麦克风。';
async function micPicker(box) {
  if (!box) return;
  const ds = await Rec.devices();
  if (ds.length < 2) { box.innerHTML = ''; return; }
  const cur = Rec.device;
  box.innerHTML = `<label class="sk-micsel">麦克风 <select>${ds.map((d, i) => `<option value="${esc(d.deviceId)}"${d.deviceId === cur || (!cur && d.deviceId === 'default') ? ' selected' : ''}>${esc(d.label || '麦克风 ' + (i + 1))}</option>`).join('')}</select></label>`;
  box.querySelector('select').onchange = e => { Rec.device = e.target.value; toast('换好了，再说一次'); };
}

/* 话筒按钮：按住说、松开结束；轻点一下 = 开始，再点一下 = 结束（说长一点不用一直按着）。
   onClip(blob, secs) 返回 Promise；期间按钮转圈。 */
function micButton(el, o) {
  const max = o.max || 90;
  let state = 'idle', downAt = 0, timer = null, wave = null;
  const hint = o.hint ? $(o.hint) : null, idleHint = hint ? hint.innerHTML : '';
  const picker = document.createElement('div'); (hint || el).after(picker);
  micPicker(picker);   // 有好几个麦克风（并且浏览器已经给过权限）才显示
  const set = s => { state = s; el.classList.toggle('rec', s === 'rec'); el.classList.toggle('busy', s === 'busy'); el.disabled = s === 'busy' || s === 'off'; };
  const tick = () => {
    const s = (performance.now() - Rec.t0) / 1000;
    if (hint) hint.innerHTML = `<b class="sk-time">${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}</b> · ${o.tapMode ? '再点一下结束' : '松开结束'}<br>${esc(o.recHint || '')}`;
    if (wave) { const bars = $$('i', wave), h = Math.min(1, Rec.level * 3); bars.forEach((b, i) => { b.style.height = (6 + h * 30 * (0.4 + 0.6 * Math.abs(Math.sin(s * 7 + i)))) + 'px'; }); }
    if (hint && s > 2.5 && !Rec.heardAt) hint.innerHTML += `<br><span class="sk-err">${MIC_HELP}</span>`;
    if (s >= max) finish();
  };
  const begin = async () => {
    if (state !== 'idle') return;
    if (!Rec.ok()) { toast('这个浏览器录不了音，换 Chrome 或 Safari 试试'); return; }
    set('rec');
    try { await Rec.start(); } catch (e) { set('idle'); toast('没打开话筒：请在浏览器里允许使用麦克风'); return; }
    if (o.wave) { wave = $(o.wave); wave.hidden = false; }
    timer = setInterval(tick, 100); tick();
  };
  const finish = async () => {
    if (state !== 'rec') return;
    clearInterval(timer); o.tapMode = false;
    if (wave) wave.hidden = true;
    const {blob, secs} = Rec.stop();
    if (secs < 0.6) { set('idle'); if (hint) hint.innerHTML = idleHint; return; }
    if (Rec.peak < Rec.SILENT) {   // 录到的是静音：不送去识别，直接说清楚，给出选麦克风
      set('idle'); if (hint) hint.innerHTML = `<span class="sk-err">${MIC_HELP}</span>`;
      micPicker(picker); return;
    }
    set('busy'); if (hint) hint.innerHTML = '正在听你说的…';
    let err = '';
    try { await o.onClip(blob, secs); } catch (e) { err = e.message || '没传上，再说一次'; toast(err); }
    if (state === 'busy') set('idle');
    // 出错的原话留在话筒下面，不会一闪就没（后台系统页「语音引擎」也记着）
    if (hint && state === 'idle') hint.innerHTML = err ? `<span class="sk-err">${esc(err)}</span><br>再按住说一次试试` : idleHint;
  };
  el.addEventListener('pointerdown', e => {
    e.preventDefault();
    if (state === 'rec') { finish(); return; }
    try { el.setPointerCapture(e.pointerId); } catch (x) {}   // 手指滑出按钮也照样收到松开
    downAt = performance.now(); begin();
  });
  el.addEventListener('pointerup', () => { if (state !== 'rec') return; if (performance.now() - downAt < 450) { o.tapMode = true; return; } if (!o.tapMode) finish(); });
  el.addEventListener('pointerleave', () => { if (state === 'rec' && !o.tapMode && performance.now() - downAt > 450) finish(); });
  el.addEventListener('pointercancel', () => { if (state === 'rec' && !o.tapMode) finish(); });   // 来电、切走：说到哪算到哪
  el.addEventListener('contextmenu', e => e.preventDefault());
  // 电脑上：按住空格说，松开结束（话筒在页面上、焦点不在输入框里时）
  const typing = () => { const a = document.activeElement; return a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)); };
  const kd = e => {
    if (!el.isConnected) { removeEventListener('keydown', kd); removeEventListener('keyup', ku); return; }
    if (e.code !== 'Space' || typing() || el.closest('[hidden]')) return;
    e.preventDefault(); if (!e.repeat && state === 'idle') { o.tapMode = false; begin(); }
  };
  const ku = e => { if (e.code === 'Space' && el.isConnected && state === 'rec' && !typing()) { e.preventDefault(); finish(); } };
  addEventListener('keydown', kd); addEventListener('keyup', ku);
  return {off() { set('off'); }, on() { set('idle'); if (hint) hint.innerHTML = idleHint; }};
}

async function sendClip(url, blob, fields) {
  const fd = new FormData(); fd.append('file', blob, 'speech.wav');
  Object.entries(fields || {}).forEach(([k, v]) => fd.append(k, v));
  const r = await fetch(url, {method: 'POST', body: fd});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || j.detail || '没传上，再说一次');
  if (j.over) toast(j.over);
  return j;
}

/* ---------------------------------------------------------------- 累计开口 / 听（DESIGN 5.67：目标是说满、听满成百上千小时，贵在坚持）
   听：页面上在放声音（地道版、跟读原句、回听录音）的每一秒都算，攒一会儿报给服务器。 */
const Heard = {
  secs: 0,
  flush(beacon) {
    const s = Math.round(this.secs * 10) / 10; if (s < 1) return; this.secs = 0;
    const body = JSON.stringify({secs: s});
    if (beacon && navigator.sendBeacon) { navigator.sendBeacon('/api/speak/listen', new Blob([body], {type: 'application/json'})); return; }
    fetch('/api/speak/listen', {method: 'POST', headers: {'Content-Type': 'application/json'}, body}).catch(() => {});
  },
};
setInterval(() => { if (Speak.playing && !document.hidden) Heard.secs += 0.5; }, 500);
setInterval(() => Heard.flush(), 20000);
addEventListener('pagehide', () => Heard.flush(true));

/* 时长按分钟说：不满 1 小时说「37 分钟」，之后「3 小时 5 分」 */
function dur(secs) {
  const m = Math.floor((secs || 0) / 60);
  if (!m) return secs > 0 ? '不到 1 分钟' : '0 分钟';
  if (m < 60) return `${m} 分钟`;
  const h = Math.floor(m / 60), r = m % 60;
  return `${h.toLocaleString()} 小时${r ? ` ${r} 分` : ''}`;
}
const milestone = m => m >= 60 ? `${(m / 60).toLocaleString()} 小时` : `${m} 分钟`;
/* 累计卡：开口、听各一行，今天加了多少，离下一个里程碑还差多少（一根细条）。
   刚练完的页面传 t0（进页面时的累计）：这次跨过了里程碑就先表扬。 */
const Totals = {
  t0: null,
  async load() { try { this.t0 = await api('/api/speak/totals'); } catch (e) {} },
  html(t) {
    const line = (k, x) => {
      const pct = Math.min(100, Math.round(x.secs / 60 / x.next * 100));
      return `<div class="sk-acc"><div class="row" style="justify-content:space-between;align-items:baseline"><span><span class="k">${k}</span> <b>${dur(x.secs)}</b></span>` +
        `<span class="tiny faint">${x.today >= x.secs - 30 ? '' : x.today >= 60 ? `今天 +${dur(x.today)}` : x.today > 0 ? '今天 +不到 1 分钟' : ''}</span></div>` +
        `<div class="bar"><i style="--w:${pct}%"></i></div><div class="tiny faint">下一站 ${milestone(x.next)}</div></div>`;
    };
    return line('累计开口', t.speak) + line('累计听', t.listen);
  },
  /* 开口练首页的大数字（展厅）：累计开口，不满 1 小时按分钟，之后「6h12」；下面一行离下一站还差多少、累计听 */
  hero(t) {
    const m = Math.floor(t.speak.secs / 60), h = Math.floor(m / 60), left = Math.max(0, t.speak.next - m);
    const big = h ? `<span data-from="0" data-to="${h}">${h}</span><i>h</i>${String(m % 60).padStart(2, '0')}` : `<span data-from="0" data-to="${m}">${m}</span><i>min</i>`;
    return `<div class="v">${big}</div><div class="small">${left ? `离 ${milestone(t.speak.next)}，还差 ${dur(left * 60)}` : ''}${t.speak.today >= 60 ? ` · 今天 +${dur(t.speak.today)}` : ''}<br>累计听 ${dur(t.listen.secs)}</div>`;
  },
  async show(box, after) {
    if (!box) return;
    Heard.flush();
    let t; try { t = await api('/api/speak/totals'); } catch (e) { return; }
    const up = [['开口', 'speak'], ['听', 'listen']].filter(([, k]) => this.t0 && t[k].reached > this.t0[k].reached);
    box.innerHTML = (up.length ? cheer(up.map(([n, k]) => `累计${n}满 ${milestone(t[k].reached)}了！`).join(' '),
      '一点一点攒起来的。口语就是这样练出来的：多说、多听、天天来。', {big: true}) : '') +
      `<div class="card sk-rise" style="--d:${after || 0}s"><div class="sk-lbl">一点一点攒起来</div>${this.html(t)}</div>`;
  },
};

/* ---------------------------------------------------------------- 显示 */
function chunkRe(en) {
  const ws = en.toLowerCase().replace(/…/g, '...').replace(/\s*\.\.\.\s*/g, ' ... ').trim().split(/\s+/);
  const parts = ws.map(w => w === '...' ? null : w.replace(/[,.?!:;]/g, ''))
    .map(w => w === null ? '__GAP__' : (w === 'sb' || w === 'sth') ? "[\\w'’]+(?:\\s+[\\w'’]+){0,2}?" : w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + "[\\w'’]*");
  let pat = '';
  parts.forEach((p, i) => { pat += p === '__GAP__' ? "(?:\\s+[\\w'’]+){0,4}?" : (i ? '\\s+' : '') + p; });
  return new RegExp("(^|[^\\w'])(" + pat + ")(?![\\w'])", 'i');
}
/* 一段英文里把表达块标出来（先转义再标） */
function markChunks(text, chunks) {
  let h = esc(text);
  (chunks || []).forEach(c => { try { h = h.replace(chunkRe(c.en), (m, pre, x) => `${pre}<span class="sk-chunk">${x}</span>`); } catch (e) {} });
  return h;
}
/* 他说的话：中文的地方淡淡标出来（不是错，是「这里要学一个表达」） */
function markZh(text) { return esc(text).replace(/([㐀-鿿，。、]+)/g, '<span class="sk-zh">$1</span>'); }
function playBtn(text, accent, label) { return `<button type="button" class="sk-play" data-say="${esc(text)}" data-lang="${esc(accent)}" data-use="sentence" title="听一遍"><i>▶</i>${label ? `<span>${esc(label)}</span>` : ''}</button>`; }
function clipBtn(id, label) { return `<button type="button" class="sk-play" data-clip="${esc(id)}"><i>▶</i><span>${esc(label)}</span></button>`; }
document.addEventListener('click', e => {
  const b = e.target.closest('[data-clip]'); if (!b) return;
  Speak.stop(); b.classList.add('saying');
  Speak.file('/speak/clip/' + b.dataset.clip, Speak.n).catch(() => toast('放不出来')).finally(() => b.classList.remove('saying'));
});
/* 先鼓励，再建议（DESIGN 5.67）：每次说完，先把做到的、进步的地方放在最上面，带一下动效；建议排在后面，只给一条。
   动效只在本人说完、点完之后放一次；系统设置了「减少动态效果」就不动。 */
/* 鼓励的样子是「纸本」的展览标题（DESIGN 5.68）：标题一个字一个字落下来，几粒金点从标题末尾散开，下面一行说具体做到了什么。
   soft：轻一点的鼓励（没散开的金点）；quiet：不放金点；big：说完一整件事时的大标题。o.icon 以前是徽章里的图标，现在不用。 */
function cheer(title, sub, o = {}) {
  const chars = [...String(title)], n = chars.length, sd = (0.08 + n * 0.055 + 0.2).toFixed(2);
  const sparks = o.quiet || o.soft ? '' : Array.from({length: 7}, (_, i) => {
    const a = -Math.PI * (0.15 + i * 0.12), r = 26 + (i % 3) * 12;
    return `<i class="sp" style="--x:${Math.min(92, 8 + n * 7)}%;--dx:${Math.round(Math.cos(a) * r)}px;--dy:${Math.round(Math.sin(a) * r)}px;width:${6 - (i % 3)}px;height:${6 - (i % 3)}px"></i>`;
  }).join('');
  return `<div class="sk-cheer${o.soft ? ' soft' : ''}${o.big ? ' big' : ''}" role="status" style="--sd:${sd}s">` +
    `<div class="t" aria-label="${esc(title)}">${chars.map((c, i) => `<span style="--i:${i}" aria-hidden="true">${esc(c)}</span>`).join('')}</div>${sparks}` +
    `${sub ? `<div class="s">${sub}</div>` : ''}</div>`;
}
function countUp(root) {
  $$('[data-to]', root).forEach(el => {
    const a = +el.dataset.from, b = +el.dataset.to, dec = String(el.dataset.to).includes('.') ? 1 : 0, t0 = performance.now();
    if (isNaN(a) || isNaN(b) || a === b || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const step = t => {
      const k = Math.min(1, (t - t0) / 800), e = 1 - Math.pow(1 - k, 3);
      el.textContent = (a + (b - a) * e).toFixed(dec);
      if (k < 1) requestAnimationFrame(step);
    };
    el.textContent = a.toFixed(dec); requestAnimationFrame(step);
  });
}
/* 和第一遍比，哪几样变好了（只说变好的，没变好的不提） */
function wins(rows) {
  const n = v => Math.round(v * 10) / 10;
  return (rows || []).flatMap(r => {
    if (r.of !== undefined) return r.b > 0 ? [`用上了 ${r.b} 个新表达`] : [];
    if (r.a === null || r.a === undefined || r.b === null || r.b === undefined) return [];
    const d = r.better === 'low' ? r.a - r.b : r.b - r.a;
    if (d <= 0) return [];
    return [{'秒': `开口快了 ${n(d)} 秒`, '次': `卡顿少了 ${n(d)} 次`, '处': `中文少了 ${n(d)} 处`}[r.unit] || `${r.k}好了 ${n(d)}`];
  });
}
/* 一组卡片依次浮上来 */
/* 一屏两栏（DESIGN 5.68「展厅」）：左边一件大作品，右边一栏展签。手机上上下排，电脑上左右排 */
const two = (main, side) => `<div class="sk-two"><div class="sk-main">${main}</div><aside class="sk-side">${side || ''}</aside></div>`;
/* 说完那一屏的大数字：挑一个变好了的数（没有就挑第一个有数的），从第一遍滚到这一遍 */
function heroNum(rows) {
  const has = r => r.a !== null && r.a !== undefined && r.b !== null && r.b !== undefined;
  const up = r => has(r) && (r.better === 'low' ? r.b < r.a : r.b > r.a);
  const r = (rows || []).find(up) || (rows || []).find(has);
  if (!r) return {html: '', row: null};
  const u = r.of !== undefined ? `<small>/${r.of}</small>` : r.unit === '秒' ? '<small>″</small>' : '';
  const a = r.of !== undefined ? `${r.a}/${r.of}` : `${r.a}${r.unit === '秒' ? '″' : r.unit ? ' ' + r.unit : ''}`;
  return {row: r, html: `<div class="sk-heronum"><div class="v"><span${up(r) ? ` data-from="${r.a}" data-to="${r.b}"` : ''}>${r.b}</span>${u}</div>` +
    `<div class="sk-lbl">${esc(r.k)} · 第一遍 ${esc(a)}</div></div>`};
}
const rise = (html, i) => `<div class="sk-rise" style="--d:${(0.12 * i).toFixed(2)}s">${html}</div>`;

function fmt(v, unit) { return v === null || v === undefined ? '—' : `${v}${unit ? ' ' + unit : ''}`; }
function compareTable(rows) {
  return `<div class="sk-cmp"><div class="hd"></div><div class="hd">第一遍</div><div class="hd">这一遍</div>` +
    rows.map((r, i) => {
      const has = r.a !== null && r.a !== undefined && r.b !== null && r.b !== undefined;
      const better = has && (r.better === 'low' ? r.b < r.a : r.b > r.a);
      const num = better ? `<span data-from="${r.a}" data-to="${r.b}">${r.b}</span>` : esc(String(r.b ?? '—'));
      const b = r.of !== undefined ? `${num} / ${r.of}` : (has ? `${num}${r.unit ? ' ' + esc(r.unit) : ''}` : fmt(r.b, r.unit));
      const a = r.of !== undefined ? `${r.a} / ${r.of}` : fmt(r.a, r.unit);
      return `<div>${esc(r.k)}</div><div>${a}</div><div class="${better ? 'now' : ''}">${b}${better ? `<span class="sk-up" style="--d:${(0.5 + 0.1 * i).toFixed(1)}s">↑</span>` : ''}</div>`;
    }).join('') + `</div>`;
}
function fixBox(fix) {
  if (!fix) return '';
  return `<div class="sk-fix"><div class="lbl">再好一点 · 只改一处</div><div>${esc(fix.wrong)} → <b>${esc(fix.right)}</b></div><div class="small muted">${esc(fix.why || '')}</div></div>`;
}
/* ---------------------------------------------------------------- 可以说得更好 + 照着说一遍（DESIGN 5.67）
   先夸，再把他的话改一改（最多 3 处，每处一句为什么），改好的马上能照着说，宽松判断，想说几遍都行。纠正是建议，不是判错。 */
const _w = w => w.toLowerCase().replace(/[^a-z0-9']/g, '');
/* 改好的句子里，和原话不一样的词标出来 */
function diffMark(orig, better) {
  const a = (orig || '').split(/\s+/).map(_w), bw = (better || '').split(/\s+/).filter(Boolean), b = bw.map(_w);
  const L = Array.from({length: a.length + 1}, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--)
    L[i][j] = a[i] && a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const keep = new Set();
  for (let i = 0, j = 0; i < a.length && j < b.length;) {
    if (a[i] && a[i] === b[j]) { keep.add(j); i++; j++; } else if (L[i + 1][j] >= L[i][j + 1]) i++; else j++;
  }
  return bw.map((w, j) => keep.has(j) || !b[j] ? esc(w) : `<b class="sk-new">${esc(w)}</b>`).join(' ');
}
function pointList(points) {
  return (points || []).length ? `<div class="sk-pts">${points.map((p, i) => `<div class="pt sk-rise" style="--d:${(0.25 + i * 0.1).toFixed(2)}s">` +
    `<div>${p.wrong ? `<span class="was">${esc(p.wrong)}</span> → ` : ''}<b>${esc(p.right)}</b>${p.zh ? ` <span class="small muted">${esc(p.zh)}</span>` : ''}</div>` +
    `${p.why ? `<div class="small muted">${esc(p.why)}</div>` : ''}</div>`).join('')}</div>` : '';
}
/* 「可以说得更好」卡：b = {better, points, good}，orig = 他的原话，o.session 照着说时带上 */
function betterBox(b, orig, o = {}) {
  if (!b || !b.better) return '';
  const pts = b.points || [];
  if (!pts.length) return `<div class="card sk-better">${cheer('这段说得很地道', '没有要改的地方。想更熟，就照着再说一遍。', {quiet: true, icon: '👍'})}${sayZone(b.better, o)}</div>`;
  return `<div class="card sk-better sk-rise" style="--d:${o.delay || 0.15}s"><div class="row" style="justify-content:space-between"><div class="sk-lbl" style="margin:0">${esc(o.title || '可以说得更好')} · ${pts.length} 处</div>${playBtn(b.better, o.accent || 'en-us', '听一遍')}</div>
    <div class="sk-en" style="margin-top:8px">${diffMark(orig, b.better)}</div>${pointList(pts)}${sayZone(b.better, o)}</div>`;
}
let _sayN = 0;
function sayZone(text, o = {}) {
  return `<div class="sk-sayz" data-text="${esc(text)}" data-session="${esc(o.session || '')}"><a href="#" class="sk-saybtn">照着说一遍</a></div>`;
}
/* 点「照着说一遍」：就地放一个话筒，说完标出说对的词；说多少遍都行 */
document.addEventListener('click', e => {
  const a = e.target.closest('.sk-saybtn'); if (!a) return;
  e.preventDefault(); Speak.stop();
  const z = a.closest('.sk-sayz'), n = ++_sayN, text = z.dataset.text;
  z.innerHTML = `<div class="sk-wave" id="sw${n}" hidden>${'<i></i>'.repeat(14)}</div>` +
    `<button type="button" class="sk-mic sm" id="sm${n}" aria-label="按住说话"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></button>` +
    `<div class="sk-hint" id="sh${n}">照着上面改好的说，松开结束</div>`;
  micButton($('#sm' + n), {hint: '#sh' + n, wave: '#sw' + n, recHint: '照着改好的说', onClip: async blob => {
    const r = await sendClip('/api/speak/say', blob, {text, session: z.dataset.session || 0});
    const words = r.marks.map((w, k) => `<span class="sk-word${w.ok ? ' got' : ' miss'}" style="--d:${(k * 0.04).toFixed(2)}s">${esc(w.w)}</span>`).join('');
    z.innerHTML = (r.ok ? cheer('说顺了！', '改好的说法已经到你嘴边了。', {quiet: r.got < r.of}) :
      cheer(`说对了 ${r.got} / ${r.of} 个词`, '灰色的几个再来一遍，越说越顺。', {soft: true, quiet: true, icon: '🎙'})) +
      `<div class="sk-en" style="margin:-4px 0 8px">${words}</div><a href="#" class="sk-saybtn">再说一遍</a>`;
  }});
});

function chunkList(chunks) {
  return `<div class="sk-list">` + chunks.map(c => `<div class="li"><div><b>${esc(c.en)}</b> <span class="small muted">${esc(c.zh)}</span>` +
    (c.yours ? `<div class="small faint">你刚才说的：${markZh(c.yours)}</div>` : '') + `</div></div>`).join('') + `</div>`;
}

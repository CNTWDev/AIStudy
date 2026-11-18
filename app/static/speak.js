/* 口语「开口练」的前端（app/speaking.py，DESIGN 5.67）：录音、四步主线、复习、和 AI 聊。
   录音直接录成 16kHz 单声道 16 位 WAV：各家语音识别都收，服务器不用转码（app/stt）。 */

/* ---------------------------------------------------------------- 录音 */
const Rec = {
  ctx: null, stream: null, node: null, src: null, bufs: [], rate: 48000, t0: 0, level: 0,
  ok() { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && (window.AudioContext || window.webkitAudioContext)); },
  async start() {
    Speak.stop();
    this.stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = this.ctx || new AC();
    if (this.ctx.state === 'suspended') await this.ctx.resume();
    this.rate = this.ctx.sampleRate; this.bufs = []; this.level = 0;
    this.src = this.ctx.createMediaStreamSource(this.stream);
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    this.node.onaudioprocess = e => {
      const d = e.inputBuffer.getChannelData(0); this.bufs.push(new Float32Array(d));
      let m = 0; for (let i = 0; i < d.length; i += 16) m = Math.max(m, Math.abs(d[i])); this.level = m;
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

/* 话筒按钮：按住说、松开结束；轻点一下 = 开始，再点一下 = 结束（说长一点不用一直按着）。
   onClip(blob, secs) 返回 Promise；期间按钮转圈。 */
function micButton(el, o) {
  const max = o.max || 90;
  let state = 'idle', downAt = 0, timer = null, wave = null;
  const hint = o.hint ? $(o.hint) : null, idleHint = hint ? hint.innerHTML : '';
  const set = s => { state = s; el.classList.toggle('rec', s === 'rec'); el.classList.toggle('busy', s === 'busy'); el.disabled = s === 'busy' || s === 'off'; };
  const tick = () => {
    const s = (performance.now() - Rec.t0) / 1000;
    if (hint) hint.innerHTML = `<b class="sk-time">${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}</b> · ${o.tapMode ? '再点一下结束' : '松开结束'}<br>${esc(o.recHint || '')}`;
    if (wave) { const bars = $$('i', wave), h = Math.min(1, Rec.level * 3); bars.forEach((b, i) => { b.style.height = (6 + h * 30 * (0.4 + 0.6 * Math.abs(Math.sin(s * 7 + i)))) + 'px'; }); }
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
    set('busy'); if (hint) hint.innerHTML = '正在听你说的…';
    try { await o.onClip(blob, secs); } catch (e) { toast(e.message || '没传上，再说一次'); }
    if (state === 'busy') set('idle');
    if (hint && state === 'idle') hint.innerHTML = idleHint;
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
  return {off() { set('off'); }, on() { set('idle'); if (hint) hint.innerHTML = idleHint; }};
}

async function sendClip(url, blob, fields) {
  const fd = new FormData(); fd.append('file', blob, 'speech.wav');
  Object.entries(fields || {}).forEach(([k, v]) => fd.append(k, v));
  const r = await fetch(url, {method: 'POST', body: fd});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || j.detail || '没传上，再说一次');
  return j;
}

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
function fmt(v, unit) { return v === null || v === undefined ? '—' : `${v}${unit ? ' ' + unit : ''}`; }
function compareTable(rows) {
  return `<div class="sk-cmp"><div class="hd"></div><div class="hd">第一遍</div><div class="hd">这一遍</div>` +
    rows.map(r => {
      const better = r.better === 'low' ? r.b < r.a : r.b > r.a;
      const b = r.of !== undefined ? `${r.b} / ${r.of}` : fmt(r.b, r.unit), a = r.of !== undefined ? `${r.a} / ${r.of}` : fmt(r.a, r.unit);
      return `<div>${esc(r.k)}</div><div>${a}</div><div class="${better ? 'now' : ''}">${b}</div>`;
    }).join('') + `</div>`;
}
function fixBox(fix) {
  if (!fix) return '';
  return `<div class="sk-fix"><div class="lbl">只改一处</div><div>${esc(fix.wrong)} → <b>${esc(fix.right)}</b></div><div class="small muted">${esc(fix.why || '')}</div></div>`;
}
function chunkList(chunks) {
  return `<div class="sk-list">` + chunks.map(c => `<div class="li"><div><b>${esc(c.en)}</b> <span class="small muted">${esc(c.zh)}</span>` +
    (c.yours ? `<div class="small faint">你刚才说的：${markZh(c.yours)}</div>` : '') + `</div></div>`).join('') + `</div>`;
}

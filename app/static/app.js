async function api(url, body, method) {
  const opt = {method: method || (body ? 'POST' : 'GET'), headers: {}};
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || data.detail || ('出错了 ' + r.status));
  return data;
}
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function $(s, el) { return (el || document).querySelector(s); }
function $$(s, el) { return [...(el || document).querySelectorAll(s)]; }

/* 题目组件：渲染一道题，作答后调用 onDone(result) */
function renderItem(box, item, opts) {
  opts = opts || {};
  const L = 'ABCDEFG';
  // 输入控件由服务端题型注册表给出（app/itemtypes.py 的 widget）：choice 选项 / self 自评 / text 输入框
  const W = item.widget || (item.type === 'mcq' ? 'choice' : item.type === 'short' ? 'self' : 'text');
  let html = `<div class="q">` + (item.src ? `<div class="muted small">📄 ${esc(item.src)}</div>` : '') +
    `<div style="font-weight:600">${esc(item.q)}</div>` +
    (item.code ? `<pre class="code">${esc(item.code)}</pre>` : '') +
    (item.zh ? `<div class="muted small">${esc(item.zh)}</div>` : '');
  if (W === 'choice') {
    html += item.options.map((o, i) => `<button class="opt" data-i="${i}">${L[i]}. ${esc(o)}</button>`).join('');
  } else if (W === 'self') {
    html += `<textarea class="ans" placeholder="先自己写一写（写关键词也行）"></textarea>`;
  } else {
    html += `<div class="row" style="margin-top:8px"><input type="text" class="ans grow" placeholder="你的答案" autocomplete="off">` +
      (item.unit ? `<span class="muted">${esc(item.unit)}</span>` : '') + `</div>`;
  }
  html += `<div class="qacts">` +
    `<button class="btn submit">${W === 'self' ? '看参考答案' : '提交'}</button>` +
    (item.hint ? `<button class="btn soft hintbtn">💡 提示</button>` : '') +
    (opts.dontKnow ? `<button class="linkbtn dkbtn">🤔 这道题还不会</button>` : '') + `</div>` +
    `<div class="hintbox"></div><div class="fbbox"></div></div>`;
  box.innerHTML = html;
  box.classList.add('askable'); box.dataset.askItem = item.item_id || item.id || ''; delete box.dataset.answered;
  const _done = opts.onDone; opts.onDone = res => { box.dataset.answered = '1'; flagRow(box, item); _done && _done(res); };
  let chosen = null;
  const t0 = Date.now();  // 做题用时：系统用它发现「会做但很慢」
  $$('.opt', box).forEach(b => b.onclick = () => { $$('.opt', box).forEach(x => x.classList.remove('sel')); b.classList.add('sel'); chosen = b.dataset.i; });
  const hb = $('.hintbtn', box);
  if (hb) hb.onclick = () => { $('.hintbox', box).innerHTML = `<div class="hint" style="margin-top:8px">💡 ${esc(item.hint)}</div>`; hb.remove(); };
  const dk = $('.dkbtn', box);
  if (dk) dk.onclick = async () => {
    dk.disabled = true;
    try {
      const res = await opts.dontKnow();
      $$('.opt', box).forEach(b => b.disabled = true);
      $$('.submit,.hintbtn,.dkbtn', box).forEach(b => b.remove());
      $('.fbbox', box).innerHTML = feedback(res);
      opts.onDone && opts.onDone(res);
    } catch (e) { dk.disabled = false; $('.fbbox', box).innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  const ans = $('.ans', box);
  if (ans && ans.tagName === 'INPUT') ans.addEventListener('keydown', e => { if (e.key === 'Enter') $('.submit', box).click(); });
  $('.submit', box).onclick = async () => {
    const btn = $('.submit', box);
    const answer = W === 'choice' ? chosen : (ans ? ans.value : '');
    if (W === 'choice' && chosen === null) { alert('先选一个答案'); return; }
    btn.disabled = true;
    try {
      const res = await opts.submit(answer, undefined, Date.now() - t0);
      if (res.reveal) {
        $('.fbbox', box).innerHTML = `<div class="fb ok"><b>参考答案：</b>${esc(res.answer)}` +
          (res.points && res.points.length ? `<ul>${res.points.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '') +
          `<div class="row"><span>对照要点，你答到了吗？</span><button class="btn sm selfok">基本答到</button><button class="btn ghost sm selfno">还差一些</button></div></div>`;
        btn.remove();
        const go = async v => { const r2 = await opts.submit(answer, v, Date.now() - t0); $('.fbbox', box).innerHTML += feedback(r2); opts.onDone && opts.onDone(r2); $$('.selfok,.selfno', box).forEach(x => x.remove()); };
        $('.selfok', box).onclick = () => go('ok'); $('.selfno', box).onclick = () => go('no');
        return;
      }
      if (W === 'choice') $$('.opt', box).forEach(b => { if (b.dataset.i === chosen) b.classList.add(res.correct ? 'right' : 'wrong'); b.disabled = true; });
      $('.fbbox', box).innerHTML = feedback(res);
      btn.remove(); if (dk) dk.remove();
      if (res.correct) cheer(box);
      opts.onDone && opts.onDone(res);
    } catch (e) { btn.disabled = false; $('.fbbox', box).innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
}
/* 做完一道题后可以标记「这道题有问题」：题库里的题会被很多孩子反复用，坏题要能被发现 */
const FLAG_REASONS = {wrong: '答案好像不对', unclear: '题目有错或看不懂', offtopic: '和这个知识点没关系'};
function flagRow(box, item) {
  const id = item.item_id || item.id;
  if (!id || $('.flagrow', box)) return;
  const row = document.createElement('div'); row.className = 'flagrow';
  row.innerHTML = '<button class="linkbtn">🚩 这道题有问题？</button>';
  row.firstChild.onclick = () => {
    row.innerHTML = '<span class="muted small">哪里不对？</span>' + Object.entries(FLAG_REASONS).map(([k, v]) => `<button class="chip" data-r="${k}">${v}</button>`).join('');
    $$('.chip', row).forEach(b => b.onclick = async () => {
      try { await api('/api/flag', {target: 'item', id, reason: b.dataset.r}); row.innerHTML = '<span class="muted small">✅ 谢谢！已经记下，会有人检查这道题。</span>'; }
      catch (e) { row.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
    });
  };
  box.appendChild(row);
}
const PRAISE = ['✅ 对了！', '✅ 漂亮！', '✅ 答对了，继续！', '✅ 很稳！', '✅ 就是这样！'];
function feedback(res) {
  if (res.dont_know) {
    return `<div class="fb dk pop">📌 没关系，知道自己哪里不会就是进步。先看懂它：` +
      `<div style="margin-top:6px"><b>答案：</b>${esc(res.answer)}</div>` +
      (res.explain ? `<div class="small" style="margin-top:6px">${esc(res.explain)}</div>` : '') +
      `<div class="small muted" style="margin-top:6px">已放进错题本，过几天再练一次就会了。</div></div>`;
  }
  if (res.correct === undefined) return '';
  return `<div class="fb ${res.correct ? 'ok' : 'no'} pop">${res.correct ? PRAISE[Math.floor(Math.random() * PRAISE.length)] : '差一点！正确答案是 <b>' + esc(res.answer) + '</b>'}` +
    (res.explain ? `<div class="small" style="margin-top:6px">${esc(res.explain)}</div>` : '') +
    (res.correct ? '' : `<div class="small muted">已放进错题本，过几天会再出现。做错也算练过，继续！</div>`) + `</div>`;
}

/* ---------- 即时反馈：小动画 ---------- */
function toast(text, ms) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = text;
  document.body.appendChild(t); setTimeout(() => t.classList.add('out'), ms || 1600); setTimeout(() => t.remove(), (ms || 1600) + 500);
}
function cheer(el) {
  const r = (el || document.body).getBoundingClientRect();
  for (let i = 0; i < 10; i++) {
    const p = document.createElement('span'); p.className = 'spark'; p.textContent = ['⭐', '✨', '🌟'][i % 3];
    p.style.left = (r.left + r.width / 2 + (Math.random() - .5) * 80) + 'px'; p.style.top = (r.top + Math.min(r.height, 120) / 2) + 'px';
    p.style.setProperty('--dx', ((Math.random() - .5) * 220) + 'px'); p.style.setProperty('--dy', (-60 - Math.random() * 140) + 'px');
    document.body.appendChild(p); setTimeout(() => p.remove(), 1000);
  }
}
function confetti() {
  const colors = ['#f6b400', '#ffd25c', '#ff9bae', '#7aa7ff', '#a893ff', '#d6eeff'];
  for (let i = 0; i < 80; i++) {
    const p = document.createElement('i'); p.className = 'confetti';
    p.style.left = Math.random() * 100 + 'vw'; p.style.background = colors[i % colors.length];
    p.style.animationDelay = Math.random() * .5 + 's'; p.style.transform = `rotate(${Math.random() * 360}deg)`;
    document.body.appendChild(p); setTimeout(() => p.remove(), 3000);
  }
}
/* 做完一项任务：+1 ⭐ 并回到今天 */
async function finishTask(type, extra) {
  try { await api('/api/plan/task-done', Object.assign({type}, extra || {})); } catch (e) {}
  sessionStorage.setItem('justDone', type);
  location.href = '/today';
}

/* 朗读（浏览器自带语音，英文） */
function say(text, lang) {
  if (!window.speechSynthesis) return;
  const u = new SpeechSynthesisUtterance(text); u.lang = lang || 'en-US'; u.rate = .85;
  speechSynthesis.cancel(); speechSynthesis.speak(u);
}

/* ---------- 问一问小助手：每个页面右下角，结合当前题目引导式回答（不给答案） ---------- */
const Ask = {
  box: null, tid: null, key: '',
  current() {
    // 孩子最近点过 / 正在看的那道题；没有就用屏幕上第一道
    const vis = $$('.askable').filter(b => { const r = b.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; });
    return (Ask.focus && document.body.contains(Ask.focus) ? Ask.focus : null) || vis[0] || $('.askable');
  },
  ctx() {
    const b = Ask.current();
    const sel = (window.getSelection() || '').toString().trim();
    const h1 = $('main h1');
    let text = sel;
    if (!text && b) text = ($('.q > div', b) || b).innerText;
    if (!text) text = ($('main') || document.body).innerText;
    return {path: location.pathname, title: h1 ? h1.innerText : document.title, text: text.slice(0, 1500),
            item_id: b ? b.dataset.askItem : '', kp_id: (window.ASK_CTX || {}).kp || '', answered: !!(b && b.dataset.answered)};
  },
  label(c) { return c.item_id ? '这道题：' + c.text.replace(/\s+/g, ' ').slice(0, 40) : (c.text && getSelection().toString() ? '选中的：' + c.text.slice(0, 40) : '这个页面：' + c.title); },
  open() {
    $('#askpanel').classList.add('on');
    const c = Ask.ctx(), key = c.item_id || c.path;
    if (key !== Ask.key) { Ask.key = key; Ask.tid = null; $('#askmsgs').innerHTML = ''; Ask.say('ai', `我是${$('#askbtn').dataset.name} ${$('#askbtn').dataset.icon} 哪里不明白就问我。我不会直接告诉你答案，但会陪你一步一步想出来！`); }
    $('#askctx').textContent = '📎 ' + Ask.label(c);
    $('#askq').focus();
  },
  say(role, text) {
    const d = document.createElement('div'); d.className = 'am ' + role; d.textContent = text;
    $('#askmsgs').appendChild(d); $('#askmsgs').scrollTop = 1e6; return d;
  },
  async send(q) {
    q = (q || $('#askq').value).trim(); if (!q) return;
    $('#askq').value = ''; Ask.say('user', q);
    const wait = Ask.say('ai', '…'); wait.classList.add('loading');
    try {
      const r = await api('/api/ask', {question: q, thread_id: Ask.tid, ctx: Ask.tid ? {} : Ask.ctx()});
      Ask.tid = r.thread_id; wait.remove(); Ask.say('ai', r.reply);
    } catch (e) { wait.remove(); Ask.say('err', e.message); }
  },
};
document.addEventListener('DOMContentLoaded', () => {
  if (!$('#askbtn')) return;
  document.addEventListener('pointerdown', e => { const b = e.target.closest && e.target.closest('.askable'); if (b) Ask.focus = b; }, true);
  $('#askbtn').onclick = Ask.open;
  $('#askclose').onclick = () => $('#askpanel').classList.remove('on');
  $('#asknew').onclick = () => { Ask.key = ''; Ask.open(); };
  $('#askform').onsubmit = e => { e.preventDefault(); Ask.send(); };
  $$('#askchips button').forEach(b => b.onclick = () => Ask.send(b.textContent));
});

/* ---------- 激励：连对、点亮知识点（少而有效：只奖励真实的进步） ---------- */
const Combo = {
  n: 0,
  hit(ok, el) {
    Combo.n = ok ? Combo.n + 1 : 0;
    let b = $('#combo');
    if (!b) { b = document.createElement('div'); b.id = 'combo'; document.body.appendChild(b); }
    if (Combo.n >= 2) {
      b.textContent = `连对 ×${Combo.n} ${Combo.n >= 5 ? '🔥🔥' : '🔥'}`;
      b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump', 'on');
      if (Combo.n === 3 || Combo.n === 5 || Combo.n % 10 === 0) cheer(el || b);
    } else b.classList.remove('on');
  },
};
/* 答题结果里带 lit：第一次掌握这个知识点 → 点亮 */
function celebrate(res, el) {
  if (res && res.lit) {
    toast(`🌟 点亮新知识点：${res.lit.name}（今天第 ${res.lit.today} 个）`, 2400);
    confetti();
  } else if (res && res.probe && res.probe.inferred) {
    toast(`🗺️ 地图又亮了一块：顺带摸清 ${res.probe.inferred + 1} 个知识点`, 2000);
  }
}

/* ---------- 跟自己比：专注计时、「上周的我」、个人最好（PB） ----------
   专注只在页面开着、而且最近一分钟里有点击 / 打字 / 滚动时才走；切走、发呆不算。
   一组做完记一笔：正确率不到 80% 的不算纪录，速度只比「每道答对的题平均用时」。 */
const Run = {
  on: false,
  start(kind, opts) {
    opts = opts || {};
    Object.assign(Run, {on: true, kind, total: opts.total || 0, n: 0, right: 0, combo: 0, best: 0, active: 0,
                        t0: Date.now(), last: Date.now(), tick: Date.now(), saved: false, pb: null, ghost: null});
    let el = $('#runbar');
    if (!el) {
      el = document.createElement('div'); el.id = 'runbar'; el.className = 'runbar';
      el.innerHTML = `<div class="row small"><span>⏱ 专注 <b class="rt">0:00</b></span><span class="muted rpb"></span><span class="grow"></span><span class="rg muted"></span></div>` +
        (opts.race === false ? '' : `<div class="race"><i class="ghost" title="上周的我"></i><i class="me" title="我"></i></div>`);
      if (opts.mount) opts.mount.insertAdjacentElement('beforebegin', el);
      else ($('main h1') || document.body).insertAdjacentElement('afterend', el);
    }
    Run.el = el;
    api('/api/records/' + kind).then(r => { Run.pb = r.pb; Run.ghost = r.ghost; Run.draw(); }).catch(() => {});
    if (!Run.bound) {
      Run.bound = true;
      ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach(e => addEventListener(e, () => { Run.last = Date.now(); }, {passive: true, capture: true}));
      document.addEventListener('visibilitychange', () => { Run.tick = Date.now(); if (!document.hidden) Run.last = Date.now(); });
      setInterval(Run.step, 1000);
    }
    Run.draw();
  },
  step() {
    if (!Run.on) return;
    const now = Date.now();
    if (!document.hidden && now - Run.last < 60000) Run.active += Math.min(now - Run.tick, 5000);
    Run.tick = now; Run.draw();
  },
  mmss(ms) { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; },
  hit(ok) {
    if (!Run.on) return;
    Run.n++; if (ok) { Run.right++; Run.combo++; Run.best = Math.max(Run.best, Run.combo); } else Run.combo = 0;
    Run.draw();
  },
  draw() {
    const el = Run.el; if (!el) return;
    $('.rt', el).textContent = Run.mmss(Run.active);
    if (Run.pb && Run.pb.focus >= 60000) $('.rpb', el).textContent = `· 最长 ${Run.mmss(Run.pb.focus)}` + (Run.active > Run.pb.focus ? ' 🔥 正在破纪录' : '');
    const race = $('.race', el);
    if (!race) return;
    const tot = Math.max(Run.total, Run.n, 1);
    $('.me', race).style.left = (100 * Math.min(Run.right, tot) / tot) + '%';
    const g = $('.ghost', race);
    if (Run.ghost) {
      // 上周的我：按上周每道答对的题平均用时，这会儿应该已经答对几题
      const gr = Run.active / Run.ghost;
      g.style.display = ''; g.style.left = (100 * Math.min(gr, tot) / tot) + '%';
      if (Run.right >= 2) {
        const d = Math.round((Run.ghost * Run.right - Run.active) / 1000);
        $('.rg', el).textContent = d >= 0 ? `比上周的我快 ${d} 秒 👟` : `上周的我领先 ${-d} 秒`;
      } else $('.rg', el).textContent = '👻 上周的我在跑';
    } else { g.style.display = 'none'; $('.rg', el).textContent = Run.pb && Run.pb.runs ? '' : '第一次：先立个纪录'; }
  },
  async finish(extra) {
    if (!Run.on || Run.saved) return null;
    Run.step(); Run.saved = true; Run.on = false;
    const body = Object.assign({kind: Run.kind, n_items: Run.n, n_right: Run.right, ms_active: Run.active,
                                ms_total: Date.now() - Run.t0, best_combo: Run.best}, extra || {});
    try {
      const r = await api('/api/run', body);
      (r.pbs || []).forEach((p, i) => setTimeout(() => { toast('🏅 新 PB！' + p.label, 2600); cheer(Run.el); }, 400 + i * 1200));
      if (Run.el) $('.rpb', Run.el).textContent = r.pbs && r.pbs.length ? '🏅 破了 ' + r.pbs.length + ' 项个人纪录' : (r.first ? '已立下第一个纪录' : '');
      return r;
    } catch (e) { return null; }
  },
};

/* ---------- 划词：在网站任何页面选中文字 → 查词 / 翻译 / 加入复习 / 问一问 ----------
   系统按行为自动记录：同一个词或句子查第二次、做题时查的，自动放进复习（不用孩子手动加）。 */
const QuickLook = {
  ctx: '', item: '',
  panel() {
    let p = $('#qlpanel');
    if (!p) {
      p = document.createElement('div'); p.id = 'qlpanel';
      p.innerHTML = `<form class="row" id="qlform"><input id="qlq" class="grow" type="text" placeholder="输入英文单词、中文词语或一句话" autocomplete="off">` +
        `<button class="btn sm">查</button><button type="button" class="btn ghost sm" id="qlx">✕</button></form><div id="qlres"></div>`;
      document.body.appendChild(p);
      $('#qlx').onclick = () => p.classList.remove('on');
      $('#qlform').onsubmit = e => { e.preventDefault(); QuickLook.auto($('#qlq').value); };
    }
    p.classList.add('on'); return p;
  },
  open() { QuickLook.ctx = ''; QuickLook.item = ''; QuickLook.panel(); $('#qlq').focus(); },
  auto(q) { q = (q || '').trim(); return QuickLook.isWord(q) ? QuickLook.go(q) : QuickLook.translate(q); },
  isWord(q) { return /[一-鿿]/.test(q) ? q.length <= 6 : q.split(/\s+/).length <= 3 && q.length <= 30; },
  lang(q) { return /[一-鿿]/.test(q) && !/[A-Za-z]{3,}/.test(q) ? 'zh' : 'en'; },
  saved(r) {
    if (r.auto_added) return `<p class="small" style="color:var(--ok)">✓ 已自动加入复习（${esc(r.auto_added)}），明天会再见到它</p>`;
    if (r.saved) return `<p class="small muted">✓ 已经在复习里了</p>`;
    return `<button class="btn sm" id="qlfav">➕ 加入复习</button>`;
  },
  bindFav(front, meaning) {
    const b = $('#qlfav'); if (!b) return;
    b.onclick = async () => {
      b.disabled = true;
      await api('/api/collect', {text: front, meaning, context: QuickLook.ctx, page: location.pathname});
      b.textContent = '✓ 已加入复习，明天开始复习'; toast('➕ 加入复习');
    };
  },
  async go(q) {
    q = (q || '').trim(); if (!q) return;
    QuickLook.panel(); $('#qlq').value = q;
    const lang = QuickLook.lang(q);
    $('#qlres').innerHTML = '<span class="loading">正在查</span>';
    try {
      const r = await api('/api/lookup', {q, context: QuickLook.ctx, lang, auto: true, item_id: QuickLook.item, page: location.pathname});
      $('#qlres').innerHTML = `<h3 style="margin:8px 0 4px">${esc(r.word || q)} <span class="muted small">${esc(r.phonetic || r.pinyin || '')} ${esc(r.pos || '')}</span>` +
        (lang === 'en' ? ` <button class="btn ghost sm" id="qlsay">🔊</button>` : '') + `</h3>` +
        `<p style="margin:4px 0"><b>${esc(r.meaning || '')}</b>${r.simple_en ? `<br><span class="muted small">${esc(r.simple_en)}</span>` : ''}</p>` +
        (r.example ? `<p class="small">例：${esc(r.example)}${r.example_zh ? `<br><span class="muted">${esc(r.example_zh)}</span>` : ''}</p>` : '') +
        (r.tip ? `<div class="hint">💡 ${esc(r.tip)}</div>` : '') + QuickLook.saved(r);
      if ($('#qlsay')) $('#qlsay').onclick = () => say(r.word || q);
      QuickLook.bindFav(r.word || q, r.meaning || '');
    } catch (e) { $('#qlres').innerHTML = `<div class="err">${esc(e.message)}</div>` + `<button class="btn sm" id="qlfav">➕ 先加入复习</button>`; QuickLook.bindFav(q, ''); }
  },
  async translate(q) {
    q = (q || '').trim(); if (!q) return;
    QuickLook.panel(); $('#qlq').value = q.slice(0, 200);
    $('#qlres').innerHTML = '<span class="loading">正在翻译</span>';
    try {
      const r = await api('/api/translate', {text: q, item_id: QuickLook.item, page: location.pathname});
      $('#qlres').innerHTML = `<p class="small muted" style="margin:8px 0 2px">${esc(q.slice(0, 300))}</p>` +
        `<p style="margin:4px 0"><b>${esc(r.meaning || '')}</b></p>` +
        (r.structure ? `<p class="small">🧩 ${esc(r.structure)}</p>` : '') +
        ((r.points || []).length ? `<p class="small">${r.points.map(x => `<span class="pill">${esc(x.text)}：${esc(x.note)}</span>`).join(' ')}</p>` : '') +
        QuickLook.saved(r);
      QuickLook.bindFav(q.slice(0, 300), r.meaning || '');
    } catch (e) { $('#qlres').innerHTML = `<div class="err">${esc(e.message)}</div>` + `<button class="btn sm" id="qlfav">➕ 先加入复习</button>`; QuickLook.bindFav(q, ''); }
  },
};
const SelMenu = {
  el: null, text: '',
  hide() { if (SelMenu.el) SelMenu.el.classList.remove('on'); },
  show() {
    const sel = getSelection();
    const text = (sel ? sel.toString() : '').trim();
    if (!text || text.length > 600 || !sel.rangeCount) return SelMenu.hide();
    const node = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    if (!node || node.closest('input,textarea,#qlpanel,#askpanel,#selmenu,.tabs,header') || node.closest('#reader')) return SelMenu.hide();
    SelMenu.text = text;
    const block = node.closest('p,li,.q,.card,td,div') || node;
    QuickLook.ctx = (block.innerText || '').slice(0, 300);
    const ab = node.closest('.askable'); QuickLook.item = ab ? (ab.dataset.askItem || '') : '';
    if (!SelMenu.el) {
      SelMenu.el = document.createElement('div'); SelMenu.el.id = 'selmenu';
      document.body.appendChild(SelMenu.el);
      SelMenu.el.addEventListener('pointerdown', e => e.preventDefault());  // 点菜单时不丢掉选区
      SelMenu.el.onclick = e => {
        const b = e.target.closest('button'); if (!b) return;
        const t = SelMenu.text; SelMenu.hide();
        if (b.dataset.a === 'look') QuickLook.go(t);
        else if (b.dataset.a === 'tr') QuickLook.translate(t);
        else if (b.dataset.a === 'add') { api('/api/collect', {text: t, context: QuickLook.ctx, page: location.pathname}).then(() => toast('➕ 已加入复习')).catch(err => toast(err.message)); }
        else if (b.dataset.a === 'ask' && window.Ask && $('#askbtn')) Ask.open();
      };
    }
    const word = QuickLook.isWord(text);
    SelMenu.el.innerHTML = (word ? `<button data-a="look">🔍 查词</button>` : '') + `<button data-a="tr">🌐 翻译</button>` +
      `<button data-a="add">➕ 加入复习</button>` + ($('#askbtn') ? `<button data-a="ask">${esc($('#askbtn').dataset.icon)} 问${esc($('#askbtn').dataset.name)}</button>` : '');
    const r = sel.getRangeAt(0).getBoundingClientRect();
    const top = r.top + scrollY - 46, left = Math.max(8, Math.min(r.left + scrollX + r.width / 2 - 120, scrollX + innerWidth - 260));
    Object.assign(SelMenu.el.style, {top: (top < scrollY + 4 ? r.bottom + scrollY + 8 : top) + 'px', left: left + 'px'});
    SelMenu.el.classList.add('on');
  },
};
document.addEventListener('DOMContentLoaded', () => {
  const b = $('#qlbtn'); if (b) b.onclick = e => { e.preventDefault(); QuickLook.open(); };
  if (!document.body.dataset.kid) return;  // 只有孩子账号在学习状态下才有划词菜单
  let t = null;
  const later = () => { clearTimeout(t); t = setTimeout(SelMenu.show, 250); };
  document.addEventListener('mouseup', later);
  document.addEventListener('touchend', later);
  document.addEventListener('keyup', e => { if (e.shiftKey) later(); });
  document.addEventListener('selectionchange', () => { if (!(getSelection() || '').toString().trim()) SelMenu.hide(); });
});

/* 学习时长自动记录：页面在前台、最近有操作（读文章时放宽到 3 分钟）才计时；每 30 秒报一次，离开页面时用 sendBeacon 补报。
   只有孩子自己的账号计时，家长查看不算。 */
const Beat = {
  acc: 0, last: Date.now(), tick: Date.now(),
  init() {
    if (!document.body.dataset.kid) return;
    ['pointerdown', 'keydown', 'scroll', 'touchstart', 'wheel', 'input'].forEach(e => addEventListener(e, () => { Beat.last = Date.now(); }, {passive: true, capture: true}));
    document.addEventListener('visibilitychange', () => { Beat.tick = Date.now(); if (document.hidden) Beat.send(true); else Beat.last = Date.now(); });
    addEventListener('pagehide', () => Beat.send(true));
    setInterval(Beat.step, 1000);
    setInterval(() => Beat.send(false), 30000);
  },
  step() {
    const now = Date.now(), idle = $('.reader') ? 180000 : 90000;
    if (!document.hidden && now - Beat.last < idle) Beat.acc += Math.min(now - Beat.tick, 5000);
    Beat.tick = now;
  },
  send(leaving) {
    const s = Math.floor(Beat.acc / 1000);
    if (s < 1) return;
    Beat.acc -= s * 1000;
    const body = JSON.stringify({s});
    if (leaving && navigator.sendBeacon) { navigator.sendBeacon('/api/beat', new Blob([body], {type: 'application/json'})); return; }
    fetch('/api/beat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body, keepalive: true})
      .then(r => r.json()).then(r => document.dispatchEvent(new CustomEvent('beat', {detail: r.minutes}))).catch(() => { Beat.acc += s * 1000; });
  },
};
document.addEventListener('DOMContentLoaded', Beat.init);

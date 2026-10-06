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
  let html = `<div class="q"><div style="font-weight:600">${esc(item.q)}</div>` +
    (item.zh ? `<div class="muted small">${esc(item.zh)}</div>` : '');
  if (item.type === 'mcq') {
    html += item.options.map((o, i) => `<button class="opt" data-i="${i}">${L[i]}. ${esc(o)}</button>`).join('');
  } else if (item.type === 'short') {
    html += `<textarea class="ans" placeholder="先自己写一写（写关键词也行）"></textarea>`;
  } else {
    html += `<div class="row" style="margin-top:8px"><input type="text" class="ans grow" placeholder="你的答案" autocomplete="off">` +
      (item.unit ? `<span class="muted">${esc(item.unit)}</span>` : '') + `</div>`;
  }
  html += `<div class="row" style="margin-top:10px">` +
    (item.hint ? `<button class="btn ghost sm hintbtn">💡 提示</button>` : '') +
    `<button class="btn submit">${item.type === 'short' ? '看参考答案' : '提交'}</button>` +
    (opts.dontKnow ? `<button class="btn ghost sm dkbtn">🤔 这道题还不会</button>` : '') + `</div>` +
    `<div class="hintbox"></div><div class="fbbox"></div></div>`;
  box.innerHTML = html;
  let chosen = null;
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
    const answer = item.type === 'mcq' ? chosen : (ans ? ans.value : '');
    if (item.type === 'mcq' && chosen === null) { alert('先选一个答案'); return; }
    btn.disabled = true;
    try {
      const res = await opts.submit(answer);
      if (res.reveal) {
        $('.fbbox', box).innerHTML = `<div class="fb ok"><b>参考答案：</b>${esc(res.answer)}` +
          (res.points && res.points.length ? `<ul>${res.points.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '') +
          `<div class="row"><span>对照要点，你答到了吗？</span><button class="btn sm selfok">基本答到</button><button class="btn ghost sm selfno">还差一些</button></div></div>`;
        btn.remove();
        const go = async v => { const r2 = await opts.submit(answer, v); $('.fbbox', box).innerHTML += feedback(r2); opts.onDone && opts.onDone(r2); $$('.selfok,.selfno', box).forEach(x => x.remove()); };
        $('.selfok', box).onclick = () => go('ok'); $('.selfno', box).onclick = () => go('no');
        return;
      }
      if (item.type === 'mcq') $$('.opt', box).forEach(b => { if (b.dataset.i === chosen) b.classList.add(res.correct ? 'right' : 'wrong'); b.disabled = true; });
      $('.fbbox', box).innerHTML = feedback(res);
      btn.remove(); if (dk) dk.remove();
      if (res.correct) cheer(box);
      opts.onDone && opts.onDone(res);
    } catch (e) { btn.disabled = false; $('.fbbox', box).innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
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
  const colors = ['#2f6f5e', '#e0a100', '#d9534f', '#3b82f6', '#a855f7', '#10b981'];
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

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
    `<button class="btn submit">${item.type === 'short' ? '看参考答案' : '提交'}</button></div>` +
    `<div class="hintbox"></div><div class="fbbox"></div></div>`;
  box.innerHTML = html;
  let chosen = null;
  $$('.opt', box).forEach(b => b.onclick = () => { $$('.opt', box).forEach(x => x.classList.remove('sel')); b.classList.add('sel'); chosen = b.dataset.i; });
  const hb = $('.hintbtn', box);
  if (hb) hb.onclick = () => { $('.hintbox', box).innerHTML = `<div class="hint" style="margin-top:8px">💡 ${esc(item.hint)}</div>`; hb.remove(); };
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
      btn.remove();
      opts.onDone && opts.onDone(res);
    } catch (e) { btn.disabled = false; $('.fbbox', box).innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
}
function feedback(res) {
  if (res.correct === undefined) return '';
  return `<div class="fb ${res.correct ? 'ok' : 'no'}">${res.correct ? '✅ 对了！' : '❌ 再看看：正确答案是 <b>' + esc(res.answer) + '</b>'}` +
    (res.explain ? `<div class="small" style="margin-top:6px">${esc(res.explain)}</div>` : '') +
    (res.correct ? '' : `<div class="small muted">已放进错题本，过几天会再出现。</div>`) + `</div>`;
}

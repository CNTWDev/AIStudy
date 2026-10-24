/* o：请求方法（字符串），或 {method, onPart}。给了 onPart 就要「边写边看」（DESIGN 5.40）：
   服务端一行一个 JSON，{"part": {栏: 目前写到的字}} 交给 onPart，最后 {"done": 结果} 作为返回值；不支持的接口照常整份返回 */
async function api(url, body, o) {
  if (typeof o === 'string') o = {method: o};
  o = o || {};
  const opt = {method: o.method || (body ? 'POST' : 'GET'), headers: {}};
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  if (o.onPart) opt.headers.Accept = 'application/x-ndjson, application/json';
  const r = await fetch(url, opt);
  if (o.onPart && r.ok && (r.headers.get('Content-Type') || '').includes('ndjson')) return apiLines(r, o.onPart);
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || data.detail || ('出错了 ' + r.status));
  return data;
}
async function apiLines(r, onPart) {
  const rd = r.body.getReader(), dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const {value, done} = await rd.read();
    buf += dec.decode(value, {stream: !done});
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;  // 等太久时服务端发的空行
      const m = JSON.parse(line);
      if (m.part) onPart(m.part);
      else if ('done' in m) return m.done;
      else throw new Error(m.error || '出错了，请再试一次');
    }
    if (done) throw new Error('连接断了，请再点一次');
  }
}
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function $(s, el) { return (el || document).querySelector(s); }
function $$(s, el) { return [...(el || document).querySelectorAll(s)]; }

/* ---------- 题目的统一展示 ----------
   所有显示题目的地方用同一套样子（服务端是 _ui.html 的 question 宏，数据来自 itemtypes.view）：
   · 不带答案（出题）：题干 + 选项（A B C…）/ 输入框
   · 带答案（判完、错题本、订正过的卷子）：选项上标出「✓ 正确答案」「我选的」，下面分栏写 答案 / 我的答案 / 解析 / 要点 */
const QKEYS = 'ABCDEFGH';
function qStem(item, num) {
  return (item.src ? `<div class="qc-meta"><span>📄 ${esc(item.src)}</span></div>` : '') + qAudio(item.audio) +
    `<div class="qc-stem">${num ? `<span class="qc-n">${esc(num)}</span>` : ''}${esc(item.q)}</div>` +
    (item.code ? `<pre class="code">${esc(item.code)}</pre>` : '') +
    (item.zh ? `<div class="qc-zh">${esc(item.zh)}</div>` : '') + qFig(item.fig);
}
/* 题目上的图：svg 是服务端按画图说明画好的（app/figures/），img 是原卷上裁下来的图 */
function qFig(f) {
  if (!f) return '';
  return `<figure class="qc-fig" tabindex="0" title="点一下放大">${f.svg ? f.svg : `<img src="${esc(f.img)}" alt="${esc(f.alt)}" loading="lazy">`}</figure>`;
}
/* 听力题的录音（app/audio/）：一句一句交给朗读服务，按角色换声音。作答前最多听 plays 遍，答完不限 */
function qAudio(a) {
  if (!a || !a.lines || !a.lines.length) return '';
  return `<div class="qc-audio" data-plays="${+a.plays || 2}"><button type="button" class="btn sm qa-play">🔊 听录音</button>` +
    `<button type="button" class="btn ghost sm qa-slow" title="慢一点">🐢</button><span class="muted small qa-left"></span>` +
    `<script type="application/json">${JSON.stringify(a).replace(/</g, '\\u003c')}</script></div>`;
}
document.addEventListener('click', async e => {
  const el = e.target.closest && e.target.closest('.qc-audio'); if (!el) return;
  if (e.target.closest('.qa-slow')) { Speak.setSlow(!Speak.slow); e.target.closest('.qa-slow').classList.toggle('on', Speak.slow); return; }
  const btn = e.target.closest('.qa-play'); if (!btn) return;
  const a = JSON.parse($('script', el).textContent);
  const box = el.closest('.q.qc'), limited = box && !box.closest('[data-answered]');
  const played = +(el.dataset.played || 0);
  if (limited && played >= +el.dataset.plays) return;
  el.dataset.played = played + 1;
  const left = +el.dataset.plays - played - 1;
  $('.qa-left', el).textContent = limited ? (left > 0 ? `还能听 ${left} 遍` : '已经听完了，答题吧') : '';
  btn.disabled = true; btn.textContent = '🔊 正在播放…';
  for (const ln of a.lines) { if (!(await Speak.play(ln.text, a.lang, 'sentence', ln.who))) break; }
  btn.textContent = '🔊 再听一遍'; btn.disabled = limited && left <= 0;
});
/* 点图放大看 */
document.addEventListener('click', e => {
  const f = e.target.closest && e.target.closest('.qc-fig');
  if (!f) return;
  const box = document.createElement('div');
  box.className = 'fig-zoom';
  box.innerHTML = `<div class="fig-zoom-in">${f.innerHTML}</div>`;
  box.onclick = () => box.remove();
  document.body.appendChild(box);
});

/* 框出原卷照片上的图：pages 是整页照片的地址，box 是千分比 [左, 上, 右, 下]；
   onSave({page, box}) 或 onSave({remove: true}) 由调用的页面去存 */
function figCrop(opts) {
  let page = opts.page || 1, box = opts.box || null;
  const ov = document.createElement('div');
  ov.className = 'fig-crop';
  ov.innerHTML = `<div class="fig-crop-in card">
    <div class="row small"><b class="grow">在照片上拖一个框，框住整张图</b>
      ${opts.pages.length > 1 ? opts.pages.map((_, i) => `<button class="btn ghost sm pg" data-p="${i + 1}">第 ${i + 1} 张</button>`).join('') : ''}</div>
    <div class="fig-crop-stage"><img draggable="false"><div class="fig-crop-box"></div></div>
    <div class="row" style="margin-top:8px"><button class="btn sm save">保存</button>
      ${opts.box ? '<button class="btn ghost sm rm">去掉这张图</button>' : ''}<button class="btn ghost sm cancel">取消</button></div></div>`;
  document.body.appendChild(ov);
  const img = $('img', ov), rect = $('.fig-crop-box', ov), stage = $('.fig-crop-stage', ov);
  const draw = () => {
    rect.style.display = box ? '' : 'none';
    if (box) Object.assign(rect.style, {left: box[0] / 10 + '%', top: box[1] / 10 + '%', width: (box[2] - box[0]) / 10 + '%', height: (box[3] - box[1]) / 10 + '%'});
    $$('.pg', ov).forEach(b => b.classList.toggle('soft', +b.dataset.p === page));
  };
  const show = p => { page = p; img.src = opts.pages[p - 1]; draw(); };
  $$('.pg', ov).forEach(b => b.onclick = () => { box = null; show(+b.dataset.p); });
  const at = e => {
    const r = img.getBoundingClientRect();
    return [Math.round(Math.min(1000, Math.max(0, (e.clientX - r.left) / r.width * 1000))),
            Math.round(Math.min(1000, Math.max(0, (e.clientY - r.top) / r.height * 1000)))];
  };
  let start = null;
  stage.onpointerdown = e => { e.preventDefault(); stage.setPointerCapture(e.pointerId); start = at(e); box = [...start, ...start]; draw(); };
  stage.onpointermove = e => {
    if (!start) return;
    const p = at(e);
    box = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])]; draw();
  };
  stage.onpointerup = () => { start = null; };
  const done = async payload => {
    try { await opts.onSave(payload); ov.remove(); } catch (err) { toast(err.message); }
  };
  $('.save', ov).onclick = () => box && box[2] - box[0] > 20 && box[3] - box[1] > 20 ? done({page, box}) : toast('先在照片上拖一个框');
  const rm = $('.rm', ov);
  if (rm) rm.onclick = () => done({remove: true});
  $('.cancel', ov).onclick = () => ov.remove();
  show(page);
}
function qOpt(text, i, tag) { return `<${tag || 'button'} class="opt${tag ? ' static' : ''}" data-i="${i}"><b class="qc-key">${QKEYS[i] || i + 1}</b><span class="qc-ot">${esc(text)}</span></${tag || 'button'}>`; }
/* 选项上标出正确答案和自己选的 */
function qMark(box, rightIdx, mineIdx) {
  $$('.opt', box).forEach((b, i) => {
    if (i === +rightIdx && rightIdx != null && rightIdx !== '') { b.classList.add('right'); if (!$('.qc-tag.ok', b)) b.insertAdjacentHTML('beforeend', '<span class="qc-tag ok">✓ 正确答案</span>'); }
    if (mineIdx != null && mineIdx !== '' && i === +mineIdx) {
      if (i !== +rightIdx) b.classList.add('wrong');
      if (!$('.qc-tag.mine', b)) b.insertAdjacentHTML('beforeend', `<span class="qc-tag mine ${i === +rightIdx ? 'ok' : 'no'}">${i === +rightIdx ? '我选的 ✓' : '我选的'}</span>`);
    }
  });
}
/* 带答案的几栏：答案 / 我的答案 / 解析 / 要点 */
function qAns(a) {
  const rows = [];
  if (a.answer != null && a.answer !== '') rows.push(['答案', esc(a.answer)]);
  if (a.mine) rows.push(['我的答案', esc(a.mine)]);
  if (a.explain) rows.push(['解析', `<span class="qc-explain">${esc(a.explain)}</span>`]);
  if (a.points && a.points.length) rows.push(['要点', `<ul>${a.points.map(p => `<li>${esc(p)}</li>`).join('')}</ul>`]);
  if (a.transcript && a.transcript.length) rows.push(['听力原文', a.transcript.map(l => `<div>${l.who ? `<b>${esc(l.who)}：</b>` : ''}${esc(l.text)}</div>`).join('')]);
  return rows.length ? `<dl class="qc-ans">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>` : '';
}
/* 一整道「带答案」的题（v 的结构同 itemtypes.view） */
function qView(v, num) {
  let h = `<div class="qc">${qStem(v, num)}`;
  if (v.options && v.options.length) h += `<div class="qc-opts">${v.options.map((o, i) => qOpt(o.text != null ? o.text : o, i, 'div')).join('')}</div>`;
  const mineOpt = (v.options || []).some(o => o.mine);
  h += qAns({answer: v.answer, mine: mineOpt ? '' : v.mine, explain: v.explain, points: v.points, transcript: v.transcript}) + '</div>';
  const wrap = document.createElement('div'); wrap.innerHTML = h;
  qMark(wrap, (v.options || []).findIndex(o => o.right), (v.options || []).findIndex(o => o.mine) >= 0 ? (v.options || []).findIndex(o => o.mine) : null);
  return wrap.innerHTML;
}

/* 题目组件：渲染一道题（不带答案），作答后在同一张卡上显示带答案的版本，再调用 onDone(result) */
function renderItem(box, item, opts) {
  opts = opts || {};
  // 输入控件由服务端题型注册表给出（app/itemtypes.py 的 widget）：choice 选项 / self 自评 / text 输入框
  const W = item.widget || (item.type === 'mcq' ? 'choice' : item.type === 'short' ? 'self' : 'text');
  let html = `<div class="q qc">` + qStem(item);
  if (W === 'choice') {
    html += `<div class="qc-opts">${item.options.map((o, i) => qOpt(o, i)).join('')}</div>`;
  } else if (W === 'discuss') {  // 思辨题：没有标准答案，AI 陪着想（app/discuss.py）
    html += (item.twist ? `<div class="hint dsc-twist">🔄 这次换个条件：${esc(item.twist)}</div>` : '') +
      `<div class="muted small dsc-tip">这道题没有标准答案。写下你的判断和理由，也可以反问。</div><textarea class="ans" placeholder="我会怎么判断、怎么做，为什么……"></textarea>`;
  } else if (W === 'self') {
    html += `<textarea class="ans" placeholder="${item.coach ? '写下你的判断和理由，AI 会按要点点评，再追问你一两句' : '先自己写一写（写关键词也行）'}"></textarea>`;
  } else {
    html += `<div class="row" style="margin-top:8px"><input type="text" class="ans grow" placeholder="你的答案" autocomplete="off">` +
      (item.unit ? `<span class="muted">${esc(item.unit)}</span>` : '') + `</div>`;
  }
  html += `<div class="qacts">` +
    `<button class="btn submit">${W === 'discuss' ? '说说我的想法' : W === 'self' ? (item.coach ? '提交，看点评' : '看参考答案') : '提交'}</button>` +
    (item.hint ? `<button class="btn soft hintbtn">💡 提示</button>` : '') +
    (opts.dontKnow && W !== 'discuss' ? `<button class="linkbtn dkbtn">🤔 ${esc(opts.dkLabel || '这道题还不会')}</button>` : '') + `</div>` +
    `<div class="hintbox"></div><div class="fbbox"></div></div>`;
  box.innerHTML = html;
  box.classList.add('askable'); box.dataset.askItem = item.item_id || item.id || ''; delete box.dataset.answered;
  // 孩子做自动判分的题（选择、填空）：选择题点一下就交；答对了「下一题」自己翻过去（nextUp）。开放题、思辨题照旧慢慢来
  const quick = Juice.who() !== 'adult' && (W === 'choice' || W === 'text') && !opts.confirm;
  if (quick) box.dataset.quick = '1'; else delete box.dataset.quick;
  if (quick && W === 'choice') $('.submit', box).hidden = true;
  let chosen = null;
  const _done = opts.onDone;
  opts.onDone = res => {
    box.dataset.answered = '1';
    $$('.qc-audio', box).forEach(a => { $('.qa-left', a).textContent = '答完了，录音可以随便听'; $('.qa-play', a).disabled = false; });
    if (W === 'choice') qMark(box, res.answer_index, res.dont_know ? null : chosen);
    flagRow(box, item); _done && _done(res);
  };
  const t0 = Date.now(), a0 = Away.now();  // 做题用时：系统用它发现「会做但很慢」；切到别的页面的时间不算
  const took = () => Date.now() - t0 - (Away.now() - a0);
  $$('.opt', box).forEach(b => b.onclick = () => {
    if (box.dataset.answered || box.dataset.pending || b.disabled) return;
    $$('.opt', box).forEach(x => x.classList.remove('sel')); b.classList.add('sel'); chosen = b.dataset.i;
    if (quick) $('.submit', box).click();
  });
  const hb = $('.hintbtn', box);
  let hinted = false;
  if (hb) hb.onclick = () => { hinted = true; $('.hintbox', box).innerHTML = `<div class="hint" style="margin-top:8px">💡 ${esc(item.hint)}</div>`; hb.remove(); };
  const dk = $('.dkbtn', box);
  if (dk) dk.onclick = async () => {
    // 「还不会」分三级（opts.ladder，docs/DESIGN.md 5.46）：先点一下路子 → 还不会就一步一步一起做这道 → 趁热试一道一样的
    const nudge = opts.ladder && !hinted && (item.hint || item.nudge);
    if (nudge) {
      hinted = true; if (hb) hb.remove();
      $('.hintbox', box).innerHTML = `<div class="hint help1" style="margin-top:8px">💡 ${isSimple() ? '先给你一点提示' : '先给你一点提示，再想一想'}：${esc(nudge)}</div>`;
      dk.textContent = '🤔 还是不会';
      return;
    }
    dk.disabled = true;
    try {
      const res = await opts.dontKnow();
      if (!res) return;  // 返回空：页面自己接着走（小测直接下一题），这里不再显示讲解
      $$('.opt', box).forEach(b => b.disabled = true);
      $$('.submit,.hintbtn,.dkbtn', box).forEach(b => b.remove());
      Combo.hit(false, null, res);  // 「还不会」不算错：连对悄悄归零，不出声
      if (opts.ladder) return walkThrough(box, item, res, opts);
      $('.fbbox', box).innerHTML = feedback(res, {choice: W === 'choice'});
      opts.onDone && opts.onDone(res);
    } catch (e) { dk.disabled = false; $('.fbbox', box).innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  const ans = $('.ans', box);
  if (ans && ans.tagName === 'INPUT') ans.addEventListener('keydown', e => { if (e.key === 'Enter') $('.submit', box).click(); });
  $('.submit', box).onclick = async () => {
    const btn = $('.submit', box);
    const answer = W === 'choice' ? chosen : (ans ? ans.value : '');
    if (W === 'choice' && chosen === null) { alert('先选一个答案'); return; }
    // 思辨题、AI 点评的开放题要等 AI 想十几秒：按下就锁住、显示它在做什么，写出来一段就先看一段（aiWait）
    const wait = W === 'discuss' || (W === 'self' && item.coach) ? aiWait(btn, $('.fbbox', box), W === 'discuss' ? 'discuss' : 'review', ans) : null;
    if (!wait) btn.disabled = true;
    if (quick) { Juice.press(); box.dataset.pending = '1'; }  // 按下就有回应，不等服务器判完；判完之前不能改选
    try {
      const res = await opts.submit(answer, undefined, took(), W === 'discuss' && item.rethink ? {rethink: item.rethink} : undefined, wait && wait.part);
      if (wait) wait.end();
      if (res.discuss) { btn.remove(); ans.readOnly = true; discussRound(box, res, opts, took); return; }
      if (res.coach) { btn.remove(); if (dk) dk.remove(); ans.readOnly = true; coachRound(box, res, opts, took); return; }
      if (res.reveal) {
        $('.fbbox', box).innerHTML = `<div class="fb dk">` + qAns({answer: res.answer, points: res.points}).replace('<dt>答案</dt>', '<dt>参考答案</dt>') +
          `<div class="row" style="margin-top:10px"><span>${isSimple() ? '你答到了吗？' : '对照要点，你答到了吗？'}</span><button class="btn sm selfok">基本答到</button><button class="btn ghost sm selfno">还差一些</button></div></div>`;
        btn.remove();
        const go = async v => {
          const r2 = await opts.submit(answer, v, took());
          $('.selfok', box).closest('.row').remove();
          $('.fbbox', box).insertAdjacentHTML('beforeend', feedback({...r2, answer: '', points: []}, {}));
          opts.onDone && opts.onDone(r2);
        };
        $('.selfok', box).onclick = () => go('ok'); $('.selfno', box).onclick = () => go('no');
        return;
      }
      if (W === 'choice') $$('.opt', box).forEach(b => { if (b.dataset.i === chosen) b.classList.add(res.correct ? 'right' : 'wrong'); b.disabled = true; });
      $('.fbbox', box).innerHTML = feedback(res, {choice: W === 'choice', mine: W === 'choice' ? '' : answer});
      btn.remove(); if (dk) dk.remove();
      delete box.dataset.pending;
      Combo.hit(!!res.correct, W === 'choice' ? $(`.opt[data-i="${chosen}"]`, box) : $('.ans', box) || box, res);
      Focus.rt(!!res.correct, took());
      opts.onDone && opts.onDone(res);
    } catch (e) {
      if (wait) wait.fail(); else btn.disabled = false;
      delete box.dataset.pending;
      $('.fbbox', box).innerHTML = `<div class="err">${esc(e.message)}</div>`;
    }
  };
  Focus.onRender(box, item);
}
/* 「还不会」的第二、三级：解析拆成几小步，看懂一步点下一步，最后是答案；再趁热试一道一样的（/api/twin，
   同一个骨架换了数字和情境）。一道「一样的」在看讲解的工夫就去取了，点按钮时多半已经备好 */
function walkThrough(box, item, res, opts) {
  const kp = item.kp_id || '', steps = res.steps || [];
  const twinP = kp ? api(`/api/twin?item_id=${encodeURIComponent(item.item_id || item.id)}&kp_id=${encodeURIComponent(kp)}`).catch(() => ({})) : null;
  const fb = $('.fbbox', box);
  fb.innerHTML = `<div class="fb dk pop help2"><div class="fb-v">${isSimple() ? '📌 没关系，我们一起做一遍：' : '📌 没关系，我们一步一步把这道做出来：'}</div>` +
    `<ol class="help-steps"></ol><div class="help-ctl"></div></div>`;
  const ol = $('.help-steps', fb), ctl = $('.help-ctl', fb);
  let i = 0;
  const end = () => {
    ctl.innerHTML = qAns({answer: res.answer, points: res.points, transcript: res.transcript}) +
      `<div class="fb-note">${isSimple() ? '📒 记下了，明天换一道新题再来' : '已放进错题本：明天换一道新题来考你，不再做这道原题。'}</div>`;
    if (twinP) {
      const tb = document.createElement('div'); tb.className = 'help3';
      tb.innerHTML = `<button type="button" class="btn soft">🔁 ${isSimple() ? '试一道一样的' : '趁热试一道一样的'}</button>`;
      ctl.appendChild(tb);
      $('button', tb).onclick = async () => {
        $('button', tb).disabled = true;
        const r = await twinP;
        if (!r || !r.item) { tb.innerHTML = `<div class="small muted">这会儿没找到一样的题，明天会换一道新题来考你。</div>`; return; }
        tb.innerHTML = `<div class="small help3-h">🔁 一样的路子，换一道：</div><div class="help3-q"></div>`;
        renderItem($('.help3-q', tb), r.item, {
          submit: (answer, self, ms) => api('/api/answer', {item_id: r.item.id, kp_id: r.item.kp_id || kp, mode: 'helped', answer, self, ms}),
          onDone: r2 => {
            if (r2.reveal) return;
            $('.help3-q', tb).insertAdjacentHTML('beforeend', `<div class="small" style="margin-top:6px">${r2.correct ? '👍 这下有路了！明天换一道新题，看看还记不记得。' : '没关系，今天先到这儿，明天换一道题再来。'}</div>`);
          }
        });
      };
    }
    opts.onDone && opts.onDone(res);
  };
  const next = () => {
    ol.insertAdjacentHTML('beforeend', `<li>${esc(steps[i]).replace(/\n/g, '<br>')}</li>`); i++;
    if (i >= steps.length) return end();
    ctl.innerHTML = `<button type="button" class="btn sm">看懂了，下一步 →</button>`;
    $('.btn', ctl).onclick = next;
  };
  if (steps.length) next(); else end();
}
/* 开放题的一轮 AI 点评（app/coach.py）。追问没结束时只说想到了哪些、还差几点，不给答案；
   学员回答追问，再点评一轮；结束时逐条对照要点，再给参考思路 */
function coachRound(box, res, opts, took) {
  const hit = res.checks.filter(c => c.hit), miss = res.checks.length - hit.length;
  const icon = {good: '✅', partial: '🟡', miss: '🔸'}[res.verdict] || '🟡';
  const li = c => `<li class="${c.hit ? 'hit' : 'miss'}">${c.hit ? '✓' : '○'} ${esc(c.text)}${c.note ? `<div class="muted small">${esc(c.note)}</div>` : ''}</li>`;
  let h = `<div class="fb ${res.done ? (res.verdict === 'good' ? 'ok' : 'no') : 'dk'} pop"><div class="fb-v">${icon} ${esc(res.verdict_label)}</div>` +
    (res.feedback ? `<div style="margin-top:8px">${esc(res.feedback)}</div>` : '');
  if (res.done) {
    h += (res.checks.length ? `<ul class="coach-checks">${res.checks.map(li).join('')}</ul>` : '') +
      qAns({answer: res.answer}).replace('<dt>答案</dt>', '<dt>参考思路</dt>') +
      (res.status_label ? `<div class="fb-note">${esc(res.status_label)}</div>` : '') + '</div>';
  } else {
    h += (hit.length ? `<ul class="coach-checks">${hit.map(li).join('')}</ul>` : '') +
      (miss ? `<div class="fb-note">还有 ${miss} 个要点没想到，先别看答案，再想一步。</div>` : '') +
      `<div class="coach-fu"><div class="fb-v">🤔 追问：${esc(res.followup)}</div>` +
      `<textarea class="ans" placeholder="接着写你的想法"></textarea><div class="qacts"><button class="btn sm fubtn">回答追问</button></div></div></div>`;
  }
  $('.fbbox', box).insertAdjacentHTML('beforeend', h);
  if (res.done) { if (res.correct) cheer(box); opts.onDone && opts.onDone(res); return; }
  const fb = $('.fbbox', box).lastElementChild, fu = $('.fubtn', fb), ta = $('textarea', fb);
  ta.focus();
  fu.onclick = async () => {
    if (ta.value.trim().length < 4) { alert('先写下你的想法'); return; }
    const wait = aiWait(fu, fb, 'review', ta);
    try {
      const r = await opts.submit(ta.value, undefined, took(), {review_id: res.review_id}, wait.part);
      wait.end(); fu.closest('.qacts').remove();
      coachRound(box, r, opts, took);
    } catch (e) { wait.fail(); fb.insertAdjacentHTML('beforeend', `<div class="err">${esc(e.message)}</div>`); }
  };
}
/* 思辨题的一轮（app/discuss.py）：AI 先把你的观点说得更有力、回答你的反问，再提一个挑战；
   你可以接着说，也可以随时写下结论。收尾时对照几种看法和各自成立的条件，记下这次用到的思考方式。不判对错 */
function discussRound(box, res, opts, took) {
  const fb = $('.fbbox', box);
  if (res.care) {  // 学员写的话里有危险信号：先关心人，这道题停下（app/safety.py）
    fb.insertAdjacentHTML('beforeend', `<div class="fb dk pop dsc-care"><div class="fb-v">💛 ${esc(res.care.title)}</div><div>${esc(res.care.text)}</div>` +
      `<ul>${(res.care.lines || []).map(l => `<li><b>${esc(l)}</b></li>`).join('')}</ul></div>`);
    return;
  }
  const actLabel = res.action_label || '接下来我会做的一件事';
  const moveChip = m => `<span class="chip dsc-move" title="${esc(m.note || '')}">${esc(m.name)}${m.how ? (m.how === 'self' ? ' · 自己想到' : ' · 追问后想到') : ''}</span>`;
  if (res.done) {
    const views = (res.perspectives || []).map(p => `<li class="${p.near ? 'near' : ''}">${p.near ? '<span class="dsc-near">你的结论接近这一种</span>' : ''}` +
      `<div><b>${esc(p.view)}</b></div><div class="small">成立的条件：${esc(p.holds_when || '')}</div>` +
      (p.risk ? `<div class="small muted">用错了会怎样：${esc(p.risk)}</div>` : '') + '</li>').join('');
    fb.insertAdjacentHTML('beforeend', `<div class="fb dk pop dsc-end"><div class="fb-v">🧭 你的结论</div>` +
      `<div class="dsc-mine">${esc(res.conclusion)}${res.action ? `<div class="small">${esc(actLabel)}：${esc(res.action)}</div>` : ''}</div>` +
      (res.shift ? `<div style="margin-top:10px">${esc(res.shift)}</div>` : '') +
      (res.own ? `<div style="margin-top:8px">✨ 你自己的东西：${esc(res.own)}</div>` : '') +
      (res.watch ? `<div style="margin-top:8px">⚠️ 值得留意：${esc(res.watch)}</div>` : '') +
      (views ? `<div class="fb-v" style="margin-top:14px">几种都有道理的看法</div><ul class="dsc-views">${views}</ul>` : '') +
      (res.moves && res.moves.length ? `<div class="dsc-moves">这次用到的思考方式：${res.moves.map(moveChip).join('')}</div>` : '') +
      (res.next ? `<div class="fb-note">📅 ${esc(res.next.due)} 换一个条件再问你：${esc(res.next.twist)}</div>` : '') + '</div>');
    opts.onDone && opts.onDone({...res, correct: true});  // 想完就算完成这一题（计数用），不是「答对」
    return;
  }
  let h = '';
  if (res.ai) {
    h = `<div class="fb dk pop dsc-turn">` + (res.steelman ? `<div>💡 ${esc(res.steelman)}</div>` : '') +
      (res.answer ? `<div style="margin-top:8px"><b>你问的：</b>${esc(res.answer)}</div>` : '') +
      (res.moves_now && res.moves_now.length ? `<div class="dsc-moves">你用到了：${res.moves_now.map(moveChip).join('')}</div>` : '') +
      (res.challenge ? `<div class="fb-v" style="margin-top:10px">🤔 ${esc(res.challenge)}</div>` : '') + '</div>';
  } else {
    h = `<div class="fb dk dsc-turn"><div class="small">写下你现在的结论，再看看几种都有道理的看法。</div></div>`;
  }
  h += `<div class="dsc-ctl">` + (res.can_continue ? `<textarea class="ans dsc-more" placeholder="接着说：同意、反驳、补充，或者反问"></textarea>` +
    `<div class="qacts"><button class="btn sm dsc-go">接着说</button><button class="btn ghost sm dsc-end-btn">我想好了，写结论</button></div>` : '') +
    `<div class="dsc-close" ${res.can_continue ? 'hidden' : ''}><textarea class="ans dsc-c" placeholder="我现在的结论是……"></textarea>` +
    `<input type="text" class="ans dsc-a" placeholder="${esc(actLabel)}（可以不写）"><div class="qacts"><button class="btn sm dsc-done">写好了</button></div></div></div>`;
  fb.insertAdjacentHTML('beforeend', h);
  const ctl = fb.lastElementChild;
  const err = e => ctl.insertAdjacentHTML('beforeend', `<div class="err">${esc(e.message)}</div>`);
  const go = $('.dsc-go', ctl), endb = $('.dsc-end-btn', ctl), close = $('.dsc-close', ctl);
  if (go) go.onclick = async () => {
    const ta = $('.dsc-more', ctl);
    if (ta.value.trim().length < 4) { alert('先写下你的想法'); return; }
    endb.disabled = true;
    const wait = aiWait(go, ctl, 'discuss', ta);
    try {
      const r = await opts.submit(ta.value, undefined, took(), {discussion_id: res.discussion_id}, wait.part);
      wait.end(); go.closest('.qacts').remove(); close.remove();
      discussRound(box, r, opts, took);
    } catch (e) { wait.fail(); endb.disabled = false; err(e); }
  };
  if (endb) endb.onclick = () => { close.hidden = false; endb.closest('.qacts').remove(); const ta = $('.dsc-more', ctl); if (ta) ta.remove(); $('.dsc-c', ctl).focus(); };
  $('.dsc-done', ctl).onclick = async () => {
    const c = $('.dsc-c', ctl).value.trim();
    if (c.length < 4) { alert('写下你现在的结论'); return; }
    const wait = aiWait($('.dsc-done', ctl), ctl, 'discuss_close', $('.dsc-c', ctl), $('.dsc-a', ctl));
    try {
      const r = await opts.submit('', undefined, took(), {discussion_id: res.discussion_id, conclusion: c, action: $('.dsc-a', ctl).value.trim()}, wait.part);
      wait.end(); ctl.remove();
      discussRound(box, r, opts, took);
    } catch (e) { wait.fail(); err(e); }
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
/* 孩子端少字（body[data-simple]，见 docs/KID_UX.md）：反馈、弹窗只留一句话 */
const isSimple = () => !!(document.body && document.body.dataset.simple);
/* 判完以后的反馈：先一句结论，再是带答案的几栏（同 qAns）。o.choice：选择题的答案已经标在选项上，不再重复；o.mine：自己写的答案 */
function feedback(res, o) {
  o = o || {};
  if (res.dont_know) {
    return `<div class="fb dk pop"><div class="fb-v">${isSimple() ? '📌 没关系，先看懂它：' : '📌 没关系，知道自己哪里不会就是进步。先看懂它：'}</div>` +
      qAns({answer: res.answer, explain: res.explain, points: res.points, transcript: res.transcript}) +
      `<div class="fb-note">${isSimple() ? '📒 记下了，过几天再练' : '已放进错题本，过几天再练一次就会了。'}</div></div>`;
  }
  if (res.correct === undefined) return '';
  const wrongAns = res.correct ? '' : res.answer;
  return `<div class="fb ${res.correct ? 'ok' : 'no'} pop"><div class="fb-v">${res.correct ? PRAISE[Math.floor(Math.random() * PRAISE.length)] : '❌ 差一点！'}</div>` +
    qAns({answer: (o.choice && res.answer_index != null) ? '' : wrongAns, mine: res.correct ? '' : o.mine, explain: res.explain, points: res.correct ? [] : res.points, transcript: res.transcript}) +
    (res.correct || res.redo ? '' : `<div class="fb-note">${isSimple() ? '📒 记下了，过几天再练' : '已放进错题本，过几天会再出现。做错也算练过，继续！'}</div>`) + `</div>`;
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
/* 数字从旧的跳到新的（只在孩子做完事以后用；系统设置了减少动画就直接显示新数） */
function countUp(el, from, to, ms) {
  const fmt = x => Math.round(x).toLocaleString('en-US');
  if (from >= to || matchMedia('(prefers-reduced-motion: reduce)').matches) { el.textContent = fmt(to); return; }
  const t0 = performance.now(), d = ms || 900;
  const step = t => { const k = Math.min(1, (t - t0) / d); el.textContent = fmt(from + (to - from) * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
  setTimeout(() => requestAnimationFrame(step), 350);
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
/* ---------- 一路做下去：做完一项，不用回首页，直接接下一项（见 docs/DESIGN.md 5.16） ----------
   做完一项：底部弹出一张小卡「✓ 完成 · 今天 4/8 · 下一项：…  [继续 →]」。刚做的题和讲解还留在页面上，
   做错了能先看清楚再走；学完一节可以先休息 3 分钟；全部做完给冲刺、乐园和一日记录的入口。 */
function _ls(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
const Flow = {
  done(st) {
    if (!st || !st.total) { location.href = '/today'; return; }
    if (Focus.gate(() => Flow.done(st))) return;  // 这口气也到点了：先收尾、休息，再弹下一项
    _ls('secs:' + st.day, st.secs_done);  // 今天页不再重复弹「第 n 节完成」
    $$('.flow-sheet,.flow-pill').forEach(x => x.remove());
    const nx = st.next, sec = st.section;
    const head = st.all_done ? '🎉 今天的任务全部完成！' : sec ? `🎉 第 ${sec.n} 节完成！` : st.just ? '✓ 完成一项 +1 ⭐' : '👉 接着做今天的任务';
    const sub = st.all_done ? (isSimple() ? '' : '坚持比做对更重要，今天你做到了。') : sec ? (sec.base ? (isSimple() ? '🛡️ 连续天数保住啦' : '第 1 节做完了，今天的连续天数保住啦。') : `又亮了一格，还剩 ${sec.left} 节。`) : '';
    const m = document.createElement('div'); m.className = 'flow-sheet'; m.setAttribute('role', 'dialog');
    m.innerHTML = `<div class="fs-card"><button type="button" class="fs-x" aria-label="先看看这页" title="先看看这页">✕</button>
      <div class="fs-h">${(st.all_done || sec) && window.AI && AI.cheer ? `<span class="fs-face" aria-hidden="true">${AI.cheer}</span>` : ''}<b>${head}</b>${sub ? `<span>${sub}</span>` : ''}</div>
      ${Flow.grow(st.grow)}
      <div class="fs-prog"><span>今天 ${st.done}/${st.total}</span><i><b style="width:${Math.round(100 * st.done / st.total)}%"></b></i></div>
      ${nx ? `<a class="fs-next" href="${esc(nx.url)}"><span class="fs-k">下一项 · 第 ${nx.n} 项${nx.minutes ? ' · 约 ' + nx.minutes + ' 分钟' : ''}</span><b>${esc(nx.title)}</b>${nx.why && !isSimple() ? `<span class="fs-why">${esc(nx.why)}</span>` : ''}<span class="btn lg block fs-go">继续 →</span></a>`
        : `<div class="fs-end"><a class="btn lg" href="/sprint">⚡ 还想做？来冲刺</a><a class="btn soft lg" href="/arena">🎮 去乐园</a></div>`}
      <div class="fs-alt">${sec && nx ? '<button type="button" class="linkbtn fs-rest">☕ 休息 3 分钟</button>' : ''}<a class="linkbtn" href="/today${st.all_done ? '#daylog' : ''}">${st.all_done ? '📝 写一句一日记录' : '回到今天'}</a></div></div>`;
    document.body.appendChild(m);
    const go = $('.fs-next', m); if (go) setTimeout(() => go.focus(), 50);
    const mile = st.grow && st.grow.milestones.length;
    if (st.just || sec || st.all_done) Juice.done();   // 一项做完：过关的和弦 + 长一点的震动
    if (st.all_done || sec || mile) confetti(); else if (st.just) cheer($('.fs-card', m));
    const tn = $('.fs-total', m); if (tn) countUp(tn, +tn.dataset.from, +tn.dataset.to);
    $('.fs-x', m).onclick = () => { m.remove(); if (nx) Flow.pill(nx); };
    const rb = $('.fs-rest', m); if (rb) rb.onclick = () => restTimer($('.fs-card', m), () => { m.remove(); Flow.done({...st, section: null}); });
    if (st.just) Flow.strip(st);
  },
  /* 积少成多（app/growth.py）：这一项答了几道 → 今天 → 一共；第一次学会的知识点带名字和「第几个」；刚跨过的里程碑 */
  grow(g) {
    if (!g || !g.total) return '';
    const n = x => x.toLocaleString('en-US');
    const bits = (g.item ? [`这一项 ${g.item} 道`] : []).concat(g.today ? [`今天 ${n(g.today)} 道`] : [], [
      `一共 <b class="fs-total" data-from="${g.total - g.item}" data-to="${g.total}">${n(g.total - g.item)}</b> 道`]);
    return `<div class="fs-grow">${bits.join(' · ')}</div>` +
      (g.learned.length === 1 ? `<a class="fs-learned" href="/learn/${esc(g.learned[0].id)}">🌟 学会了：<b>${esc(g.learned[0].name)}</b>（第 ${g.learned[0].n} 个）</a>`
        : g.learned.length ? `<div class="fs-learned">🌟 学会了 ${g.learned.length} 个：<b>${g.learned.map(k => esc(k.name)).join('、')}</b>（第 ${g.learned[0].n}～${g.learned[g.learned.length - 1].n} 个）</div>` : '') +
      g.milestones.map(x => `<div class="fs-mile">🏅 ${esc(x.text)}</div>`).join('');
  },
  /* 收起来以后：右下角留一个「下一项 →」 */
  pill(nx) {
    const a = document.createElement('a'); a.className = 'flow-pill'; a.href = nx.url; a.innerHTML = `下一项：<b>${esc(nx.title)}</b> →`;
    document.body.appendChild(a);
  },
  /* 页面顶上的「今天 3/8」条：这一项标成做完，按钮变成直接去下一项 */
  strip(st) {
    const b = $('.flowbar'); if (!b) return;
    b.classList.add('done');
    const n = $('.flb-prog b', b); if (n) n.textContent = `${st.done}/${st.total}`;
    const t = $('.flb-t', b); if (t && !t.textContent.startsWith('✓')) t.textContent = '✓ 做完了：' + t.textContent.replace(/^第 \d+ 项：/, '');
    const g = $('.flb-go', b);
    if (g && st.next) { g.href = st.next.url; g.textContent = '下一项 ›'; } else if (g) g.remove();
  },
};
/* 做完一项任务：+1 ⭐，接着做下一项 */
async function finishTask(type, extra) {
  let r = {};
  try { r = await api('/api/plan/task-done', Object.assign({type}, extra || {})); } catch (e) {}
  if (r.flow && r.flow.total) Flow.done(r.flow); else location.href = '/today';
}
/* 课间休息 3 分钟（学完一节时） */
function restTimer(card, done) {
  const tips = ['站起来伸个懒腰 🙆', '看看窗外最远的地方 🌳', '喝几口水 💧', '转转脖子和肩膀 🔄', '闭上眼睛深呼吸 3 次 😌'];
  let left = 180, i = 0;
  card.innerHTML = `<div class="big">☕</div><b>课间休息</b><div class="rest-t">3:00</div><p class="rest-tip">${tips[0]}</p><button type="button" class="btn ghost block">休息好了，继续 →</button>`;
  card.classList.add('resting');
  const t = card.querySelector('.rest-t'), tip = card.querySelector('.rest-tip');
  const timer = setInterval(() => {
    left--; t.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
    if (left % 20 === 0) tip.textContent = tips[++i % tips.length];
    if (left <= 0) { clearInterval(timer); t.textContent = '时间到！'; toast('⏰ 休息好了，开始下一项吧'); }
  }, 1000);
  card.querySelector('button').onclick = () => { clearInterval(timer); done(); };
}
const Speak = {
  audio: null, n: 0, playing: false, slow: false, _end: null,
  langOf(t) { return /[一-鿿]/.test(t) && !/[A-Za-z]{3,}/.test(t) ? 'zh' : 'en'; },
  useOf(t) { return t.trim().split(/\s+/).length <= 2 && t.length <= 24 ? 'word' : 'sentence'; },
  server(lang) { return (window.TTS_LANGS || []).includes(lang); },
  stop() {
    Speak.n++; Speak.playing = false;
    if (Speak.audio) { Speak.audio.onended = Speak.audio.onerror = null; Speak.audio.pause(); }
    if (window.speechSynthesis) speechSynthesis.cancel();
    if (Speak._end) { const f = Speak._end; Speak._end = null; f(false); }
  },
  /* 读一段文字，读完返回 true；被打断（又点了别的）返回 false。role：听力对话里的角色（F 女 / M 男 / C 孩子 / N 旁白） */
  async play(text, lang, use, role) {
    text = String(text || '').trim(); if (!text) return false;
    lang = (lang || Speak.langOf(text)).slice(0, 2).toLowerCase();
    use = use || Speak.useOf(text);
    Speak.stop();
    const my = Speak.n;
    let url = '';
    if (Speak.server(lang)) { try { url = (await api('/api/tts', {text, lang, use, role: role || ''})).url || ''; } catch (e) {} }
    if (my !== Speak.n) return false;
    if (url) { try { return await Speak.file(url, my); } catch (e) { if (my !== Speak.n) return false; } }
    return Speak.browser(text, lang, use, my, role);
  },
  /* 先登记好下一段（只拿地址，不播放），连续朗读时用来减少等待 */
  async prepare(text, lang, use) {
    lang = (lang || Speak.langOf(text)).slice(0, 2).toLowerCase();
    if (!Speak.server(lang)) return '';
    try { const r = await api('/api/tts', {text, lang, use: use || 'passage'}); if (r.url) { const a = new Audio(); a.preload = 'auto'; a.src = r.url; } return r.url || ''; } catch (e) { return ''; }
  },
  file(url, my) {
    return new Promise((res, rej) => {
      const a = Speak.audio || (Speak.audio = new Audio());
      a.src = url; a.defaultPlaybackRate = a.playbackRate = Speak.slow ? .85 : 1;
      if ('preservesPitch' in a) a.preservesPitch = true;
      Speak._end = res; Speak.playing = true;
      a.onended = () => { Speak.playing = false; Speak._end = null; res(true); };
      a.onerror = () => { Speak.playing = false; Speak._end = null; rej(new Error('audio')); };
      a.play().catch(e => { if (my === Speak.n) { Speak.playing = false; Speak._end = null; rej(e); } });
    });
  },
  browser(text, lang, use, my, role) {
    if (!window.speechSynthesis) return Promise.resolve(false);
    return new Promise(res => {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = lang === 'zh' ? 'zh-CN' : 'en-US';
      const v = Speak.voice(lang); if (v) u.voice = v;
      u.pitch = ({M: .75, F: 1.15, C: 1.4})[role] || 1;  // 浏览器自带的声音分不出男女，用音高区分对话里的人
      u.rate = ({word: .7, sentence: .8, passage: .85})[use] * (Speak.slow ? .85 : 1);
      Speak._end = res; Speak.playing = true;
      u.onend = u.onerror = () => { Speak.playing = false; if (Speak._end === res) Speak._end = null; res(my === Speak.n); };
      speechSynthesis.speak(u);
    });
  },
  /* 只设 lang 时，有的浏览器（中文系统）会用默认的中文声音念英语，所以明确挑一个这门语言的声音，优先本机的 */
  voice(lang) {
    const want = lang === 'zh' ? /^zh[-_](CN|Hans)/i : /^en[-_]US/i, base = lang === 'zh' ? /^zh/i : /^en/i;
    const vs = speechSynthesis.getVoices();
    const pick = re => vs.find(v => re.test(v.lang) && v.localService) || vs.find(v => re.test(v.lang));
    return pick(want) || pick(base) || null;
  },
  setSlow(on) { Speak.slow = on; if (Speak.audio) Speak.audio.playbackRate = on ? .85 : 1; },
};
function say(text, lang, use) { return Speak.play(text, lang, use); }
/* 一个 🔊 按钮的 HTML：<button class="say" data-say="..." data-lang="en"> （全站统一用事件委托处理点击） */
function sayBtn(text, lang, cls) { return `<button type="button" class="btn ghost sm say ${cls || ''}" data-say="${esc(text)}" data-lang="${esc(lang || '')}" title="读一读">🔊</button>`; }
document.addEventListener('click', e => {
  const b = e.target.closest('[data-say]'); if (!b) return;
  e.preventDefault(); e.stopPropagation();
  b.classList.add('saying'); Speak.play(b.dataset.say, b.dataset.lang || '', b.dataset.use || '').finally(() => b.classList.remove('saying'));
}, true);

/* 连续朗读一组段落（阅读页、读书、听书）：逐段（长段按句子切成小块）读，正在读的段落高亮，提前准备下一块。
   Listen.mount(段落元素数组, {lang, bar: 放播放条的元素, onProgress(i), onDone()}) */
const Listen = {
  els: [], lang: 'en', i: 0, on: false, opt: {},
  chunks(t, max) {
    t = t.replace(/\s+/g, ' ').trim(); if (t.length <= max) return t ? [t] : [];
    const out = []; let cur = '';
    for (const s of t.split(/(?<=[.!?。！？；;…]["'”’」』）)]?)\s*/)) {
      if (cur && (cur + s).length > max) { out.push(cur.trim()); cur = ''; }
      cur += (cur && /^[A-Za-z]/.test(s) ? ' ' : '') + s;
      while (cur.length > max) { out.push(cur.slice(0, max)); cur = cur.slice(max); }
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  },
  mount(els, opt) {
    Listen.els = els; Listen.opt = opt || {}; Listen.lang = Listen.opt.lang || 'en'; Listen.i = Listen.opt.start || 0;
    const bar = document.createElement('div'); bar.className = 'listenbar';
    bar.innerHTML = `<button class="btn sm" data-l="play">▶ 听</button><button class="btn ghost sm" data-l="prev" title="上一段">⏮</button>` +
      `<button class="btn ghost sm" data-l="next" title="下一段">⏭</button><button class="btn ghost sm" data-l="slow" title="再慢一点">🐢</button>` +
      `<span class="muted small" data-l="pos"></span>`;
    (Listen.opt.bar || document.body).appendChild(bar); Listen.bar = bar;
    bar.onclick = e => {
      const b = e.target.closest('[data-l]'); if (!b) return;
      const a = b.dataset.l;
      if (a === 'play') Listen.on ? Listen.pause() : Listen.play();
      else if (a === 'prev') Listen.jump(Listen.i - 1);
      else if (a === 'next') Listen.jump(Listen.i + 1);
      else if (a === 'slow') { Speak.setSlow(!Speak.slow); b.classList.toggle('on', Speak.slow); toast(Speak.slow ? '🐢 再慢一点' : '正常语速'); }
    };
    els.forEach(el => el.classList.add('lp'));
    Listen.show();
  },
  show() {
    if (!Listen.bar) return;
    $('[data-l=play]', Listen.bar).textContent = Listen.on ? '⏸ 暂停' : (Listen.i > 0 ? '▶ 接着听' : '▶ 听');
    $('[data-l=pos]', Listen.bar).textContent = `${Math.min(Listen.i + 1, Listen.els.length)} / ${Listen.els.length} 段`;
    Listen.els.forEach((el, i) => el.classList.toggle('reading', Listen.on && i === Listen.i));
  },
  pause() { Listen.on = false; Speak.stop(); Listen.show(); },
  jump(i) { Speak.stop(); Listen.i = Math.max(0, Math.min(Listen.els.length - 1, i)); Listen.on = false; Listen.play(); },
  async play() {
    if (Listen.i >= Listen.els.length) Listen.i = 0;
    Listen.on = true; Listen.show();
    const run = ++Listen.run;
    while (Listen.on && run === Listen.run && Listen.i < Listen.els.length) {
      const el = Listen.els[Listen.i];
      el.scrollIntoView({block: 'center', behavior: 'smooth'});
      const parts = Listen.chunks(el.textContent, 400);
      const nextEl = Listen.els[Listen.i + 1];
      for (let k = 0; k < parts.length; k++) {
        const nxt = parts[k + 1] || (nextEl ? Listen.chunks(nextEl.textContent, 400)[0] : '');
        if (nxt) Speak.prepare(nxt, Listen.lang, 'passage');
        const ok = await Speak.play(parts[k], Listen.lang, 'passage');
        if (!ok || run !== Listen.run || !Listen.on) return;
      }
      if (Listen.opt.onProgress) Listen.opt.onProgress(Listen.i);
      Listen.i++; Listen.show();
    }
    if (Listen.i >= Listen.els.length) { Listen.on = false; Listen.show(); if (Listen.opt.onDone) Listen.opt.onDone(); }
  },
  run: 0,
};

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
    const sel = Ask.sel || (window.getSelection() || '').toString().trim();
    const h1 = $('main h1');
    let text = sel;
    if (!text && b) text = ($('.q > div', b) || b).innerText;
    if (!text) text = ($('main') || document.body).innerText;
    return {path: location.pathname, title: h1 ? h1.innerText : document.title, text: text.slice(0, 1500),
            item_id: b ? b.dataset.askItem : '', kp_id: (window.ASK_CTX || {}).kp || '', answered: !!(b && b.dataset.answered)};
  },
  label(c) { return c.item_id ? '这道题：' + c.text.replace(/\s+/g, ' ').slice(0, 40) : (Ask.sel ? '选中的：' + c.text.slice(0, 40) : '这个页面：' + c.title); },
  sel: '',  // 划词菜单「问…」带过来的选中文字：焦点一进输入框选区就没了，所以先记下
  open(sel) {
    Ask.sel = typeof sel === 'string' ? sel : '';
    $('#askpanel').classList.add('on');
    const c = Ask.ctx(), key = c.item_id || c.path + (Ask.sel ? '#' + Ask.sel : '');
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
  $('#askbtn').onclick = () => Ask.open();
  $('#askclose').onclick = () => $('#askpanel').classList.remove('on');
  $('#asknew').onclick = () => { Ask.key = ''; Ask.open(Ask.sel); };
  $('#askform').onsubmit = e => { e.preventDefault(); Ask.send(); };
  $$('#askchips button').forEach(b => b.onclick = () => Ask.send(b.textContent));
});

/* ---------- 激励：每答一题的即时反馈，留给做完一项（DESIGN 5.21、5.52） ----------
   答题卡（renderItem）答完一题就调 Combo.hit，页面不用管：声音和震动在 Juice（juice.js），这里只记连对。
   题目是这一页的主角：答对「叮咚」，连对一题升一个音，选项自己变色，就这么多；不飞 +1、不弹连对胶囊、不冒星星，
   「这一项 N 道」的数悄悄加一。难题答对在题下面说一句。真正的庆祝留给一项做完（Flow 的下一项卡）。
   答错一个柔的「嗒」，连对悄悄归零。成人声音默认关。 */
const Combo = {
  n: 0,
  hit(ok, el, res) {
    res = res || {};
    Combo.n = ok ? Combo.n + 1 : 0;
    if (res.dont_know) return;
    Juice.answer(ok, {el, streak: Combo.n, hard: res.hard});
    const who = Juice.who(), fb = el && el.closest && $('.fbbox', el.closest('.q') || document);
    if (ok && res.hard && who !== 'adult' && fb)
      fb.insertAdjacentHTML('afterbegin', `<div class="small hardnote">${who === 'teen' ? '💪 这道题不简单，做对了' : '💪 这题很多人会错，你做对了'}</div>`);
  },
};
/* 「下一题」：自动判分的题答对了，0.8 秒后自己翻过去（按钮上走一道进度条）；答错停下来看讲解。回车 = 点它。
   这期间在页面上点了别的（看解析、选字、点提示），就不自动翻了。opts.wait 改等多久（有话要孩子看完时） */
const AUTO_NEXT_MS = 800;
function nextUp(box, label, go, res, opts) {
  opts = opts || {};
  const nx = document.createElement('div'); nx.className = 'row nextrow'; nx.style.marginTop = '10px';
  nx.innerHTML = `<button class="btn nextbtn">${label}</button>` + (opts.extra || '');
  box.appendChild(nx);
  const btn = $('.nextbtn', nx);
  let timer = 0;
  const stop = e => {
    if (e && e.target.closest && e.target.closest('.nextbtn')) return;
    clearTimeout(timer); btn.classList.remove('auto'); removeEventListener('pointerdown', stop, true);
  };
  btn.onclick = () => { stop(); if (btn.disabled) return; btn.disabled = true; go(); };
  const card = box.matches('[data-quick]') ? box : $('[data-quick]', box);
  if (res && res.correct && !res.dont_know && card) {
    const ms = opts.wait || AUTO_NEXT_MS;
    btn.style.setProperty('--auto', ms + 'ms'); btn.classList.add('auto');
    timer = setTimeout(() => { if (btn.isConnected && !document.hidden) btn.click(); else stop(); }, ms);
    addEventListener('pointerdown', stop, true);
  }
  return nx;
}
/* 电脑上手不离键盘：1–4 / A–D 选选项（页面上只有一道没答的题时），回车点「下一题」 */
document.addEventListener('keydown', e => {
  if (e.ctrlKey || e.metaKey || e.altKey || !e.target.closest || e.target.closest('input, textarea, select, [contenteditable]')) return;
  if (e.key === 'Enter') { const b = $$('.nextbtn').filter(x => !x.disabled && x.offsetParent).pop(); if (b) { e.preventDefault(); b.click(); } return; }
  const m = /^[1-8]$/.test(e.key) ? +e.key - 1 : 'abcdefgh'.indexOf(e.key.toLowerCase());
  if (m < 0 || e.key.length !== 1) return;
  const open = $$('[data-quick]').filter(x => !x.dataset.answered && $('.opt', x));
  if (open.length !== 1) return;
  const o = $(`.opt[data-i="${m}"]`, open[0]);
  if (o && !o.disabled) { e.preventDefault(); o.click(); }
});
/* 答题结果里带 lit：第一次掌握这个知识点 → 点亮 */
function celebrate(res, el) {
  if (res && res.lit) {
    toast(`🌟 点亮：${res.lit.name}`, 2400);
    confetti();
  } else if (res && res.probe && res.probe.inferred) {
    toast(`🗺️ 地图亮了 ${res.probe.inferred + 1} 块`, 2000);
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
      // 题目才是主角（DESIGN 5.21）：这里只是一条细线 + 一行灰字，不跟着滚，也不一秒一秒地跳
      // 📚 这一项答了几道（积少成多，悄悄加一）；🔊 全站一个声音开关（游戏里的也是它）
      el.innerHTML = `<div class="row small"><span class="rn" hidden>📚 这一项 <b>0</b> 道</span><span class="muted rpb"></span><span class="grow"></span><span class="rg muted"></span>` +
        `<button type="button" class="linkbtn rsnd" title="答题的声音和震动"></button></div>` +
        (opts.race === false ? '' : `<div class="race"><i class="ghost" title="上周的我"></i><i class="me" title="我"></i></div>`);
      if (opts.mount) opts.mount.insertAdjacentElement('beforebegin', el);
      else ($('main h1') || document.body).insertAdjacentElement('afterend', el);
    }
    Run.el = el;
    const snd = $('.rsnd', el), paint = () => { snd.textContent = Juice.sfx.on ? '🔊' : '🔇'; snd.setAttribute('aria-label', Juice.sfx.on ? '关掉声音' : '打开声音'); };
    snd.onclick = () => { Juice.sfx.toggle(); paint(); if (Juice.sfx.on) Juice.sfx.play('ding'); };
    paint();
    Focus.ensure({reading: kind === 'read'});
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
    Focus.hit(ok);
    Run.n++; if (ok) { Run.right++; Run.combo++; Run.best = Math.max(Run.best, Run.combo); } else Run.combo = 0;
    Run.draw();
  },
  draw() {
    const el = Run.el; if (!el) return;
    const rn = $('.rn', el); rn.hidden = !Run.n; $('b', rn).textContent = Run.n;
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
    } else { g.style.display = 'none'; $('.rg', el).textContent = Run.pb && Run.pb.runs ? '' : (isSimple() ? '' : '第一次：先立个纪录'); }
  },
  async finish(extra) {
    if (!Run.on || Run.saved) return null;
    Run.step(); Run.saved = true; Run.on = false;
    const body = Object.assign({kind: Run.kind, n_items: Run.n, n_right: Run.right, ms_active: Run.active,
                                ms_total: Date.now() - Run.t0, best_combo: Run.best}, extra || {});
    try {
      const r = await api('/api/run', body);
      (r.pbs || []).forEach((p, i) => setTimeout(() => { toast('🏅 新纪录！' + p.label, 2600); cheer(Run.el); }, 400 + i * 1200));
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
        sayBtn(r.word || q, lang) + `</h3>` +
        `<p style="margin:4px 0"><b>${esc(r.meaning || '')}</b>${r.simple_en ? `<br><span class="muted small">${esc(r.simple_en)}</span>` : ''}</p>` +
        (r.example ? `<p class="small">例：${esc(r.example)} ${sayBtn(r.example, 'en')}${r.example_zh ? `<br><span class="muted">${esc(r.example_zh)}</span>` : ''}</p>` : '') +
        (r.tip ? `<div class="hint">💡 ${esc(r.tip)}</div>` : '') + QuickLook.saved(r);
      QuickLook.bindFav(r.word || q, r.meaning || '');
    } catch (e) { $('#qlres').innerHTML = `<div class="err">${esc(e.message)}</div>` + `<button class="btn sm" id="qlfav">➕ 先加入复习</button>`; QuickLook.bindFav(q, ''); }
  },
  async translate(q) {
    q = (q || '').trim(); if (!q) return;
    QuickLook.panel(); $('#qlq').value = q.slice(0, 200);
    $('#qlres').innerHTML = '<span class="loading">正在翻译</span>';
    try {
      const r = await api('/api/translate', {text: q, item_id: QuickLook.item, page: location.pathname});
      $('#qlres').innerHTML = `<p class="small muted" style="margin:8px 0 2px">${esc(q.slice(0, 300))} ${sayBtn(q.slice(0, 600))}</p>` +
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
        else if (b.dataset.a === 'say') say(t);
        else if (b.dataset.a === 'ask' && $('#askbtn')) Ask.open(t);  // Ask 是顶层 const，不在 window 上
      };
    }
    const word = QuickLook.isWord(text);
    SelMenu.el.innerHTML = (word ? `<button data-a="look">🔍 查词</button>` : '') + `<button data-a="tr">🌐 翻译</button>` +
      `<button data-a="say">🔊 读一读</button><button data-a="add">➕ 加入复习</button>` + ($('#askbtn') ? `<button data-a="ask">${esc($('#askbtn').dataset.icon)} 问${esc($('#askbtn').dataset.name)}</button>` : '');
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

/* 页面切到后台（换标签页、切别的应用）累计了多久：做题用时要扣掉这段 */
const Away = {ms: 0, since: document.hidden ? Date.now() : 0};
Away.now = () => Away.ms + (Away.since ? Date.now() - Away.since : 0);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) Away.since = Date.now();
  else if (Away.since) { Away.ms += Date.now() - Away.since; Away.since = 0; }
});

/* ---------- 一口气（app/focus.py，DESIGN 5.36）：学习切成一段一段，长度按自己能撑住的时间一点点加长 ----------
   · 今天清单里的页面、做一组题的页面自动开始；换页面接着算（sessionStorage 记着这口气），去乐园、回今天页就停。
   · 离开（切走 / 2 分钟没动）每次记一笔；回来按离开多久分档：15 秒内不说，2 分钟内一句话，更久在题上方提醒先重读题。
   · 走神不让孩子自己记，从作答里自己看：反应时比自己平时忽快忽慢、答得太快又错、错了以后越点越快、离开。
     有点晃 → 下一题读出来；一直掉线、又做了一半以上 → 在下一道答对的题后提前收尾（算完成，档位不加）。
   · 到了目标时间，在一道题答完、一项任务做完时结束（读书时不打断，悄悄开始下一口气），弹结束卡：
     在线 / 掉线的一条线、一句只说好的话、下一口气多长；可以休息（小学跟着动一动），休息完叫名字回来。 */
const Focus = {
  b: null, on: false, starting: false, resting: false, due: false, early: false, ending: false, reading: false,
  active: 0, away: 0, run: 0, longest: 0, n: 0, right: 0, segs: [], score: 0, state: 'on',
  last: Date.now(), tick: Date.now(), hiddenAt: 0, idleAt: 0, wrongAt: 0, prevWrong: false, title: '',
  IDLE: 120000,
  adult() { return document.body.dataset.tone === 'adult'; },
  async ensure(opts) {
    if (!document.body.dataset.kid || /^\/(today|arena|diagnose)/.test(location.pathname)) return;
    if (opts && opts.reading) Focus.reading = true;
    if (Focus.on || Focus.starting) return;
    Focus.starting = true;
    let b = null;
    try { b = await api('/api/focus/start', {resume: Focus._saved()}); } catch (e) {}
    Focus.starting = false;
    if (!b || !b.id) return;
    Focus.adopt(b);
    if (!Focus.bound) Focus.bind();
  },
  _saved() { try { return sessionStorage.getItem('fz') || ''; } catch (e) { return ''; } },
  adopt(b) {
    Object.assign(Focus, {b, on: true, due: false, early: false, ending: false, active: b.active_ms || 0, away: b.away_ms || 0,
                          longest: b.longest_ms || 0, run: 0, n: b.n_items || 0, right: b.n_right || 0, segs: b.segs || [],
                          score: 0, state: 'on', last: Date.now(), tick: Date.now()});
    try { sessionStorage.setItem('fz', b.id); } catch (e) {}
    document.body.classList.add('fz-on');  // 有了一口气的进度条，做一组题的「⏱ 专注」计时就不重复显示
    Focus.mount(); if (Focus.el) delete Focus.el.dataset.k; Focus.draw();
  },
  bind() {
    Focus.bound = true;
    ['pointerdown', 'keydown', 'scroll', 'touchstart', 'wheel', 'input'].forEach(e => addEventListener(e, Focus.poke, {passive: true, capture: true}));
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        Focus.hiddenAt = Date.now(); Focus.title = document.title;
        if (Focus.on && !Focus.resting) document.title = Focus.adult() ? '⏸ 等你回来' : '⏸ 这口气在等你';
        Focus.sync(true);
      } else {
        if (Focus.title) { document.title = Focus.title; Focus.title = ''; }
        const ms = Focus.hiddenAt ? Date.now() - Focus.hiddenAt : 0; Focus.hiddenAt = 0;
        Focus.tick = Focus.last = Date.now();
        if (ms) Focus.back('away', ms);
      }
    });
    addEventListener('pagehide', () => Focus.sync(true));
    setInterval(Focus.step, 1000);
    setInterval(() => Focus.sync(false), 15000);
  },
  poke() {
    const now = Date.now();
    if (Focus.idleAt) { const ms = now - Focus.idleAt; Focus.idleAt = 0; Focus.back('idle', ms); }
    Focus.last = now;
  },
  step() {
    const now = Date.now(), dt = Math.min(now - Focus.tick, 5000); Focus.tick = now;
    if (!Focus.on || Focus.resting || Focus.ending || document.hidden) return;
    if (Speak.playing) Focus.last = now;  // 在听朗读也算在
    const idle = Focus.reading ? 180000 : Focus.IDLE;
    if (now - Focus.last >= idle) { if (!Focus.idleAt) Focus.idleAt = Focus.last; return; }
    Focus.active += dt; Focus.run += dt; Focus.longest = Math.max(Focus.longest, Focus.run);
    Focus.seg(dt, Focus.state !== 'off');
    if (!Focus.due && Focus.active >= Focus.b.target_ms) {
      Focus.due = true;
      if (Focus.reading) Focus.end('target');  // 读书时不打断：悄悄记下，接着下一口气
    }
    Focus.draw();
  },
  seg(ms, on) { const s = Focus.segs, l = s[s.length - 1]; if (l && !!l[1] === on) l[0] += ms; else s.push([ms, on ? 1 : 0]); },
  what() {
    if (Date.now() - Focus.wrongAt < 20000) return 'after_wrong';
    if ($('.seek')) return 'waiting';
    if (Focus.reading || $('.reader')) return 'reading';
    return 'item';
  },
  task() { const t = $('.flb-t'); return (t ? t.textContent.replace(/^(第 \d+ 项：|✓ 做完了：)/, '') : (($('main h1') || {}).textContent || document.title)).trim().slice(0, 60); },
  /* 离开后回来：记一笔，按离开多久分档提醒 */
  back(kind, ms) {
    if (!Focus.on || Focus.resting || ms < 3000) return;
    const body = JSON.stringify({block: Focus.b.id, ms, kind, what: Focus.what(), task: Focus.task()});
    if (navigator.sendBeacon) navigator.sendBeacon('/api/focus/leave', new Blob([body], {type: 'application/json'}));
    if (ms <= 15000) return;
    Focus.away += ms; Focus.run = 0; Focus.score += 1; Focus.judge();
    Focus.seg(Math.min(ms, 60000), false);
    const kid = !Focus.adult();
    if (ms <= 120000) { toast(kid ? '回来啦，接着做 👍' : '欢迎回来，接着这一题', 2000); return; }
    const q = $$('.q.qc').filter(x => !x.closest('[data-answered]')).pop();
    const m = Math.round(ms / 60000);
    const note = `<div class="hint fz-back">${kid ? `刚才停了 ${m} 分钟。先把题再读一遍，再答 📖` : `刚才离开了 ${m} 分钟，先把题目再读一遍。`}</div>`;
    if (q && !$('.fz-back', q)) { q.insertAdjacentHTML('afterbegin', note); q.scrollIntoView({block: 'center', behavior: 'smooth'}); }
    else toast(kid ? `刚才停了 ${m} 分钟，接着来` : `离开了 ${m} 分钟，接着来`, 2600);
  },
  /* 一道题答完（renderItem）：从用时和对错看在不在线；只跟自己平时比 */
  rt(ok, ms) {
    if (!Focus.on) return;
    if (!Run.on) { Focus.n++; if (ok) Focus.right++; }
    const bl = Focus.b.baseline; let pts = 0;
    if (ms > 0 && bl) { const z = (Math.log(ms) - bl.med) / bl.mad; if (Math.abs(z) > 2.5) pts += 0.5; }
    if (!ok && ms > 0 && ms < 2500) pts += 1;                                        // 快到读不完题又错了
    if (!ok && Focus.prevWrong && bl && ms > 0 && ms < 0.5 * Math.exp(bl.med)) pts += 1;  // 错了以后越点越快
    Focus.score = 0.6 * Focus.score + pts;
    Focus.prevWrong = !ok; if (!ok) Focus.wrongAt = Date.now();
    Focus.judge();
    Focus.boundary(ok);
  },
  hit(ok) { if (!Focus.on) return; Focus.n++; if (ok) Focus.right++; Focus.boundary(ok); },
  judge() {
    Focus.state = Focus.score >= 1.2 ? 'off' : Focus.score >= 0.6 ? 'wobble' : 'on';
    if (Focus.state === 'off' && Focus.active >= 0.5 * Focus.b.target_ms) Focus.early = true;
  },
  /* 新的一题出来：有点晃就把题读出来（换个方式把注意力拉回来），不说「你走神了」 */
  onRender(box, item) {
    if (!Focus.on || Focus.state === 'on' || !item || !item.q) return;
    box.classList.add('fz-nudge'); setTimeout(() => box.classList.remove('fz-nudge'), 2400);
    say(String(item.q).replace(/\$|\\\(|\\\)/g, '').slice(0, 300));
  },
  /* 一道题、一张卡做完：到点了就收尾；一直掉线就在答对的这题后提前收尾 */
  boundary(ok) {
    if (Focus.ending || Focus.reading) return;
    if (Focus.due) setTimeout(() => Focus.end('target'), 1400);
    else if (Focus.early && ok) setTimeout(() => Focus.end('early'), 1400);
  },
  /* 一项任务做完要弹「下一项」时：这口气也到点了就先收尾，休息完再弹 */
  gate(fn) {
    if (!Focus.on || !Focus.due || Focus.reading) return false;
    Focus.end('target', fn); return true;
  },
  payload(extra) {
    return Object.assign({id: Focus.b.id, active_ms: Focus.active, away_ms: Focus.away, longest_ms: Focus.longest,
                          n_items: Focus.n, n_right: Focus.right, segs: Focus.segs}, extra || {});
  },
  sync(leaving) {
    if (!Focus.on) return;
    const body = JSON.stringify(Focus.payload());
    if (leaving && navigator.sendBeacon) navigator.sendBeacon('/api/focus/sync', new Blob([body], {type: 'application/json'}));
    else fetch('/api/focus/sync', {method: 'POST', headers: {'Content-Type': 'application/json'}, body, keepalive: true}).catch(() => {});
  },
  async end(ended, then) {
    if (!Focus.on || Focus.ending) return;
    Focus.ending = true;
    let r = null;
    try { r = await api('/api/focus/end', Focus.payload({ended})); } catch (e) {}
    try { sessionStorage.removeItem('fz'); } catch (e) {}
    Focus.on = false;
    const again = async () => { await Focus.ensure(); then && then(); };
    if (!r || !r.segs) { Focus.ending = false; return again(); }
    if (Focus.reading) { if (r.held) toast(Focus.adult() ? '又专注了一段 ✓' : '🔥 一口气撑住了，接着读', 2400); Focus.ending = false; return again(); }
    Focus.card(r, ended, again);
  },
  line(segs) {
    const tot = segs.reduce((a, s) => a + s[0], 0) || 1;
    return `<div class="fz-line" aria-hidden="true">${segs.map(s => `<i class="${s[1] ? 'on' : ''}" style="flex:${(s[0] / tot).toFixed(4)}"></i>`).join('')}</div>`;
  },
  card(r, ended, again) {
    const kid = !Focus.adult(), b = Focus.b;
    const head = r.held ? (kid ? '🔥 这一口气撑住了！' : '这一段完成 ✓') : ended === 'early' ? (kid ? '✓ 先歇一下' : '先歇一下') : (kid ? '✓ 这一口气做完了' : '这一段完成');
    const rest = b.move ? `🤸 动一动 ${Math.round(b.rest_s / 60)} 分钟` : `☕ 休息 ${Math.round(b.rest_s / 60)} 分钟`;
    const m = document.createElement('div'); m.className = 'flow-sheet fz-sheet'; m.setAttribute('role', 'dialog');
    m.innerHTML = `<div class="fs-card"><div class="fs-h"><b>${head}</b><span>${r.minutes} 分钟${r.n_items ? ' · ' + r.n_items + ' 道题' : ''}</span></div>
      ${Focus.line(r.segs)}<p class="fz-say">${esc(r.sentence)}</p>${r.news ? `<p class="fz-news">${esc(r.news)}</p>` : ''}
      <div class="fs-end"><button type="button" class="btn lg fz-rest-go">${rest}</button><button type="button" class="btn ghost fz-next">${kid ? '接着下一口气 →' : '继续 →'}</button></div></div>`;
    document.body.appendChild(m);
    if (r.held && kid) confetti();
    $('.fz-next', m).onclick = () => { m.remove(); Focus.ending = false; again(); };
    $('.fz-rest-go', m).onclick = () => Focus.rest($('.fs-card', m), b, () => { m.remove(); Focus.ending = false; again(); });
  },
  /* 休息：小学跟着动一动；时间到叫名字（最多 3 次，隔 30 秒），点「我回来了」就停。成人不叫。 */
  rest(card, b, done) {
    Focus.resting = true;
    const kid = !Focus.adult();
    const tips = b.move ? ['原地开合跳 10 下 🙆', '高抬腿 20 下 🏃', '伸个大懒腰，够够天花板 🙌', '转转脖子和肩膀 🔄', '看看窗外最远的地方 🌳']
                        : ['站起来走一走 🚶', '看看窗外最远的地方 🌳', '喝几口水 💧', '转转脖子和肩膀 🔄', '闭上眼睛深呼吸 3 次 😌'];
    let left = b.rest_s, i = 0, calls = 0, callT = null;
    const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    card.classList.add('resting');
    card.innerHTML = `<div class="big">${b.move ? '🤸' : '☕'}</div><b>${b.move ? '动一动' : '休息一下'}</b><div class="rest-t">${mmss(left)}</div><p class="rest-tip">${tips[0]}</p><button type="button" class="btn ghost block">${kid ? '我回来了 →' : '继续 →'}</button>`;
    const t = $('.rest-t', card), tip = $('.rest-tip', card);
    const call = () => {
      if (!kid || calls >= 3) return;
      calls++;
      say(`${(window.ME && window.ME.call) || ''}，休息好了，回来接着学吧`, 'zh');
      callT = setTimeout(call, 30000);
    };
    const timer = setInterval(() => {
      left--;
      if (left > 0) { t.textContent = mmss(left); if (left % 20 === 0) tip.textContent = tips[++i % tips.length]; return; }
      clearInterval(timer); t.textContent = kid ? '时间到！' : '休息好了'; call();
    }, 1000);
    $('button', card).onclick = () => { clearInterval(timer); clearTimeout(callT); Speak.stop(); Focus.resting = false; Focus.last = Focus.tick = Date.now(); done(); };
  },
  /* 进度条：🔥 这一口气 ▓▓▓░░ 2:13 / 5:00 · ☕ 休息 */
  mount() {
    if (Focus.el && document.body.contains(Focus.el)) return;
    const el = document.createElement('div'); el.className = 'fzbar'; el.setAttribute('role', 'status');
    el.innerHTML = `<span class="fz-ico" aria-hidden="true">🔥</span><span class="fz-t">${Focus.adult() ? '本段' : '这一口气'}</span><span class="fz-track"><i></i></span><span class="fz-n"></span><button type="button" class="linkbtn fz-pause" title="休息一下，回来接着这口气">☕</button>`;
    const at = $('.flowbar') || $('#runbar');
    if (at) at.insertAdjacentElement('afterend', el); else ($('main h1') || $('main') || document.body).insertAdjacentElement($('main h1') ? 'afterend' : 'afterbegin', el);
    Focus.el = el;
    $('.fz-pause', el).onclick = () => {
      if (!Focus.on || Focus.resting) return;
      const m = document.createElement('div'); m.className = 'flow-sheet fz-sheet'; m.innerHTML = '<div class="fs-card"></div>';
      document.body.appendChild(m); Focus.sync(false);
      Focus.rest($('.fs-card', m), Focus.b, () => m.remove());
    };
  },
  /* 只按整分钟跳一格，不一秒一秒地走：读题、想题的时候页面不动（DESIGN 5.30） */
  draw() {
    const el = Focus.el; if (!el || !Focus.b) return;
    // 平时收着（题目是主角）：最后一分钟、到点了、或者有点晃（state 不是 on）才浮出来；说的还是「这一口气」，不说「你走神了」
    const m = Math.floor(Focus.active / 60000), tm = Math.round(Focus.b.target_ms / 60000);
    const show = Focus.due || Focus.state !== 'on' || Focus.b.target_ms - Focus.active <= 60000, key = (Focus.due ? 'due' : m) + (show ? '' : 'q');
    el.classList.toggle('quiet', !show);
    if (el.dataset.k === String(key)) return;
    el.dataset.k = key;
    $('.fz-track i', el).style.width = Math.min(100, 100 * m / tm) + '%';
    el.classList.toggle('due', Focus.due);
    $('.fz-n', el).textContent = Focus.due ? (Focus.adult() ? '到了，做完这题' : '到啦，做完这题就歇') : `${m} / ${tm} 分钟`;
  },
};
document.addEventListener('DOMContentLoaded', () => { if ($('.flowbar')) Focus.ensure(); });

/* 等题时不干等（app/prefetch.py）：题大多提前备好了；真要等，超过 1 秒就在 box 里放一张今天要复习的单词小卡
   （看意思四选一，答了照常算复习），没有单词就放这个知识点「记住」的那一句。题好了，小卡下面出现「题来了 →」，孩子点了才换，不硬切。
   用法：const r = await waitWith(box, api(...), kp)；box 里原来的内容（「正在准备题目」）在等待期间会被换掉。 */
async function waitWith(box, promise, kp) {
  let ready = false, shown = false, go = null, card = null;
  const skip = [];
  const tail = () => $('.fillnext', box);
  const showNext = () => {
    const t = tail(); if (!t) return;
    t.innerHTML = `<div class="row" style="justify-content:center;margin-top:10px"><button class="btn">题来了 →</button></div>`;
    $('.btn', t).onclick = () => go && go();
  };
  const fill = async () => {
    if (ready) return;
    let f = null;
    try { f = await api('/api/filler?' + new URLSearchParams({kp: kp || '', skip: skip.join(',')})); } catch (e) { return; }
    if (ready) { if (shown) showNext(); return; }  // 取小卡的工夫题已经好了：不再换新卡
    if (!f || (!f.card && !f.remember)) { if (shown && tail()) tail().innerHTML = `<p class="loading small" style="text-align:center">${esc(AI.name)}马上把题拿来</p>`; return; }
    shown = true;
    if (f.card) {
      card = f.card; skip.push(f.card);
      const q = f.quiz;
      box.innerHTML = `<div class="fillcard"><div class="muted small">等题的工夫，复习一个${f.kind === 'term' ? '术语' : '单词'}</div>` +
        `<div class="face">${esc(q.q)}${q.hint ? `<div class="muted small">${esc(q.hint)}</div>` : ''}</div>` +
        q.options.map(o => `<button class="opt" data-v="${esc(o)}">${esc(o)}</button>`).join('') +
        `<div class="fbbox"></div><div class="fillnext">${ready ? '' : `<p class="loading small" style="text-align:center">${esc(AI.name)}还在挑题</p>`}</div></div>`;
      $$('.opt', box).forEach(b => b.onclick = async () => {
        $$('.opt', box).forEach(x => x.disabled = true);
        let res = {};
        try { res = await api(`/api/review/${card}/answer`, {answer: b.dataset.v, mode: 'choice'}); } catch (e) { /* 小卡答不上也不耽误做题 */ }
        $$('.opt', box).forEach(x => { if (x.dataset.v === res.answer) x.classList.add('right'); else if (x === b && !res.correct) x.classList.add('wrong'); });
        $('.fbbox', box).innerHTML = res.answer ? `<div class="fb ${res.correct ? 'ok' : 'no'} pop">${res.correct ? '✅ 对了！' : '正确答案是 <b>' + esc(res.answer) + '</b>'}</div>` : '';
        if (/^[A-Za-z]/.test(res.answer || '')) say(res.answer);
        if (ready) showNext(); else setTimeout(fill, 1200);  // 题还没好：再来一张
      });
    } else {
      box.innerHTML = `<div class="fillcard"><div class="muted small">等题的工夫，先记一记</div><div class="hint">📌 ${esc(f.remember)}</div>` +
        `<div class="fillnext">${ready ? '' : `<p class="loading small" style="text-align:center">${esc(AI.name)}还在挑题</p>`}</div></div>`;
      if (ready) showNext();
    }
  };
  const timer = setTimeout(fill, 1000);
  let r;
  try { r = await promise; } finally { ready = true; clearTimeout(timer); }
  if (!shown) return r;
  // 小卡在屏幕上：下面出「题来了 →」，孩子可以答完这张再点，也可以直接去做题
  return new Promise(res => { go = () => res(r); showNext(); });
}

/* 等 AI 写内容时，小伙伴出来「翻书」（docs/DESIGN.md 5.34）：以前只有一行灰色小字「正在准备…」，孩子找不到，直接点了下一步。
   现在 box 里是小伙伴 + 一句它在干什么 + 一串它正在做的事（按写这份内容的真实步骤），一步步打勾，最后一步等内容好了才算完。
   刚打开就有的（库里现成的）不闪：350 毫秒内好了就什么都不显示。等的时候没有题要想，所以小伙伴可以动；内容一出来就停。
   用法：const s = seek(box, 'story', {domain})；内容好了 s.stop()，返回 true 表示刚才确实等过（可以让内容淡入）。
   compact：一行的小版本（题这种通常很快、或者后面还有等题小卡接着的）。 */
const SEEK_SRC = {  // 「为什么学」第一步去哪翻：先按学科，再按学科领域
  math: ['📜', '翻古代的算书和数学家的手稿'], science: ['🔭', '翻科学家几百年来的实验笔记'], zh: ['📜', '翻古人的诗文和老字典'],
  language: ['🗺️', '翻世界各地的书、信和老报纸'], humanities: ['🏛️', '翻史书和博物馆里的老物件'], computing: ['💾', '翻计算机先驱们的笔记'],
  social: ['🏛️', '翻史书、地图和各国的老档案'], finance: ['📈', '翻经典案例和行业里的老账本'], '': ['📜', '翻几千年的书和资料'],
  geography: ['🗺️', '翻探险家的地图和航海日志'], economics: ['📈', '翻企业家的复盘和经典案例'], ceo: ['📈', '翻企业家的复盘和经典案例'],
};
const SEEK = {
  story: o => ({t: '正在帮你翻几千年的书', sub: '找这个知识点是怎么来的，找到就放在这里',
    steps: [SEEK_SRC[o.lang === 'zh' ? 'zh' : o.subject] || SEEK_SRC[o.domain] || SEEK_SRC[''], ['🧑‍🔬', '找当年是谁、被什么难题卡住了'], ['🏙️', '看看今天生活里哪里用得上'], ['🎨', '挑一个最好懂的故事，画成故事条']]}),
  teach: () => ({t: '正在把它讲成几小步', sub: '讲好了就在这里展开',
    steps: [['📚', '翻一翻大家都是怎么讲它的'], ['💡', '找一个最好懂的讲法'], ['🧩', '拆成几小步，配上例子和图'], ['✅', '从头检查一遍']]}),
  worked: () => ({t: '正在挑一道例题，做给你看', steps: [['📝', '挑一道最典型的题'], ['✍️', '一步一步写清楚'], ['✅', '检查一遍']]}),
  items: () => ({t: '正在按你的水平挑题', steps: [['📊', '看看你哪里稳、哪里还不稳'], ['🎯', '挑难度刚好的题'], ['✅', '核对答案和解析']]}),
  passage: o => ({t: '正在给你写一篇短文', sub: '写好了会自动打开，大约半分钟',
    steps: [['🧭', o.topic ? `围绕「${o.topic}」找素材` : '挑一个你会喜欢的话题'], ...(o.lang === 'en' ? [['🔤', '把你最近要复习的生词放进去']] : []),
      ['📏', '按你的年级调好长短和难度'], ['❓', '出几道读完做的小题']]}),
  // 下面三种是等 AI 回话（aiWait）：步骤就是让 AI 做的那几件事，按这个顺序写回来
  discuss: () => ({t: '正在读你的想法', sub: '先把你的观点说得更有力，再提一个值得想的问题', long: '这次想得久一点，再等几秒',
    steps: [['🔍', '读懂你的判断和理由'], ['🧠', '分析：哪里最有力，哪里还没想到'], ['💬', '写回应，再提一个新挑战']]}),
  discuss_close: () => ({t: '正在对照你这一路的想法', long: '这次想得久一点，再等几秒',
    steps: [['🔁', '对照你开头和现在的想法'], ['🧭', '和几种成熟的看法比一比'], ['⚠️', '找出这个结论在什么情况下会出问题']]}),
  review: () => ({t: '正在看你的回答', long: '这次想得久一点，再等几秒',
    steps: [['🔍', '诊断：逐条对照要点'], ['🧠', '分析：找出最关键的缺口'], ['✍️', '给出建议：写点评和追问']]}),
};
const SEEK_AT = [0, 2600, 6000, 10500, 15500];  // 第几毫秒走到第几步；最后一步一直等到内容好了
let seekN = 0;
function seekFace() {  // 同一页可能放几次小伙伴：SVG 里的 id 每次换个后缀
  const n = ++seekN, f = (window.AI && AI.face) || '🙋';
  return f.startsWith('<svg') ? f.replace(/id="([\w-]+)"/g, `id="$1-s${n}"`).replace(/url\(#([\w-]+)\)/g, `url(#$1-s${n})`) : `<span class="seek-emo">${esc(f)}</span>`;
}
function seek(box, kind, o) {
  o = o || {};
  const spec = SEEK[kind](o), name = (window.AI && AI.name) || '';
  // 成人：不出小伙伴、不带名字，只有一个转圈和几行字（DESIGN 5.40）
  const adult = document.body.dataset.tone === 'adult';
  const title = o.title || (adult ? spec.t : name + spec.t);
  const face = adult ? '<span class="seek-spin" aria-hidden="true"></span>' : `<span class="seek-face">${seekFace()}<i class="seek-lens" aria-hidden="true">🔍</i></span>`;
  let shown = false, stopped = false;
  const timers = [];
  const show = () => {
    if (stopped) return;
    shown = true;
    if (o.compact) {
      box.innerHTML = `<div class="seek compact${adult ? ' plain' : ''}" role="status">${face}<span class="loading">${esc(title)}</span></div>`;
      return;
    }
    box.innerHTML = `<div class="seek${adult ? ' plain' : ''}" role="status" aria-live="polite"><div class="seek-hd">${face}` +
      `<div><b class="seek-t">${esc(title)}</b>${spec.sub ? `<div class="seek-sub">${esc(spec.sub)}</div>` : ''}</div></div>` +
      `<ol class="seek-trail">${spec.steps.map(([ico, txt]) => `<li><span class="seek-ico" aria-hidden="true">${adult ? '' : ico}</span><span>${esc(txt)}</span></li>`).join('')}</ol>` +
      `<div class="seek-long small muted" hidden>${esc(spec.long || '这次要翻的书有点多，再等一下下')}</div></div>`;
    const lis = $$('.seek-trail li', box);
    const at = i => { if (stopped) return; lis.forEach((li, j) => { li.className = j < i ? 'done' : j === i ? 'cur' : ''; }); };
    lis.forEach((_, i) => timers.push(setTimeout(() => at(i), SEEK_AT[i] ?? SEEK_AT[SEEK_AT.length - 1] + i * 4000)));
    timers.push(setTimeout(() => { const l = $('.seek-long', box); if (l && !stopped) l.hidden = false; }, 25000));
  };
  if (o.delay === 0) show(); else timers.push(setTimeout(show, o.delay ?? 350));
  return {stop() { stopped = true; timers.forEach(clearTimeout); return shown; }};
}
/* 等 AI 回话（思辨题、AI 点评这种要想十几秒的）：所有这样的按钮同一套（DESIGN 5.40）。
   以前按下去只是按钮变浅，十几秒什么都没有，大家以为卡住了，一直点。现在：
   1. 按下立刻锁住，按钮上转圈写「正在想」，写的字也锁住（不会提交第二次）；
   2. box 里马上出现 AI 正在做的几步（seek：孩子是小伙伴，成人只有文字），一步步打勾；
   3. AI 一开始写，就把写出来的话一段段放上来（边写边看），写完换成正式的卡片。
   用法：const w = aiWait(btn, box, 'discuss', textarea…)；opts.submit(…, w.part)；好了 w.end()，出错 w.fail()（按钮和输入框放回去）。 */
const AIDRAFT = {  // 边写边看时先放哪几栏，和正式卡片同样的开头
  discuss: [['steelman', '💡 '], ['answer', '<b>你问的：</b>'], ['challenge', '🤔 ']],
  discuss_close: [['shift', ''], ['own', '✨ 你自己的东西：'], ['watch', '⚠️ 值得留意：']],
  review: [['feedback', ''], ['followup', '🤔 追问：']],
};
function aiWait(btn, box, kind, ...inputs) {
  const label = btn.innerHTML;
  btn.disabled = true; btn.setAttribute('aria-busy', 'true'); btn.textContent = '正在想';
  inputs.forEach(i => { if (i) i.readOnly = true; });
  const slot = document.createElement('div'); slot.className = 'aiwait'; box.appendChild(slot);
  const look = seek(slot, kind, {delay: 0});
  let draft = null;
  const done = () => { look.stop(); slot.remove(); btn.removeAttribute('aria-busy'); };
  return {
    part(p) {
      look.stop();
      if (!draft) { slot.innerHTML = `<div class="fb dk aiwait-draft" aria-live="polite"><div class="loading small">正在写</div><div class="aiwait-body"></div></div>`; draft = $('.aiwait-body', slot); }
      draft.innerHTML = AIDRAFT[kind].filter(([k]) => p[k]).map(([k, pre]) => `<p>${pre}${esc(p[k])}</p>`).join('');
    },
    end: done,
    fail() { done(); btn.disabled = false; btn.innerHTML = label; inputs.forEach(i => { if (i) i.readOnly = false; }); },
  };
}
/* 内容来了：等过的就轻轻淡入，接上刚才的等待 */
function seekIn(el, waited) { if (waited && el) { el.classList.remove('seek-in'); void el.offsetWidth; el.classList.add('seek-in'); } }

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
    if (Speak.playing && !document.hidden) Beat.last = now;  // 在听朗读也算在学
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

/* ---------- 图表上的数：鼠标放上去 / 手指点一下，看那一天的具体数 ----------
   · 带 data-tip 的格子、柱子（学习日历、这一周、热力图、后台近 14 天）：悬停显示；手指点第一下显示，再点一下才打开那一天
   · 正确率曲线（.tplot[data-trend]，数据在里面的 JSON）：鼠标在图上移动 / 手指左右划，吸到最近的一天，竖线标出来
   · 键盘：Tab 到点或格子上同样显示 */
const ChartTip = (() => {
  let box = null, cur = null, touch = false;
  const WD = '日一二三四五六';
  function tipBox() {
    if (!box) { box = document.createElement('div'); box.className = 'ctip'; box.setAttribute('role', 'status'); box.hidden = true; document.body.appendChild(box); }
    return box;
  }
  /* at = 图上要指着的那一点（屏幕坐标）；没有就指着元素的上沿中间 */
  function show(owner, html, at) {
    const b = tipBox(); b.style.left = b.style.top = '0px'; b.innerHTML = html; b.hidden = false; cur = owner;
    const r = owner.getBoundingClientRect();
    const x0 = at ? at.x : r.left + r.width / 2, top = at ? at.y : r.top, bottom = at ? at.y : r.bottom;
    const w = b.offsetWidth, h = b.offsetHeight, vw = document.documentElement.clientWidth;
    const y = top - h - 12 >= 6 ? top - h - 12 : bottom + 14;
    b.style.left = Math.round(Math.min(Math.max(6, x0 - w / 2), vw - w - 6)) + 'px';
    b.style.top = Math.round(y) + 'px';
  }
  function hide() {
    if (box) box.hidden = true;
    if (cur && cur.matches('.tplot')) clearTrend(cur);
    if (cur) cur.classList.remove('tip-on');
    cur = null;
  }
  function plain(t, linked) {
    const [head, ...rest] = String(t).split('\n');
    return `<b>${esc(head)}</b>${rest.map(l => `<span>${esc(l)}</span>`).join('')}` +
      (linked && touch ? '<em>再点一下，看这一天 →</em>' : '');
  }

  /* 正确率曲线 */
  function days(plot) {
    if (!plot._days) { try { plot._days = JSON.parse(plot.querySelector('script[type="application/json"]').textContent); } catch (e) { plot._days = []; } }
    return plot._days;
  }
  function nearest(plot, clientX) {
    const ds = days(plot); if (!ds.length) return -1;
    const r = plot.getBoundingClientRect(), px = 100 * (clientX - r.left) / Math.max(1, r.width);
    let bi = 0; ds.forEach((d, i) => { if (Math.abs(d.x - px) < Math.abs(ds[bi].x - px)) bi = i; });
    return bi;
  }
  function trendHtml(plot, d) {
    const dt = new Date(d.d + 'T00:00:00'), min = +plot.dataset.min || 5;
    const head = `${dt.getMonth() + 1}/${dt.getDate()} 周${WD[dt.getDay()]}`;
    const body = d.n
      ? `<span>做了 ${d.n} 题，对 ${d.ok} 题</span><span class="big">正确率 ${d.acc}%${d.n < min ? ' <small>题少，参考就好</small>' : ''}</span>`
      : '<span class="muted">这天没做题</span>';
    const avg = d.avg == null ? '' : `<span>近 7 天平均 ${d.avg}%</span>`;
    const go = plot.hasAttribute('data-link') && d.n && touch ? '<em>再点一下，看这一天 →</em>' : '';
    return `<b>${head}</b>${body}${avg}${go}`;
  }
  function clearTrend(plot) { plot._i = -1; plot.querySelectorAll('.tg,.th,.ta').forEach(e => { e.hidden = true; }); plot.querySelectorAll('.pt.on').forEach(e => e.classList.remove('on')); }
  function showTrend(plot, i) {
    const d = days(plot)[i]; if (!d) return;
    if (cur && cur !== plot) hide();
    plot._i = i;
    const g = plot.querySelector('.tg'), h = plot.querySelector('.th'), a = plot.querySelector('.ta');
    g.hidden = false; g.style.left = d.x + '%';
    h.hidden = d.y == null; if (d.y != null) { h.style.left = d.x + '%'; h.style.top = d.y + '%'; }
    a.hidden = d.ya == null; if (d.ya != null) { a.style.left = d.x + '%'; a.style.top = d.ya + '%'; }
    plot.querySelectorAll('.pt').forEach(p => p.classList.toggle('on', p.dataset.day === d.d));
    const r = plot.getBoundingClientRect(), yy = d.y != null ? d.y : (d.ya != null ? d.ya : 0);
    show(plot, trendHtml(plot, d), {x: r.left + r.width * d.x / 100, y: r.top + r.height * yy / 100});
  }
  function openDay(plot, i) { const d = days(plot)[i]; if (d && d.n && plot.hasAttribute('data-link')) location.href = '/day/' + d.d; }

  document.addEventListener('pointerdown', e => {
    touch = e.pointerType !== 'mouse';
    const plot = e.target.closest && e.target.closest('.tplot[data-trend]');
    if (plot) { plot._prev = cur === plot ? plot._i : -1; showTrend(plot, nearest(plot, e.clientX)); }
    else if (cur && !(box && box.contains(e.target)) && !(e.target.closest && e.target.closest('[data-tip]') === cur)) hide();
  }, true);
  document.addEventListener('pointermove', e => {
    const plot = e.target.closest && e.target.closest('.tplot[data-trend]');
    if (plot && (e.pointerType === 'mouse' || e.buttons || e.pressure)) {
      const i = nearest(plot, e.clientX); if (i !== plot._i || cur !== plot) showTrend(plot, i);
    }
  });
  document.addEventListener('pointerover', e => {
    if (e.pointerType !== 'mouse') return;
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (t && t !== cur) { hide(); t.classList.add('tip-on'); show(t, plain(t.dataset.tip, false)); }
  });
  document.addEventListener('pointerout', e => {
    if (e.pointerType !== 'mouse' || !cur) return;
    const to = e.relatedTarget;
    if (cur.matches('.tplot') ? !(to && cur.contains(to)) : (e.target.closest('[data-tip]') === cur && !(to && cur.contains(to)))) hide();
  });
  document.addEventListener('click', e => {
    if (!e.detail) return;   // 键盘回车：照常打开链接
    const plot = e.target.closest && e.target.closest('.tplot[data-trend]');
    if (plot) {
      e.preventDefault();
      if (!touch || plot._prev === plot._i) openDay(plot, plot._i);
      return;
    }
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (t && touch) {
      const linked = t.matches('a[href]');
      if (cur !== t || !linked) { e.preventDefault(); hide(); t.classList.add('tip-on'); show(t, plain(t.dataset.tip, linked)); }
    }
  });
  document.addEventListener('focusin', e => {
    const pt = e.target.closest && e.target.closest('.tplot[data-trend] .pt');
    if (pt) { const plot = pt.closest('.tplot'); showTrend(plot, days(plot).findIndex(d => d.d === pt.dataset.day)); return; }
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (t && t !== cur && e.target.matches(':focus-visible')) { hide(); t.classList.add('tip-on'); show(t, plain(t.dataset.tip, false)); }
  });
  document.addEventListener('focusout', e => { if (cur && cur.contains(e.target) && !(e.relatedTarget && cur.contains(e.relatedTarget))) hide(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
  window.addEventListener('scroll', () => { if (cur) hide(); }, {passive: true});
  return {hide};
})();

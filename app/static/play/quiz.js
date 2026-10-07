/* 统一答题面板（所有游戏共用）：出题、判分、经验值、冷冻都走服务器（app/arena/），游戏只管玩法。
     const quiz = new ArenaQuiz({matchId, onAnswer, onXP, onOpen, onClose, dock});
     quiz.open();   // 弹出式（火柴人：左下角「答题」按钮）
   dock：传一个容器元素就变成「常驻」式，一直显示在游戏下方（闪电赛跑、星星守卫：答题就是操作）。
   答对：+经验值、撒花，马上出下一题；答错：冷冻 5 秒（瞎答更久），显示正确答案和思路。
   单词题可以点 🔊 听发音（浏览器自带的朗读）。 */
class ArenaQuiz {
  constructor(opts) {
    this.o = opts; this.item = null; this.frozenUntil = 0; this.busy = false; this.streak = 0;
    this.docked = !!opts.dock;
    this.isOpen = this.docked;
    const el = document.createElement('div');
    el.className = 'aq' + (this.docked ? ' aq-docked' : '');
    el.hidden = !this.docked;
    el.innerHTML = `<div class="aq-card" role="dialog" aria-label="答题">
      <div class="aq-head"><span class="aq-topic"></span><span class="aq-streak"></span>${this.docked ? '' : '<button class="aq-x" type="button" aria-label="回去玩">回去玩 ✕</button>'}</div>
      <div class="aq-body"></div></div>`;
    (opts.dock || document.body).appendChild(el);
    this.el = el; this.body = el.querySelector('.aq-body');
    const x = el.querySelector('.aq-x'); if (x) x.onclick = () => this.close();
    if (!this.docked) el.addEventListener('pointerdown', e => { if (e.target === el) this.close(); });
    this._key = e => this.onKey(e);
    if (this.docked) document.addEventListener('keydown', this._key, true);
  }
  get frozen() { return Date.now() < this.frozenUntil; }
  open() {
    if (this.isOpen && !this.docked) return;
    this.isOpen = true; this.el.hidden = false;
    if (!this.docked) { document.addEventListener('keydown', this._key, true); requestAnimationFrame(() => this.el.classList.add('on')); }
    window.Play && Play.sfx.play('pop');
    this.o.onOpen && this.o.onOpen();
    if (this.frozen) this.showFreeze(); else if (!this.item) this.load(); else this.render();
  }
  close() {
    if (!this.isOpen || this.docked) return;
    this.isOpen = false; this.el.classList.remove('on'); this.el.hidden = true; clearInterval(this.timer);
    document.removeEventListener('keydown', this._key, true);
    this.o.onClose && this.o.onClose();
  }
  destroy() { clearInterval(this.timer); document.removeEventListener('keydown', this._key, true); this.el.remove(); }
  async load() {
    this.body.innerHTML = '<div class="aq-wait"><span></span><span></span><span></span></div>';
    try {
      const r = await fetch(`/api/arena/${this.o.matchId}/q`);
      const d = await r.json();
      if (r.status === 423) { this.frozenUntil = Date.now() + (d.freeze_ms || 5000); return this.showFreeze(); }
      if (!r.ok) throw new Error(d.error || d.detail || '出错了');
      this.item = d.item; this.render();
    } catch (e) { this.body.innerHTML = `<div class="err">${esc(e.message)}</div><button type="button" class="btn sm aq-retry">再试一次</button>`; this.body.querySelector('.aq-retry').onclick = () => this.load(); }
  }
  speak(text) {
    try { const u = new SpeechSynthesisUtterance(text); u.lang = 'en-US'; u.rate = 0.85; speechSynthesis.cancel(); speechSynthesis.speak(u); } catch (e) { /* 不支持朗读 */ }
  }
  render() {
    const it = this.item;
    this.el.querySelector('.aq-topic').textContent = it.topic || '';
    const isWord = it.src === 'words' && it.say;
    let h = `<div class="aq-q${it.q.length > 26 ? ' long' : ''}${isWord ? ' word' : ''}">${esc(it.q)}${isWord ? ' <button type="button" class="aq-say" aria-label="听发音">🔊</button>' : ''}</div>`;
    if (it.widget === 'choice') {
      h += `<div class="aq-opts">${it.options.map((o, i) => `<button type="button" class="aq-opt c${i}" data-i="${i}"><b>${'ABCD'[i]}</b><span>${esc(o)}</span></button>`).join('')}</div>`;
    } else {
      const coarse = matchMedia('(pointer:coarse)').matches;
      h += `<div class="aq-ans"><input class="aq-in" autocomplete="off" ${coarse ? 'inputmode="none" readonly' : 'inputmode="decimal"'} placeholder="?">` +
        (it.unit ? `<span class="aq-unit">${esc(it.unit)}</span>` : '') + `<button type="button" class="aq-ok">确定</button></div>`;
      const extra = it.keypad === 'frac' ? '/' : it.keypad === 'neg' ? '−' : '.';
      const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', extra, '0', '⌫'];
      h += `<div class="aq-pad">${keys.map(k => `<button type="button" data-k="${k}"${k === '⌫' ? ' class="del"' : ''}>${k}</button>`).join('')}</div>`;
    }
    h += `<div class="aq-fb"></div>`;
    this.body.innerHTML = h;
    const say = this.body.querySelector('.aq-say');
    if (say) { say.onclick = () => this.speak(it.say); if (this.o.autoSpeak !== false) this.speak(it.say); }
    const inp = this.body.querySelector('.aq-in');
    if (inp) {
      if (!inp.readOnly) setTimeout(() => inp.focus({preventScroll: true}), 30);
      this.body.querySelectorAll('.aq-pad button').forEach(b => b.onclick = () => {
        const k = b.dataset.k; window.Play && Play.sfx.play('click');
        inp.value = k === '⌫' ? inp.value.slice(0, -1) : (inp.value + (k === '−' ? '-' : k)).slice(0, 12);
      });
      this.body.querySelector('.aq-ok').onclick = () => this.submit(inp.value);
    }
    this.body.querySelectorAll('.aq-opt').forEach(b => b.onclick = () => this.submit(b.dataset.i, b));
  }
  onKey(e) {
    if (!this.isOpen) return;
    const t = e.target;
    if (this.docked && t && t.matches && t.matches('input:not(.aq-in), textarea')) return;
    const inp = this.body.querySelector('.aq-in');
    if (!this.docked) e.stopPropagation();  // 弹出时不触发游戏按键
    if (e.key === 'Escape' && !this.docked) { e.preventDefault(); this.close(); return; }
    if (e.key === 'Enter') {
      const nx = this.body.querySelector('.aq-next');
      if (nx && !nx.disabled) { e.preventDefault(); nx.click(); } else if (inp && this.item) { e.preventDefault(); this.submit(inp.value); }
      return;
    }
    if (this.item && this.item.widget === 'choice' && /^[1-4a-dA-D]$/.test(e.key)) {
      const i = /\d/.test(e.key) ? +e.key - 1 : 'abcd'.indexOf(e.key.toLowerCase());
      const b = this.body.querySelector(`.aq-opt[data-i="${i}"]`); if (b && !b.disabled) { e.preventDefault(); e.stopPropagation(); b.click(); }
      return;
    }
    if (inp && inp.readOnly && /^[0-9./-]$/.test(e.key)) { inp.value = (inp.value + e.key).slice(0, 12); e.preventDefault(); e.stopPropagation(); }
    else if (inp && inp.readOnly && e.key === 'Backspace') { inp.value = inp.value.slice(0, -1); e.preventDefault(); e.stopPropagation(); }
  }
  async submit(answer, btn) {
    if (this.busy || !this.item) return;
    if (String(answer ?? '').trim() === '') { const i = this.body.querySelector('.aq-in'); i && i.classList.add('shake'); setTimeout(() => i && i.classList.remove('shake'), 400); return; }
    this.busy = true;
    try {
      const res = await api(`/api/arena/${this.o.matchId}/a`, {item_id: this.item.id, answer: String(answer).replace('−', '-')});
      const it = this.item; this.item = null;
      this.body.querySelectorAll('button:not(.aq-x):not(.aq-say)').forEach(b => b.disabled = true);
      if (btn) btn.classList.add(res.correct ? 'right' : 'wrong');
      if (!res.correct && it.widget === 'choice') {   // 标出正确选项
        const right = it.options.findIndex(o => res.answer.endsWith(o));
        const rb = this.body.querySelector(`.aq-opt[data-i="${right}"]`); if (rb) rb.classList.add('right');
      }
      this.streak = res.correct ? (res.streak || this.streak + 1) : 0;
      this.o.onAnswer && this.o.onAnswer(res);
      if (res.correct) this.showRight(res); else { this.frozenUntil = Date.now() + (res.freeze_ms || 5000); this.showWrong(res); }
    } catch (e) {
      this.body.querySelector('.aq-fb').innerHTML = `<div class="err">${esc(e.message)}</div>`;
      if (/过期/.test(e.message)) setTimeout(() => this.load(), 800);
    } finally { this.busy = false; }
  }
  confetti() {
    const box = this.el.querySelector('.aq-card');
    for (let i = 0; i < 14; i++) {
      const s = document.createElement('i'); s.className = 'aq-conf';
      s.style.left = (30 + Math.random() * 40) + '%'; s.style.setProperty('--dx', (Math.random() * 240 - 120) + 'px');
      s.style.setProperty('--dy', (-60 - Math.random() * 120) + 'px'); s.style.background = ['#ffd23f', '#ff8fab', '#7bdff2', '#b8f2a6', '#c3a6ff'][i % 5];
      box.appendChild(s); setTimeout(() => s.remove(), 900);
    }
  }
  showRight(res) {
    window.Play && Play.sfx.play(res.crit ? 'crit' : 'right');
    this.confetti();
    this.o.onXP && this.o.onXP(res);
    this.el.querySelector('.aq-streak').textContent = res.streak >= 2 ? `🔥 连对 ${res.streak}` : '';
    const fb = this.body.querySelector('.aq-fb');
    fb.innerHTML = `<div class="aq-right pop"><div class="aq-xp">+${res.xp} ⚡${res.crit ? ' <span class="aq-crit">暴击 ×2！</span>' : ''}</div>` +
      (res.special ? `<div class="aq-sp">💥 连对 ${res.streak} 题，攒到一次大招！</div>` : '') + '</div>';
    if (this.docked || this.o.autoNext) setTimeout(() => { if (this.isOpen && !this.item && !this.frozen) this.load(); }, this.docked ? 650 : 750);
    else {
      fb.firstChild.insertAdjacentHTML('beforeend', '<button type="button" class="btn lg block aq-next">继续答题 →</button>');
      this.body.querySelector('.aq-next').onclick = () => this.load();
    }
  }
  showWrong(res) {
    window.Play && (Play.sfx.play('wrong'), Play.sfx.play('freeze', 0.25));
    this.el.querySelector('.aq-streak').textContent = '';
    this.el.querySelector('.aq-card').classList.add('frozen');
    this.body.querySelector('.aq-fb').innerHTML =
      `<div class="aq-wrong pop"><div>正确答案：<b>${esc(res.answer)}</b></div>` +
      (res.explain ? `<div class="small">${esc(res.explain)}</div>` : '') +
      (res.guessing ? `<div class="small"><b>答得太快了</b>，看清题目再答，冷冻时间会短一些。</div>` : '') +
      `<button type="button" class="btn lg block aq-next" disabled>❄️ 冷冻中…</button></div>`;
    this.countdown(this.body.querySelector('.aq-next'));
  }
  showFreeze() {
    this.el.querySelector('.aq-card').classList.add('frozen');
    this.body.innerHTML = `<div class="aq-wrong"><div>❄️ 刚才答错了，冷冻一会儿。</div><button type="button" class="btn lg block aq-next" disabled>❄️ 冷冻中…</button></div>`;
    this.countdown(this.body.querySelector('.aq-next'));
  }
  countdown(btn) {
    clearInterval(this.timer);
    const tick = () => {
      const left = Math.ceil((this.frozenUntil - Date.now()) / 1000);
      if (left > 0) { btn.textContent = `❄️ 冷冻 ${left} 秒`; return; }
      clearInterval(this.timer); this.el.querySelector('.aq-card').classList.remove('frozen');
      btn.disabled = false; btn.textContent = '继续答题 →';
      btn.onclick = () => this.load();
      if (this.docked) this.load();
    };
    tick(); this.timer = setInterval(tick, 200);
  }
}

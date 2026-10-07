/* 游戏乐园的统一答题面板（所有游戏共用）：出题、作答、经验值、冷冻都走服务器（app/arena.py），
   游戏只管玩法。用法：
     const quiz = new ArenaQuiz({matchId, onXP: r => …, onOpen: () => …, onClose: () => …});
     quiz.open();      // 左下角「答题」按钮
   答对：显示经验值，直接出下一题（可以一直答，点「回去战斗」关掉）；
   答错：冷冻 5 秒（瞎答会更久），显示正确答案和思路，冷冻结束后再继续。 */
class ArenaQuiz {
  constructor(opts) {
    this.o = opts; this.isOpen = false; this.item = null; this.frozenUntil = 0; this.busy = false;
    const el = document.createElement('div');
    el.className = 'aq'; el.hidden = true;
    el.innerHTML = `<div class="aq-card" role="dialog" aria-label="答题">
      <div class="aq-head"><span class="aq-topic"></span><span class="aq-streak"></span><button class="aq-x" type="button" aria-label="回去战斗">回去战斗 ✕</button></div>
      <div class="aq-body"></div></div>`;
    document.body.appendChild(el);
    this.el = el; this.body = el.querySelector('.aq-body');
    el.querySelector('.aq-x').onclick = () => this.close();
    this._key = e => this.onKey(e);
  }
  get frozen() { return Date.now() < this.frozenUntil; }
  async open() {
    if (this.isOpen) return;
    this.isOpen = true; this.el.hidden = false;
    document.addEventListener('keydown', this._key, true);
    this.o.onOpen && this.o.onOpen();
    if (this.frozen) this.showFreeze(); else this.load();
  }
  close() {
    if (!this.isOpen) return;
    this.isOpen = false; this.el.hidden = true; clearInterval(this.timer);
    document.removeEventListener('keydown', this._key, true);
    this.o.onClose && this.o.onClose();
  }
  async load() {
    this.body.innerHTML = '<div class="aq-wait">出题中…</div>';
    try {
      const r = await fetch(`/api/arena/${this.o.matchId}/q`);
      const d = await r.json();
      if (r.status === 423) { this.frozenUntil = Date.now() + (d.freeze_ms || 5000); return this.showFreeze(); }
      if (!r.ok) throw new Error(d.error || d.detail || '出错了');
      this.item = d.item; this.render();
    } catch (e) { this.body.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  }
  render() {
    const it = this.item;
    this.el.querySelector('.aq-topic').textContent = it.topic || '';
    let h = `<div class="aq-q">${esc(it.q)}</div>`;
    if (it.widget === 'choice') {
      h += `<div class="aq-opts">${it.options.map((o, i) => `<button type="button" class="aq-opt" data-i="${i}">${'ABCD'[i]}. ${esc(o)}</button>`).join('')}</div>`;
    } else {
      const coarse = matchMedia('(pointer:coarse)').matches;
      h += `<div class="aq-ans"><input class="aq-in" autocomplete="off" ${coarse ? 'inputmode="none" readonly' : 'inputmode="decimal"'} placeholder="答案">` +
        (it.unit ? `<span class="muted">${esc(it.unit)}</span>` : '') + `</div>`;
      const extra = it.keypad === 'frac' ? ['/'] : it.keypad === 'neg' ? ['−'] : ['.'];
      const keys = ['7', '8', '9', '4', '5', '6', '1', '2', '3', extra[0], '0', '⌫'];
      h += `<div class="aq-pad">${keys.map(k => `<button type="button" data-k="${k}">${k}</button>`).join('')}</div>`;
      h += `<button type="button" class="btn lg block aq-ok">确定</button>`;
    }
    h += `<div class="aq-fb"></div>`;
    this.body.innerHTML = h;
    this.t0 = Date.now();
    const inp = this.body.querySelector('.aq-in');
    if (inp) {
      if (!inp.readOnly) setTimeout(() => inp.focus(), 30);
      this.body.querySelectorAll('.aq-pad button').forEach(b => b.onclick = () => {
        const k = b.dataset.k;
        inp.value = k === '⌫' ? inp.value.slice(0, -1) : (inp.value + (k === '−' ? '-' : k)).slice(0, 12);
      });
      this.body.querySelector('.aq-ok').onclick = () => this.submit(inp.value);
    }
    this.body.querySelectorAll('.aq-opt').forEach(b => b.onclick = () => this.submit(b.dataset.i, b));
  }
  onKey(e) {
    e.stopPropagation();  // 答题时不触发游戏按键
    if (e.key === 'Escape') { e.preventDefault(); this.close(); return; }
    const inp = this.body.querySelector('.aq-in');
    if (e.key === 'Enter') {
      e.preventDefault();
      const nx = this.body.querySelector('.aq-next');
      if (nx && !nx.disabled) nx.click(); else if (inp && this.item) this.submit(inp.value);
      return;
    }
    if (inp && inp.readOnly && /^[0-9./-]$/.test(e.key)) { inp.value = (inp.value + e.key).slice(0, 12); e.preventDefault(); }
    else if (inp && inp.readOnly && e.key === 'Backspace') { inp.value = inp.value.slice(0, -1); e.preventDefault(); }
  }
  async submit(answer, btn) {
    if (this.busy || !this.item) return;
    if (String(answer ?? '').trim() === '') { const i = this.body.querySelector('.aq-in'); i && i.classList.add('shake'); setTimeout(() => i && i.classList.remove('shake'), 400); return; }
    this.busy = true;
    try {
      const res = await api(`/api/arena/${this.o.matchId}/a`, {item_id: this.item.id, answer: String(answer).replace('−', '-')});
      this.item = null;
      this.body.querySelectorAll('button:not(.aq-x)').forEach(b => b.disabled = true);
      if (btn) btn.classList.add(res.correct ? 'right' : 'wrong');
      this.o.onAnswer && this.o.onAnswer(res);
      if (res.correct) this.showRight(res); else { this.frozenUntil = Date.now() + (res.freeze_ms || 5000); this.showWrong(res); }
    } catch (e) {
      this.body.querySelector('.aq-fb').innerHTML = `<div class="err">${esc(e.message)}</div>`;
      if (/过期/.test(e.message)) setTimeout(() => this.load(), 800);
    } finally { this.busy = false; }
  }
  showRight(res) {
    this.o.onXP && this.o.onXP(res);
    this.el.querySelector('.aq-streak').textContent = res.streak >= 2 ? `🔥 连对 ${res.streak}` : '';
    this.body.querySelector('.aq-fb').innerHTML =
      `<div class="aq-right pop"><div class="aq-xp">+${res.xp} 经验值${res.crit ? ' <span class="aq-crit">暴击 ×2！</span>' : ''}</div>` +
      (res.special ? `<div class="aq-sp">⚡ 连对 ${res.streak} 题，攒到一次必杀！</div>` : '') +
      `<button type="button" class="btn lg block aq-next">继续答题 →</button></div>`;
    this.body.querySelector('.aq-next').onclick = () => this.load();
  }
  showWrong(res) {
    this.el.querySelector('.aq-streak').textContent = '';
    this.body.querySelector('.aq-fb').innerHTML =
      `<div class="aq-wrong pop"><div>正确答案是 <b>${esc(res.answer)}</b></div>` +
      (res.explain ? `<div class="small">${esc(res.explain)}</div>` : '') +
      (res.guessing ? `<div class="small"><b>答得太快了</b>，看清题目再答，冷冻时间会短一些。</div>` : '') +
      `<button type="button" class="btn lg block aq-next" disabled>❄️ 冷冻中…</button></div>`;
    this.countdown(this.body.querySelector('.aq-next'));
  }
  showFreeze() {
    this.body.innerHTML = `<div class="aq-wrong"><div>❄️ 刚才答错了，冷冻一会儿。</div><button type="button" class="btn lg block aq-next" disabled>❄️ 冷冻中…</button></div>`;
    this.countdown(this.body.querySelector('.aq-next'));
  }
  countdown(btn) {
    clearInterval(this.timer);
    const tick = () => {
      const left = Math.ceil((this.frozenUntil - Date.now()) / 1000);
      if (left > 0) { btn.textContent = `❄️ 冷冻 ${left} 秒`; return; }
      clearInterval(this.timer); btn.disabled = false; btn.textContent = '继续答题 →';
      btn.onclick = () => this.load();
    };
    tick(); this.timer = setInterval(tick, 200);
  }
}

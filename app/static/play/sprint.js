/* 冲刺页面：题目不限量，连对倍数越来越高（规则都在服务器 app/sprint.py，这里只管显示）。 */
(function () {
  const root = document.getElementById('sp');
  if (!root) return;
  const D = JSON.parse(root.dataset.info || '{}');
  const $s = s => root.querySelector(s);
  if (!D.open) return;
  const TA = D.tier_at;
  let run = null, quiz = null, lastTier = 1, today = D.today;

  $s('#sp-go').onclick = start;
  $s('#sp-stop').onclick = stop;
  goalLine();

  async function start() {
    Play.sfx.unlock(); Play.sfx.play('go');
    try { run = await api('/api/sprint/start', {}); } catch (e) { toast(e.message); return; }
    $s('#sp-start').hidden = true; $s('#sp-end').hidden = true; $s('#sp-run').hidden = false;
    $s('#sp-dock').innerHTML = '';
    lastTier = 1; setTier(1, 0); $s('#sp-pts').textContent = '0';
    quiz = new ArenaQuiz({base: `/api/sprint/${run.run_id}`, dock: $s('#sp-dock'), dontKnow: true,
      rightHtml: r => `<div class="aq-xp">+${r.gain} 分${r.tier > 1 ? ` <span class="sp-mult">×${r.tier}</span>` : ''}</div>` +
        (r.why === 'idle' ? '<div class="small">刚才停了好久，倍数降了一档</div>' : ''),
      wrongHtml: r => r.why === 'guess' ? '<div class="small"><b>答得太快了</b>，像是在猜，倍数回到 ×1。看清题目再答。</div>' :
        r.tier_down ? `<div class="small">倍数降到 ×${r.tier}，再连对几题就回来了。</div>` : '',
      onAnswer: onAnswer});
    quiz.load();
  }

  function onAnswer(r) {
    today = r.points_today;
    $s('#sp-pts').textContent = r.points;
    $s('#sp-today').textContent = r.points_today;
    $s('#sp-stars').textContent = r.stars_today;
    setTier(r.tier, r.heat);
    if (r.tier_up) { tierUp(r.tier); }
    else if (r.tier_down) { Play.sfx.play('hurt'); }
    goalLine();
    if (r.rest) restPop('已经连续冲了 20 分钟，眼睛需要休息一下。', true);
    else if (r.long_day) restPop('今天一共学了 90 分钟了，非常棒！可以休息一下，明天再冲。', true);
  }

  function setTier(t, heat) {
    const el = $s('#sp-tier'); el.className = `sp-tier t${t}`; el.querySelector('b').textContent = t;
    $s('#sp-heat').style.width = Math.min(100, 100 * heat / TA[2]) + '%';
    const nx = TA.find(x => x > heat);
    $s('#sp-next').textContent = nx ? `再连对 ${nx - heat} 题升到 ×${TA.indexOf(nx) + 1}` : '🔥 最高倍数 ×3！保持住';
  }

  function tierUp(t) {
    lastTier = t;
    Play.sfx.play(t === 3 ? 'power' : 'star');
    const el = $s('#sp-tier'); el.classList.remove('pop'); void el.offsetWidth; el.classList.add('pop');
    toast(t === 3 ? '🔥 升到 ×3！最高倍数' : '⚡ 升到 ×2！');
    if (t === 3) confetti();
  }

  function goalLine() {
    const need = D.goal_need;
    $s('#sp-goal').textContent = need ? (today >= need ? `🎯 今天的目标「${D.goal_name}」达成！` : `🎯 目标「${D.goal_name}」：还差 ${need - today} 分`) : '';
  }

  function restPop(text, canGo) {
    const m = document.createElement('div'); m.className = 'sec-pop';
    m.innerHTML = `<div class="sec-card"><div class="big">👀</div><b>休息一下</b><p class="muted">${text}</p>
      <button type="button" class="btn lg block" data-a="stop">好的，结束冲刺</button>${canGo ? '<button type="button" class="btn ghost block" data-a="go">再冲一会儿</button>' : ''}</div>`;
    document.body.appendChild(m);
    m.querySelector('[data-a=stop]').onclick = () => { m.remove(); stop(); };
    const g = m.querySelector('[data-a=go]'); if (g) g.onclick = () => m.remove();
  }

  async function stop() {
    if (!run) return;
    const id = run.run_id; run = null;
    if (quiz) quiz.stop();
    let r;
    try { r = await api(`/api/sprint/${id}/end`, {}); } catch (e) { toast(e.message); return; }
    $s('#sp-run').hidden = true;
    const end = $s('#sp-end'); end.hidden = false;
    end.innerHTML = `<div class="sp-big">${r.new_best ? '🏅' : '⚡'}</div>
      <h2>${r.new_best ? '新纪录！今天冲得最多' : '这次冲刺结束啦'}</h2>
      <div class="pl-stats"><div><b>+${r.points}</b><span>这次的分</span></div><div><b>${r.right}/${r.answered}</b><span>答对 / 答题</span></div><div><b>×${r.best_tier}</b><span>最高倍数</span></div></div>
      <p>今天一共 <b>${r.points_today}</b> 分，换了 <b>${r.stars_today}</b> 颗 ⭐。${r.best && !r.new_best ? `个人纪录 ${r.best} 分。` : ''}</p>
      <div class="pl-row"><button type="button" class="btn lg" id="sp-again">再冲一次</button><a class="btn ghost lg" href="/today">回到今天</a><a class="btn ghost lg" href="/arena">去乐园</a></div>`;
    if (r.new_best) { confetti(); Play.sfx.play('win'); }
    end.querySelector('#sp-again').onclick = start;
  }

  window.addEventListener('pagehide', () => { if (run) navigator.sendBeacon && navigator.sendBeacon(`/api/sprint/${run.run_id}/end`); });
})();

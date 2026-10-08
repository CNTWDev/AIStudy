/* 每个游戏页面的外壳（所有游戏共用）：开始面板（规则、选出什么题）、倒计时、答题面板、结束战报、贴纸、再来一局。
   游戏只写玩法：
     PlayShell.init({
       game: 'race', tempo: 140, dock: true,           // dock = 答题面板常驻在下方
       rules: ['……', '……'], keys: '电脑键盘：……',
       setup(shell) {},                                  // 页面打开时：画一帧预览、生成按钮
       begin(match, quiz, shell) {},                     // 倒计时结束：开始玩
       onAnswer(res) {}, onXP(res) {},                   // 答题结果（答题面板转发）
     });
     shell.finish(result, stats)   // 结束一局：win / lose / draw / quit，stats 是给战报和贴纸用的统计
     shell.toast(text, kind)       // 顶部提示：good / warn
   有关卡的游戏（app/arena/levels.py，现在是火柴人大战）：开始面板上是闯关地图，一个世界一页，选一关开打；
   开局时服务器把这一关的参数放在 match.stage 里，战报显示这次几颗星、解锁了什么，可以直接点「下一关」。
   开局、出题、结束都走服务器（app/arena/matches.py）。 */
(function () {
  const PlayShell = window.PlayShell = {};
  const root = document.getElementById('pl');
  const $ = s => root.querySelector(s);

  PlayShell.init = function (game) {
    const data = JSON.parse(root.dataset.info || '{}');
    const sh = {root, game, data, match: null, quiz: null, over: true, src: localStorageGet('play.src') || 'mix'};
    sh.canvas = $('canvas'); sh.hud = $('.pl-hud'); sh.ctrl = $('.pl-ctrl'); sh.dock = $('.pl-dock');
    sh.toast = (t, k) => Play.ui.toast($('.pl-toast'), t, k);
    if (game.dock) root.classList.add('has-dock');
    const me = $('.pl-me'); if (me) Art.portrait(me, data.avatar, 'win');
    Play.ui.soundButtons(root, game.tempo);

    // ---- 开始面板
    const start = $('.pl-start'), end = $('.pl-end');
    const srcs = data.sources || [];
    if (!srcs.find(s => s.id === sh.src)) sh.src = srcs.length ? srcs[0].id : 'mix';
    start.querySelector('.pl-rules').innerHTML = game.rules.map(r => `<li>${r}</li>`).join('');
    start.querySelector('.pl-keys').textContent = game.keys || '';
    const box = start.querySelector('.pl-srcs');
    if (srcs.length > 1) {
      box.innerHTML = '<div class="pl-label">出什么题？</div>' + srcs.map(s =>
        `<button type="button" class="pl-src${s.id === sh.src ? ' on' : ''}" data-src="${s.id}" title="${esc(s.desc)}"><span>${s.icon}</span>${esc(s.name)}</button>`).join('');
      box.querySelectorAll('.pl-src').forEach(b => b.onclick = () => {
        sh.src = b.dataset.src; localStorageSet('play.src', sh.src); Play.sfx.play('click');
        box.querySelectorAll('.pl-src').forEach(x => x.classList.toggle('on', x === b));
      });
    }
    const how = start.querySelector('.pl-how');
    if (how && $('.pl-stage').clientHeight < 460) how.open = false;   // 小屏幕先收起规则，开始按钮一眼能看到
    const go = start.querySelector('.pl-go');
    if (data.locked) { go.hidden = true; start.querySelector('.pl-locked').textContent = data.locked; }
    go.onclick = () => begin();
    end.querySelector('.pl-again').onclick = () => begin();
    const nextBtn = end.querySelector('.pl-next');
    if (nextBtn) nextBtn.onclick = () => { sh.stage = +nextBtn.dataset.n; drawMap(); begin(); };

    // ---- 闯关地图：一个世界一页
    const map = start.querySelector('.pl-map'), stages = data.stages;
    sh.stage = stages ? stages.upto : null;
    let page = stages ? Math.ceil(stages.upto / 6) - 1 : 0;
    function drawMap() {
      if (!map || !stages) return;
      page = Math.max(0, Math.min(stages.worlds.length - 1, page));
      const w = stages.worlds[page];
      map.hidden = false; start.classList.add('has-map');
      map.innerHTML = `<div class="pm-head"><button type="button" class="pm-arr" data-d="-1" ${page ? '' : 'disabled'} aria-label="上一个世界">‹</button>
          <div class="pm-name"><b>世界 ${w.n} · ${esc(w.name)}</b><small>⭐ ${w.stars}/18 · 一共 ⭐ ${stages.stars}/${stages.max_stars}</small></div>
          <button type="button" class="pm-arr" data-d="1" ${page < stages.worlds.length - 1 ? '' : 'disabled'} aria-label="下一个世界">›</button></div>
        <div class="pm-row pm-${w.scene}">${w.levels.map(l => `<button type="button" class="pm-lv${l.boss ? ' boss' : ''}${l.open ? '' : ' lock'}${l.n === sh.stage ? ' on' : ''}${l.cleared ? ' done' : ''}" data-n="${l.n}" ${l.open ? '' : 'disabled'}>
            <b>${l.boss ? '👑' : l.open ? l.n : '🔒'}</b><span>${l.open ? '★★★'.slice(0, l.stars).padEnd(3, '☆') : ''}</span></button>`).join('<i class="pm-path"></i>')}</div>
        <div class="pm-gift${w.done ? ' got' : ''}">${w.unlock.icon} ${w.done ? `已解锁：${esc(w.unlock.name)}` : `打败第 ${w.n * 6} 关的大怪兽，解锁 <b>${esc(w.unlock.name)}</b>（${esc(w.unlock.how)}）`}</div>`;
      map.querySelectorAll('.pm-arr').forEach(b => b.onclick = () => { page += +b.dataset.d; Play.sfx.play('click'); drawMap(); });
      map.querySelectorAll('.pm-lv:not(.lock)').forEach(b => b.onclick = () => { sh.stage = +b.dataset.n; Play.sfx.play('click'); drawMap(); });
      go.firstChild.textContent = `开始第 ${sh.stage} 关！`;
    }
    drawMap();

    async function begin() {
      Play.sfx.unlock(); Play.sfx.play('click');
      start.hidden = true; end.hidden = true;
      try {
        sh.match = await api('/api/arena/start', {game: data.game, src: sh.src, stage: sh.stage});
      } catch (e) {
        end.hidden = false; end.querySelector('.pl-sum').innerHTML = `<div class="pl-big">🙈</div><p class="err">${esc(e.message)}</p>`;
        end.querySelector('.pl-again').hidden = true; return;
      }
      if (sh.quiz) sh.quiz.destroy();
      sh.quiz = new ArenaQuiz({matchId: sh.match.match_id, dock: game.dock ? sh.dock : null,
        onAnswer: r => game.onAnswer && game.onAnswer(r, sh), onXP: r => game.onXP && game.onXP(r, sh),
        onOpen: () => game.onQuizOpen && game.onQuizOpen(sh), onClose: () => game.onQuizClose && game.onQuizClose(sh)});
      if (game.dock) { sh.dock.hidden = false; sh.quiz.el.classList.add('wait'); }
      root.classList.add('playing');
      Play.ui.countdown($('.pl-cd'), () => {
        sh.over = false;
        Play.music.start(game.tempo);
        if (game.dock) { sh.quiz.el.classList.remove('wait'); sh.quiz.load(); }
        game.begin(sh.match, sh.quiz, sh);
      });
    }

    sh.finish = async function (result, stats, title) {
      if (sh.over) return;
      sh.over = true;
      Play.music.stop();
      if (sh.quiz) { sh.quiz.stop(); if (game.dock) sh.quiz.el.classList.add('wait'); }
      root.classList.remove('playing');
      Play.sfx.play(result === 'win' ? 'win' : result === 'lose' ? 'lose' : 'pop');
      let r;
      try { r = await api(`/api/arena/${sh.match.match_id}/end`, {result, stats: stats || {}}); }
      catch (e) { r = {result, answered: 0, right: 0, accuracy: 0, xp: 0, tips: [e.message], topics: [], stickers: [], seconds_left: 0}; }
      const head = title || {win: '🏆 你赢了！', lose: '💪 差一点！再来一局', draw: '🤝 平局', quit: '这局结束了'}[result];
      const lv = r.level, sg = r.stage;
      end.querySelector('.pl-sum').innerHTML = `<div class="pl-title ${result}">${head}</div>
        ${sg ? stageHtml(sg) : ''}
        <div class="pl-stats"><div class="focus" title="实际答对 ÷ 按你的水平预期答对 × 100。100 是正常发挥，越高越投入"><b>${r.focus ?? '—'}</b><span>💎 专注指数</span></div><div><b>${r.right}/${r.answered}</b><span>答对 / 答题</span></div><div><b>${r.best_streak || 0}</b><span>最长连对</span></div></div>
        ${r.topics && r.topics.length ? `<div class="pl-topics">这局练了：${r.topics.map(t => `<span>${esc(t.topic)} <b>${t.right}/${t.n}</b></span>`).join('')}</div>` : ''}
        ${r.tips.length ? `<ul class="pl-tips">${r.tips.map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
        ${lv ? `<div class="pl-lv"><span>乐园 Lv.${lv.level}</span><i><b style="width:${lv.pct}%"></b></i><small>再答对 ${lv.to_next} 题升级</small></div>` : ''}
        <p class="pl-left">今天还能玩 ${Math.floor((r.seconds_left || 0) / 60)} 分钟</p>`;
      const tooLate = (r.seconds_left || 0) < 30;
      end.querySelector('.pl-again').hidden = tooLate;
      end.querySelector('.pl-again').textContent = sg ? (sg.stars ? '再打一次' : '再试一次') : '再来一局';
      if (nextBtn) { nextBtn.hidden = tooLate || !(sg && sg.next); if (sg && sg.next) { nextBtn.dataset.n = sg.next; nextBtn.textContent = `下一关 →`; } }
      if (sg) updateStages(sg);
      end.hidden = false;
      if (sg && sg.stars) Play.sfx.play('star');
      if (r.stickers && r.stickers.length) showStickers(r.stickers);
    };

    function stageHtml(sg) {
      const stars = [0, 1, 2].map(i => `<span class="${i < sg.stars ? 'on' : ''}" style="animation-delay:${0.25 + i * 0.3}s">★</span>`).join('');
      const why = sg.stars ? '' : '<p class="small">打赢电脑就能拿第 1 颗星。能量快没了就去答题！</p>';
      return `<div class="pl-stage-res"><div class="ps-n">第 ${sg.n} 关</div><div class="ps-stars">${stars}</div>
        <div class="ps-how">${[['win', '过关'], ['focus', '专注指数 90+'], ['hp', '血量剩一半']].map(([k, t]) => `<span class="${sg.checks[k] ? 'ok' : ''}">${sg.checks[k] ? '✓' : '·'} ${t}</span>`).join('')}</div>
        ${sg.new_best && sg.best > 0 ? '<div class="ps-best">新纪录！</div>' : ''}${why}
        ${sg.unlocked ? `<div class="ps-unlock">${sg.unlocked.icon} 解锁了 <b>${esc(sg.unlocked.name)}</b>：${esc(sg.unlocked.how)}</div>` : ''}
        ${sg.all_clear && sg.first_clear ? '<div class="ps-unlock">🥋 30 关全部打通！你是火柴人大师！</div>' : ''}</div>`;
    }
    function updateStages(sg) {   // 战报回来后，地图上的星星和能打到第几关跟着更新
      if (!stages) return;
      for (const w of stages.worlds) for (const l of w.levels) {
        if (l.n === sg.n) { stages.stars += Math.max(0, sg.best - l.stars); w.stars += Math.max(0, sg.best - l.stars); l.stars = sg.best; l.cleared = l.cleared || sg.stars > 0; }
        if (sg.next && l.n === sg.next) l.open = true;
      }
      if (sg.unlocked) stages.worlds[Math.floor((sg.n - 1) / 6)].done = true;
      if (sg.next) { stages.upto = Math.max(stages.upto, sg.next); sh.stage = sg.next; page = Math.floor((sg.next - 1) / 6); }
      drawMap();
    }

    function showStickers(list) {
      const pop = document.createElement('div'); pop.className = 'pl-sticker-pop';
      pop.innerHTML = `<div class="pl-sticker-card"><div class="pl-label">得到新贴纸！</div>${list.map(s =>
        `<div class="pl-sticker"><span class="ico">${s.icon}</span><b>${esc(s.name)}</b><small>${esc(s.how)}</small>${s.avatar ? '<em>解锁了新角色，回乐园首页换上吧</em>' : ''}</div>`).join('')}
        <button type="button" class="btn lg block">太棒了！</button></div>`;
      root.appendChild(pop); Play.sfx.play('sticker');
      pop.querySelector('button').onclick = () => pop.remove();
    }

    if (game.setup) game.setup(sh);
    return sh;
  };

  function localStorageGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 隐私模式 */ } }
})();

/* 联网游戏的外壳（所有联网游戏共用）：大厅（家里谁在等我、开房间）、房间（两个人、选角色、选关）、开局同步、
   倒下答题复活、两个人的状态条、表情、过关结算。游戏只写玩法：
     MpShell.init({
       id: 'duo', tempo: 120, rules: ['……'], keys: '电脑键盘：……',
       input: {left: ['ArrowLeft', 'a'], …},          // 键盘
       touch: [['left', '◀'], ['right', '▶'], ['jump', '⤒', 'big']],   // 触屏按钮（不写就没有）
       preview(sh, lv) {},                             // 大厅 / 房间里：画这一关的样子
       begin(ctx) {},                                  // 倒计时结束：开始玩这一关
       end() {},                                       // 这一关结束（过关、失败、有人退出）：停掉循环
       onRevive(info) {},                              // 答够题复活了（info.rescued：被队友救的）
       save() {},                                      // 我自己的状态（位置、长度、道具……），每 2 秒存到服务器
       saveWorld() {},                                 // 共享的世界（砖块、豆子、怪兽、宝石……）：开房间的存档用，也发给重连的人
       loadWorld(w) {},                                // 换成这份世界（重连时）
       peerView() {},                                  // 我这边看到的队友（位置、长度……）：队友重连时发给他
       loadMe(v) {},                                   // 队友那边看到的我：重连时用它放回自己（比存档新）
     });
   刷新页面、换一台设备进来：服务器有这一关的存档（app/arena/mp/rooms.py 的 save_play），begin 时 ctx.resume = {me, world}，
   游戏据此先还原自己和世界（不要从头开始）。对方还在玩的话，以对方那里的实时状态为准：外壳向对方要一份
   （need_world → world），换成对方的世界（loadWorld）、按对方看到的我放回自己（loadMe）。存档只在两个人都掉线时兜底，
   以及只有我自己知道的东西（还欠几道题、倒下几次、泡泡堂的道具）。
   同一个孩子在两台设备上：后打开的那台问「在这台设备上接着玩吗？」，点了才把原来那台换下来（take）。
   ctx（begin 时给游戏的）：
     ctx.net            连接（MpNet，net.js）：ctx.net.on('s', …)、ctx.net.send({…})
     ctx.me / ctx.peer  {id, name, avatar, role}；ctx.host：我是不是开房间的（共享的东西由他算）
     ctx.level          这一关的数据（app/arena/mp/levels/<游戏>/NN.json）；ctx.seed：两台设备一样的随机种子
     ctx.die()          我倒下了：弹出答题（题按我的水平出），答够了调游戏的 onRevive。欠题规则：
                        要答对 1 道；答错多欠 1 道（最多欠 3 道）；点「这题不会」不加，看懂讲解再答下一道。
     ctx.rescue()       队友救了我（泡泡堂）：不用再答题，也不算倒下
     ctx.reviving       我正在答题；ctx.peerNeed：队友还欠几道（0 = 在玩）
     ctx.finish(report) 开房间的人报过关：{seconds, deaths, score, gems…}，服务器记纪录、算星星，两边一起出结算
     ctx.lose(why)      没过关（时间到了这类），两边一起结束
     ctx.roleIndex(r)   角色是第几个（0 / 1，对应关卡里的 1 / 2 号出生点）
     ctx.elapsed()      这一关玩了多少秒；ctx.deaths()：两个人一共倒下几次；ctx.toast(文字, 'good' | 'warn')
   出题判分是乐园的接口（/api/arena/…，game=游戏 id，不冷冻）；房间和过关是 /api/mp/…（app/arena/mp/rooms.py）。 */
(function () {
  const MpShell = window.MpShell = {};
  const root = document.getElementById('pl');
  const $ = s => root.querySelector(s);
  const EMOTES = ['👍', '⏳', '🙌', '❤️', '❓'];
  const EMOTE_TEXT = {'👍': '好的', '⏳': '等等我', '🙌': '太棒了', '❤️': '加油', '❓': '怎么走？'};

  MpShell.init = function (def) {
    const data = JSON.parse(root.dataset.info || '{}');
    const G = data.lobby.game, LV = data.lobby.levels;
    const sh = {root, def, data, canvas: $('canvas'), hud: $('.pl-hud'), ctrl: $('.pl-ctrl')};
    const panel = $('.pl-start .pl-panel'), over = $('.pl-start');
    sh.toast = (t, k) => Play.ui.toast($('.pl-toast'), t, k);
    Play.ui.soundButtons(root, def.tempo);
    if (def.input) Play.input.init(def.input);

    let lobby = data.lobby, room = null, net = null, peerOn = false, state = 'lobby', page = 0, mapSeen = 0, poll = 0;
    let ctx = null, match = null, quiz = null, playing = 0, t0 = 0, myDeaths = 0, peerDeaths = 0, byeTimer = 0;
    let saveTimer = 0, otherMe = null, peerPlaying = 0, resuming = false, gone = false;
    const stageEl = $('.pl-stage');
    const ROLES = Object.fromEntries(G.roles.map(r => [r.id, r]));   // 角色按登记的顺序：第一个用关卡里的 1 号出生点
    const roleOf = r => ROLES[r] || {name: r, icon: ''};
    const lvAt = n => LV[n - 1];

    // ---------------------------------------------------------------- 预览（大厅、房间里画选中的那一关）
    let previewN = (lobby.rooms.find(r => r.mine || r.joined) || {}).level || 1;
    function preview() { if (!playing && def.preview) def.preview(sh, lvAt(previewN) || LV[0]); }
    const st = Play.stage(sh.canvas, {height: 450, minW: 640, maxW: 1400});
    sh.stage = st;
    st.onResize = () => preview();

    // ---------------------------------------------------------------- 大厅
    function showLobby() {
      state = 'lobby'; over.hidden = false; room = null;
      if (net) { net.close(); net = null; }
      const sibs = lobby.siblings, waiting = lobby.rooms.filter(r => !r.mine && r.host);
      let h = `<div class="pl-title">${esc(data.g.icon)} ${esc(data.g.name)}</div>`;
      if (!sibs.length) {
        h += `<p class="mp-note">这是两个人一起玩的游戏。家里还没有别的小伙伴的账号，请爸爸妈妈在家长页添加兄弟姐妹。</p>`;
      } else {
        if (waiting.length) h += waiting.map(r => `<div class="mp-invite"><canvas class="mp-face" data-av="${esc(r.host.avatar)}"></canvas>
            <div class="grow"><b>${esc(r.host.name)}</b> 开了房间在等你<small>${roleOf(r.host_role).icon} ${esc(roleOf(r.host_role).name)} · 第 ${r.level} 关</small></div>
            <button type="button" class="btn lg pl-go mp-join" data-id="${r.id}">加入！</button></div>`).join('');
        h += `<div class="pl-label">${waiting.length ? '或者自己开一个房间，' : ''}我想当：</div>
          <div class="mp-roles">${G.roles.map(v => `<button type="button" class="mp-role" data-role="${v.id}"><span>${v.icon}</span>${esc(v.name)}</button>`).join('')}</div>
          <p class="mp-hint">开好房间后，让${sibs.map(s => esc(s.name)).join('、')}在自己的平板或电脑上打开 乐园 → ${esc(data.g.name)}，点「加入」。</p>`;
      }
      if (lobby.locked) h += `<p class="pl-locked">${esc(lobby.locked)}</p>`;
      h += `<details class="pl-how"><summary>怎么玩</summary><ul class="pl-rules">${def.rules.map(r => `<li>${r}</li>`).join('')}</ul></details><p class="pl-keys">${esc(def.keys || '')}</p>`;
      panel.innerHTML = h;
      faces(panel);
      panel.querySelectorAll('.mp-join').forEach(b => b.onclick = () => enter(api(`/api/mp/rooms/${b.dataset.id}/join`, {})));
      panel.querySelectorAll('.mp-role').forEach(b => b.onclick = () => enter(api('/api/mp/rooms', {game: G.id, role: b.dataset.role})));
      clearInterval(poll);
      poll = setInterval(async () => {   // 家里有人开房间了就显示出来
        if (state !== 'lobby') return;
        try { const l = await (await fetch(`/api/mp/${G.id}/lobby`)).json(); if (JSON.stringify(l.rooms) !== JSON.stringify(lobby.rooms)) { lobby = l; showLobby(); } } catch (e) { /* 等下次 */ }
      }, 3000);
      preview();
    }
    function faces(el) { el.querySelectorAll('canvas[data-av]').forEach(c => { c.width = 96; c.height = 120; Art.portrait(c, c.dataset.av, 'win'); }); }

    async function enter(p) {
      Play.sfx.unlock(); Play.sfx.play('click');
      try { room = await p; } catch (e) { sh.toast(e.message, 'warn'); return; }
      clearInterval(poll);
      connect();
      showRoom();
    }

    // ---------------------------------------------------------------- 连接
    function connect() {
      net = new MpNet(room.id);
      net.onStatus = (ok, why) => {
        if (ok) { net.send({t: 'here', playing}); refresh(); }
        if (!ok && why === 'ws') sh.toast('连不上联网服务。请爸爸妈妈看看服务器的 WebSocket 设置（docs/DEPLOY.md）', 'warn');
        if (!ok && why && why !== 'ws') { sh.toast(why, 'warn'); backToLobby(); }
        drawRoomBits();
      };
      net.on('hello', m => {
        if (m.from === data.me.id) { net.send({t: 'here', playing, self: 1}); return; }   // 我在另一台设备上打开了
        peerSeen(m); net.send({t: 'here', playing}); if (!playing) refresh();
      });
      net.on('here', m => {
        if (m.from === data.me.id) { if (m.self) otherMe = {playing: m.playing || 0}; return; }
        peerSeen(m); peerPlaying = m.playing || 0;
        if (!playing && m.playing && (state === 'room' || state === 'end')) rejoin();   // 对方还在玩：我回到这一关
        else if (!playing) refresh();
      });
      net.on('take', m => { if (m.from === data.me.id) takenAway(); });
      net.on('need_world', m => {
        if (isPeer(m) && playing && def.saveWorld) net.send({t: 'world', n: playing, el: +ctx.elapsed().toFixed(1), w: def.saveWorld(), you: def.peerView ? def.peerView() : null});
      });
      net.on('world', m => {
        if (!isPeer(m) || !playing || m.n !== playing || !ctx) return;
        if (def.loadWorld) def.loadWorld(m.w);
        if (m.you && def.loadMe) def.loadMe(m.you);
        if (m.el > ctx.elapsed() + 1) t0 = performance.now() - m.el * 1000;   // 时间跟还在玩的那台对齐
      });
      net.on('bye', m => {
        if (!isPeer(m)) return;
        peerOn = false; drawRoomBits(); hudDraw();
        if (playing) { sh.toast(`${peerName()}掉线了，等一等…`, 'warn'); clearTimeout(byeTimer); byeTimer = setTimeout(() => { if (!peerOn && playing) endOver(`${peerName()}离开了`, true); }, 30000); }
      });
      net.on('*', m => { if (isPeer(m) && !peerOn && m.t !== 'bye') { peerOn = true; clearTimeout(byeTimer); drawRoomBits(); hudDraw(); } });
      net.on('room', () => refresh());
      net.on('start', m => { if (!room.host) startLevel(m.n, m.seed); });
      net.on('cant', m => { sh.toast(`${peerName()}：${m.why}`, 'warn'); if (playing) endOver(m.why, true); });
      net.on('rv', m => {
        if (!isPeer(m)) return;
        if (ctx) { ctx.peerNeed = m.need; if (m.fresh) peerDeaths++; if (m.rescued) peerDeaths = Math.max(0, peerDeaths - 1); }
        hudDraw();
      });
      net.on('emote', m => { if (isPeer(m) && EMOTES.includes(m.e)) showEmote(m.e, false); });
      net.on('done', m => { if (!room.host && playing) endDone(m.res); });
      net.on('over', m => { if (playing) endOver(m.why, false); });
    }
    const isPeer = m => room && m.from !== data.me.id && room.members.some(x => x.id === m.from);
    function peerSeen(m) { if (isPeer(m)) { const was = peerOn; peerOn = true; clearTimeout(byeTimer); if (!was) { drawRoomBits(); hudDraw(); } } }
    const peer = () => room && room.members.find(x => x.id !== data.me.id);
    const peerName = () => (peer() || {}).name || '队友';
    async function refresh() {
      if (!room) return;
      try { room = await (await fetch(`/api/mp/rooms/${room.id}`)).json(); } catch (e) { return; }
      if (room.error) { sh.toast(room.error, 'warn'); return backToLobby(); }
      previewN = room.level;
      if (state === 'room') showRoom();
    }
    async function backToLobby() {
      if (room) { try { await api(`/api/mp/rooms/${room.id}/leave`, {}); } catch (e) { /* 房间已经关了 */ } }
      try { lobby = await (await fetch(`/api/mp/${G.id}/lobby`)).json(); } catch (e) { /* 用旧的 */ }
      if (net) { net.send({t: 'room'}); net.close(); net = null; }
      peerOn = false; showLobby();
    }

    // ---------------------------------------------------------------- 房间
    function showRoom() {
      state = 'room'; over.hidden = false;
      const p = peer(), me = room.me, prog = room.progress;
      const card = (m, label) => m ? `<div class="mp-mem${m.id === data.me.id ? ' me' : ''}"><canvas class="mp-face" data-av="${esc(m.avatar)}"></canvas>
          <b>${esc(m.name)}</b><span class="mp-r">${roleOf(m.role).icon} ${esc(roleOf(m.role).name)}</span>
          ${m.id === data.me.id ? '<small class="mp-dot on">我</small>' : `<small class="mp-dot" data-peer-dot>…</small>`}
          ${m.locked ? `<small class="mp-lock">🔒 ${esc(m.locked)}</small>` : ''}</div>`
        : `<div class="mp-mem empty"><div class="mp-wait">⏳</div><b>等${esc(label)}进来</b><small>让${esc(label)}在自己的设备上打开 乐园 → ${esc(data.g.name)}</small></div>`;
      const sibNames = lobby.siblings.map(s => s.name).join('或');
      let h = `<div class="pl-title">${esc(data.g.icon)} ${esc(data.g.name)}</div>
        <div class="mp-mems">${card(room.members[0])}<span class="mp-amp">＋</span>${card(room.members[1], sibNames || '队友')}</div>`;
      if (room.host) h += `<button type="button" class="btn sm ghost mp-swap">🔁 换角色</button>`;
      if (prog) h += mapHtml(prog);
      const lv = lvAt(room.level);
      if (lv && lv.tip) h += `<p class="mp-tip">💡 ${esc(lv.tip)}</p>`;
      const locked = room.members.find(m => m.locked);
      if (room.host) h += `<button type="button" class="btn lg block pl-go mp-start" ${p && !locked ? '' : 'disabled'}>开始第 ${room.level} 关！</button>`;
      else h += `<p class="mp-note">等 <b>${esc(room.members[0].name)}</b> 选关、点开始。</p>`;
      h += `<p class="pl-locked">${locked ? esc(`${locked.name}：${locked.locked}`) : ''}</p>
        <div class="mp-foot"><button type="button" class="btn sm ghost mp-leave">退出房间</button></div>`;
      panel.innerHTML = h;
      faces(panel); drawRoomBits();
      const sw = panel.querySelector('.mp-swap');
      if (sw) sw.onclick = async () => {
        const other = G.roles.map(r => r.id).find(r => r !== me.role);
        try { room = await api(`/api/mp/rooms/${room.id}/role`, {role: other}); net.send({t: 'room'}); showRoom(); } catch (e) { sh.toast(e.message, 'warn'); }
      };
      panel.querySelectorAll('.pm-arr').forEach(b => b.onclick = () => { page += +b.dataset.d; Play.sfx.play('click'); showRoom(); });
      if (room.host) panel.querySelectorAll('.pm-lv:not(.lock)').forEach(b => b.onclick = async () => {
        Play.sfx.play('click');
        try { room = await api(`/api/mp/rooms/${room.id}/level`, {n: +b.dataset.n}); previewN = room.level; net.send({t: 'room'}); showRoom(); preview(); } catch (e) { sh.toast(e.message, 'warn'); }
      });
      const go = panel.querySelector('.mp-start');
      if (go) go.onclick = () => {
        if (!peerOn) { sh.toast(`${peerName()}还没连上`, 'warn'); return; }
        const seed = Math.floor(Math.random() * 1e9);
        net.send({t: 'start', n: room.level, seed});
        startLevel(room.level, seed);
      };
      panel.querySelector('.mp-leave').onclick = () => backToLobby();
      preview();
    }
    function drawRoomBits() {
      const d = panel.querySelector('[data-peer-dot]');
      if (d) { d.textContent = peerOn ? '在线' : '还没连上'; d.classList.toggle('on', peerOn); }
      const go = panel.querySelector('.mp-start');
      if (go && room) go.disabled = !(peer() && peerOn && !room.members.find(m => m.locked));
    }
    function mapHtml(prog) {
      const worlds = [...new Set(LV.map(l => l.world.n))];
      if (mapSeen !== room.level) { page = Math.max(0, worlds.indexOf(lvAt(room.level).world.n)); mapSeen = room.level; }
      page = Math.max(0, Math.min(worlds.length - 1, page));
      const wn = worlds[page], ls = prog.levels.filter(l => l.world === wn), wl = LV.find(l => l.world.n === wn).world;
      return `<div class="pl-map mp-map"><div class="pm-head"><button type="button" class="pm-arr" data-d="-1" ${page ? '' : 'disabled'} aria-label="上一个世界">‹</button>
          <div class="pm-name"><b>${wl.name ? `世界 ${wn} · ${esc(wl.name)}` : '关卡'}</b><small>我们俩一共 ⭐ ${prog.stars}/${prog.max_stars}</small></div>
          <button type="button" class="pm-arr" data-d="1" ${page < worlds.length - 1 ? '' : 'disabled'} aria-label="下一个世界">›</button></div>
        <div class="pm-row">${ls.map(l => `<button type="button" class="pm-lv${l.open ? '' : ' lock'}${l.n === room.level ? ' on' : ''}${l.stars ? ' done' : ''}" data-n="${l.n}" ${l.open && room.host ? '' : 'disabled'} title="${esc(l.name)}">
            <b>${l.open ? l.n : '🔒'}</b><span>${l.open ? '★★★'.slice(0, l.stars).padEnd(3, '☆') : ''}</span></button>`).join('<i class="pm-path"></i>')}</div>
        <div class="mp-lvname">第 ${room.level} 关 · ${esc(lvAt(room.level).name)}${bestText(prog.levels[room.level - 1])}</div></div>`;
    }
    const bestText = l => !l || !l.best ? '' : G.lower_is_better ? ` · 我们的纪录 ${fmtS(l.best)}` : ` · 我们的纪录 ${l.best} 分`;
    const fmtS = s => s >= 60 ? `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒` : `${(+s).toFixed(1)} 秒`;

    // ---------------------------------------------------------------- 开局
    async function startLevel(n, seed, resume) {
      if (playing || gone || state === 'take') return;
      Play.sfx.unlock(); Play.sfx.play('click');
      try { match = resume && resume.match ? {match_id: resume.match} : await api('/api/arena/start', {game: G.id}); }
      catch (e) { sh.toast(e.message, 'warn'); net.send({t: 'cant', why: e.message}); return; }
      playing = n; previewN = n; state = 'play'; over.hidden = true; $('.pl-end').hidden = true;
      net.send({t: 'here', playing});
      if (quiz) quiz.destroy();
      quiz = new ArenaQuiz({matchId: match.match_id, sticky: true, autoNext: true, onAnswer: onAnswer, mount: stageEl});
      quiz.el.querySelector('.aq-card').insertAdjacentHTML('afterbegin', '<div class="mp-debt"></div>');
      myDeaths = resume ? resume.deaths || 0 : 0; peerDeaths = resume ? resume.peer_deaths || 0 : 0;
      const p = peer(), me = room.members.find(m => m.id === data.me.id);
      ctx = {net, me, peer: p, host: room.host, level: lvAt(n), seed, sh, reviving: false, need: 0, peerNeed: 0,
             resume: resume ? {me: resume.me || null, world: resume.world || null} : null,
             die, rescue, finish, lose, toast: sh.toast, elapsed: () => t0 ? (performance.now() - t0) / 1000 : 0,
             deaths: () => myDeaths + peerDeaths, roleName: r => roleOf(r).name, roleIndex: r => G.roles.findIndex(x => x.id === r)};
      root.classList.add('playing');
      buildCtrl(); hudDraw();
      const go = () => {
        if (playing !== n) return;
        t0 = performance.now() - (resume ? (resume.el || 0) * 1000 : 0);
        Play.music.start(def.tempo);
        def.begin(ctx);
        clearInterval(saveTimer); saveTimer = setInterval(save, 2000); save();
        if (resume) {
          net.send({t: 'need_world'});
          if (resume.need > 0) { ctx.reviving = true; ctx.need = resume.need; net.send({t: 'rv', need: ctx.need}); Play.input.blocked = true; debtText(); hudDraw(); askNow(); }
          sh.toast('接着玩！', 'good');
        }
      };
      if (resume) go(); else Play.ui.countdown($('.pl-cd'), go);
    }

    // ---------------------------------------------------------------- 存档、重连、换设备
    function save(end) {
      if (!net || !ctx) return;
      if (end) { net.send({t: 'save', end: 1, n: playing, seed: ctx.seed}); return; }
      if (!playing) return;
      const msg = {t: 'save', n: playing, seed: ctx.seed, el: +ctx.elapsed().toFixed(1), need: ctx.need, deaths: myDeaths, match: match && match.match_id};
      try { msg.me = def.save ? def.save() : null; if (room.host && def.saveWorld) msg.w = def.saveWorld(); } catch (e) { /* 游戏还没准备好 */ }
      net.send(msg);
    }
    addEventListener('pagehide', () => save());
    document.addEventListener('visibilitychange', () => { if (document.hidden) save(); });
    async function rejoin() {   // 回到正在玩的这一关（刷新了、换了设备、对方还在玩）
      if (resuming || playing || gone) return;
      resuming = true;
      try {
        const r = await (await fetch(`/api/mp/rooms/${room.id}`)).json();
        if (r.error) { resuming = false; sh.toast(r.error, 'warn'); return backToLobby(); }
        room = r;
        if (room.play) await startLevel(room.play.n, room.play.seed, room.play);
        if (!playing && !gone) showRoom();
      } catch (e) { /* 等下次 */ }
      resuming = false;
    }
    function askTake(n) {   // 同一个账号在另一台设备上正在玩
      state = 'take'; over.hidden = false;
      panel.innerHTML = `<div class="pl-title">📱 换到这台设备？</div>
        <p>你在另一台设备上正在玩${n ? `第 ${n} 关` : ''}。</p>
        <button type="button" class="btn lg block pl-go mp-take">在这台设备上接着玩</button>
        <button type="button" class="btn ghost block mp-keep" style="margin-top:8px">不了，接着用那台玩</button>`;
      panel.querySelector('.mp-take').onclick = () => {
        net.send({t: 'take'});
        panel.innerHTML = '<div class="pl-title">正在换过来…</div>';
        setTimeout(() => { otherMe = null; state = 'room'; rejoin(); }, 1200);   // 等那台把最后的进度存好
      };
      panel.querySelector('.mp-keep').onclick = () => { gone = true; if (net) { net.close(); net = null; } showGone('好的，接着在另一台设备上玩吧。'); };
    }
    function takenAway() {   // 我在另一台设备上点了「在这里接着玩」：这台停下来（进度先存好，不算结束）
      save(); gone = true;
      if (def.end) def.end();
      Play.music.stop(); Play.input.blocked = false; Play.input.clear();
      if (quiz) quiz.stop();
      clearInterval(saveTimer); root.classList.remove('playing'); sh.ctrl.innerHTML = ''; sh.hud.innerHTML = '';
      playing = 0; t0 = 0; $('.pl-end').hidden = true;
      setTimeout(() => { if (net) { net.close(); net = null; } }, 300);
      showGone('已经换到另一台设备上玩了。');
    }
    function showGone(text) {
      state = 'gone'; over.hidden = false;
      panel.innerHTML = `<div class="pl-title">👋 ${esc(text)}</div><button type="button" class="btn lg block pl-go mp-here">回到这台设备上玩</button>`;
      panel.querySelector('.mp-here').onclick = () => location.reload();
    }

    // ---------------------------------------------------------------- 倒下答题复活
    function die() {
      if (!ctx || ctx.reviving) return;
      ctx.reviving = true; ctx.need = 1; myDeaths++;
      net.send({t: 'rv', need: 1, fresh: true});
      Play.input.clear(); Play.input.blocked = true;
      debtText(); hudDraw();
      setTimeout(askNow, 600);
    }
    function askNow() {   // 题目盖在游戏画面上，答对之前一直在（关不掉）
      if (!ctx || !ctx.reviving || !quiz) return;
      quiz.open();
      const r = stageEl.getBoundingClientRect();
      if (r.top < 0 || r.bottom > innerHeight) stageEl.scrollIntoView({block: 'center', behavior: 'smooth'});
    }
    setInterval(() => { if (playing && ctx && ctx.reviving && quiz && !quiz.isOpen) askNow(); }, 1000);   // 万一题目没弹出来
    function onAnswer(res) {
      if (!ctx || !ctx.reviving) return;
      if (res.correct) ctx.need--;
      else if (!res.dont_know) ctx.need = Math.min(3, ctx.need + 1);
      net.send({t: 'rv', need: ctx.need});
      debtText(res); hudDraw();
      if (ctx.need <= 0) setTimeout(() => revived(false), 650);
    }
    function debtText(res) {
      const el = quiz && quiz.el.querySelector('.mp-debt'); if (!el) return;
      el.innerHTML = ctx.need <= 0 ? '🎉 复活啦！' : `${res && !res.correct && !res.dont_know ? '答错了，多欠 1 道。' : ''}再答对 <b>${ctx.need}</b> 道就复活`;
    }
    function revived(rescued) {
      if (!ctx || !ctx.reviving) return;
      ctx.reviving = false; ctx.need = 0;
      quiz.close(true); quiz.item = null;
      Play.input.blocked = false; Play.input.clear();
      net.send({t: 'rv', need: 0, rescued});
      if (rescued) myDeaths = Math.max(0, myDeaths - 1);
      hudDraw();
      def.onRevive && def.onRevive({rescued});
    }
    function rescue() { revived(true); }

    // ---------------------------------------------------------------- 状态条、表情、触屏按钮
    function hudDraw() {
      if (!room || !playing) { sh.hud.innerHTML = ''; return; }
      const chip = (m, mine) => {
        const need = mine ? (ctx && ctx.need) : (ctx && ctx.peerNeed);
        const off = !mine && !peerOn;
        return `<div class="mp-chip${mine ? ' me' : ''}${need ? ' dead' : ''}${off ? ' off' : ''}" data-id="${m.id}"><span>${roleOf(m.role).icon}</span><b>${esc(m.name)}</b>
          <small>${off ? '掉线了' : need ? `答题中 · 还要对 ${need} 道` : '在玩'}</small><i class="mp-emote"></i></div>`;
      };
      const me = room.members.find(m => m.id === data.me.id), p = peer();
      sh.hud.innerHTML = `<div class="mp-chips">${chip(me, true)}${p ? chip(p, false) : ''}</div><div class="grow"></div><div class="mp-clock"></div>`;
    }
    sh.hudClock = text => { const c = sh.hud.querySelector('.mp-clock'); if (c) c.textContent = text; };
    function showEmote(e, mine) {
      const id = mine ? data.me.id : (peer() || {}).id;
      const el = sh.hud.querySelector(`.mp-chip[data-id="${id}"] .mp-emote`);
      if (el) { el.textContent = `${e} ${EMOTE_TEXT[e]}`; el.classList.remove('on'); void el.offsetWidth; el.classList.add('on'); }
      Play.sfx.play('pop');
    }
    function buildCtrl() {
      const touch = (def.touch || []).map(([k, label, cls]) => `<button type="button" class="pl-btn ${cls || ''}" data-k="${k}">${label}</button>`);
      const half = Math.ceil(touch.length / 2);
      sh.ctrl.innerHTML = `<div class="pl-pad mp-touch">${touch.slice(0, def.touchSplit ?? half).join('')}</div>
        <div class="mp-emotes">${EMOTES.map(e => `<button type="button" class="mp-em" data-e="${e}" title="${EMOTE_TEXT[e]}">${e}</button>`).join('')}</div>
        <div class="pl-pad mp-touch">${touch.slice(def.touchSplit ?? half).join('')}</div>`;
      sh.ctrl.querySelectorAll('[data-k]').forEach(b => Play.input.hold(b, b.dataset.k));
      sh.ctrl.querySelectorAll('.mp-em').forEach(b => b.onclick = () => { net.send({t: 'emote', e: b.dataset.e}); showEmote(b.dataset.e, true); });
      if (!matchMedia('(pointer:coarse)').matches) sh.ctrl.querySelectorAll('.mp-touch').forEach(x => x.classList.add('mouse'));
    }

    // ---------------------------------------------------------------- 结束
    async function finish(report) {
      if (!ctx || !room.host || ctx.finishing) return;
      ctx.finishing = true;
      const rep = Object.assign({seconds: +ctx.elapsed().toFixed(1), deaths: ctx.deaths()}, report || {});
      let res;
      try { res = await api(`/api/mp/rooms/${room.id}/finish`, {n: playing, report: rep}); }
      catch (e) { res = {error: e.message, n: playing, ...rep, stars: 0, checks: {}}; }
      net.send({t: 'done', res});
      endDone(res);
    }
    function lose(why) { if (!ctx) return; net.send({t: 'over', why}); endOver(why, false, 'lose'); }
    function stopPlay() {
      save(true); clearInterval(saveTimer);
      if (def.end) def.end();
      Play.music.stop(); Play.input.blocked = false; Play.input.clear();
      if (quiz) { quiz.stop(); }
      root.classList.remove('playing');
      sh.ctrl.innerHTML = ''; sh.hud.innerHTML = '';
      const n = playing; playing = 0; t0 = 0;
      net && net.send({t: 'here', playing: 0});
      return n;
    }
    async function myEnd(result) {
      try { return await api(`/api/arena/${match.match_id}/end`, {result, stats: {}}); } catch (e) { return null; }
    }
    async function endDone(res) {
      if (!playing) return;
      const n = stopPlay();
      Play.sfx.play('win');
      const mine = await myEnd('win');
      const lv = lvAt(n), stars = G.stars.map((s, i) => `<span class="${res.checks && res.checks[s.key] ? 'on' : ''}" style="animation-delay:${0.25 + i * 0.3}s">★</span>`).join('');
      const how = G.stars.map(s => `<span class="${res.checks && res.checks[s.key] ? 'ok' : ''}">${res.checks && res.checks[s.key] ? '✓' : '·'} ${esc(starLabel(s, lv))}</span>`).join('');
      const value = G.lower_is_better ? fmtS(res.seconds) : `${res.score} 分`;
      let h = `<div class="pl-title win">🎉 我们一起过关了！</div>
        <div class="pl-stage-res"><div class="ps-n">第 ${n} 关 · ${esc(lv.name)}</div><div class="ps-stars">${stars}</div><div class="ps-how">${how}</div>
        <div class="mp-time">${G.lower_is_better ? '⏱' : '🏅'} ${value}${res.new_best && res.prev_best ? ' <b class="ps-best">新纪录！</b>' : res.prev_best ? `<small>（我们的纪录 ${G.lower_is_better ? fmtS(res.best) : res.best + ' 分'}）</small>` : ''}</div></div>`;
      if (res.error) h += `<p class="err">${esc(res.error)}</p>`;
      h += personal(mine);
      showEnd(h, res.next, n);
      const st = [].concat((res.stickers || {})[String(data.me.id)] || [], (mine && mine.stickers) || []);
      if (st.length) stickers(st);
      if (res.progress && room) room.progress = res.progress;
    }
    async function endOver(why, quit, result) {
      if (!playing) return;
      const n = stopPlay();
      Play.sfx.play(quit ? 'pop' : 'lose');
      const mine = await myEnd(result || 'quit');
      showEnd(`<div class="pl-title lose">${quit ? '这一关先停下了' : '💪 差一点！'}</div><p>${esc(why || '')}</p>${personal(mine)}`, null, n, true);
      if (mine && mine.stickers && mine.stickers.length) stickers(mine.stickers);
    }
    function starLabel(s, lv) { return s.key === 'time' && lv.par ? `${lv.par} 秒内` : s.label; }
    function personal(r) {
      if (!r || !r.answered) return '<p class="small muted">这一关我没有倒下，一道题都不用答！</p>';
      return `<div class="pl-stats"><div class="focus" title="100 = 平时的你"><b>${r.focus ?? '—'}</b><span>💎 我的专注值</span></div><div><b>${r.right}/${r.answered}</b><span>我答对 / 答题</span></div></div>
        ${r.tips && r.tips.length ? `<ul class="pl-tips">${r.tips.slice(0, 2).map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}`;
    }
    function showEnd(h, next, n, again) {
      state = 'end';
      const end = $('.pl-end'), sum = end.querySelector('.pl-sum');
      sum.innerHTML = h;
      const row = end.querySelector('.pl-row');
      if (room && room.host) {
        row.innerHTML = `${next ? `<button type="button" class="btn lg pl-next" data-n="${next}">下一关 →</button>` : ''}
          <button type="button" class="btn lg pl-again" data-n="${n}">${again ? '再试一次' : '再玩一次'}</button>
          <button type="button" class="btn ghost lg mp-back">回房间</button>`;
        row.querySelectorAll('[data-n]').forEach(b => b.onclick = async () => {
          if (!peerOn) { sh.toast(`${peerName()}还没连上`, 'warn'); return; }
          const m = +b.dataset.n;
          try { room = await api(`/api/mp/rooms/${room.id}/level`, {n: m}); } catch (e) { sh.toast(e.message, 'warn'); return; }
          const seed = Math.floor(Math.random() * 1e9);
          net.send({t: 'start', n: m, seed}); startLevel(m, seed);
        });
      } else {
        row.innerHTML = `<p class="mp-note grow">等 <b>${esc(room ? room.members[0].name : '')}</b> 开始下一局…</p><button type="button" class="btn ghost lg mp-back">回房间</button>`;
      }
      row.querySelector('.mp-back').onclick = () => { end.hidden = true; state = 'room'; refresh().then(showRoom); };
      end.hidden = false;
    }
    function stickers(list) {
      const pop = document.createElement('div'); pop.className = 'pl-sticker-pop';
      pop.innerHTML = `<div class="pl-sticker-card"><div class="pl-label">得到新贴纸！</div>${list.map(s =>
        `<div class="pl-sticker"><span class="ico">${s.icon}</span><b>${esc(s.name)}</b><small>${esc(s.how)}</small></div>`).join('')}
        <button type="button" class="btn lg block">太棒了！</button></div>`;
      document.body.appendChild(pop); Play.sfx.play('sticker');
      pop.querySelector('button').onclick = () => pop.remove();
    }

    // ---------------------------------------------------------------- 打开页面
    const back = lobby.rooms.find(r => r.mine || r.joined);   // 刷新了页面 / 换了设备：回到自己的房间，正在玩就接着玩
    if (back) {
      panel.innerHTML = '<div class="pl-title">正在回到房间…</div>';
      (async () => {
        try { room = await (back.mine ? fetch(`/api/mp/rooms/${back.id}`).then(r => r.json()) : api(`/api/mp/rooms/${back.id}/join`, {})); }
        catch (e) { sh.toast(e.message, 'warn'); return showLobby(); }
        if (room.error) return showLobby();
        connect();
        await new Promise(r => setTimeout(r, 1200));   // 看看我是不是在另一台设备上开着
        if (otherMe && otherMe.playing) return askTake(otherMe.playing);
        if (otherMe) net.send({t: 'take'});
        if (room.play || peerPlaying) rejoin(); else showRoom();
      })();
    } else showLobby();
    if (lobby.locked) $('.pl-start').classList.add('locked');
    return sh;
  };
})();

/* 所有游戏共用的外壳（docs/DESIGN.md 5.26）：单机、两人联网、全家一起玩是同一套，区别只是房间里有几个人。
   外壳管：大厅（联网：谁在等我、开公开 / 家里的房间、起昵称）、房间（谁在、角色、闯关地图、出什么题）、开局倒计时、答题面板、
   倒下答题复活（联网）、暂停（谁都能暂停、谁都能继续）、存档和刷新 / 换设备接着玩、状态条和表情（联网）、结算（星星、纪录、解锁、个人战报、贴纸）。
   游戏只写玩法：
     PlayShell.init({
       tempo: 120, rules: ['……'], keys: '电脑键盘：……',
       quiz: 'modal' | 'dock' | 'revive',   // 答题面板：点按钮弹出（火柴人）/ 常驻下方（赛跑、守城）/ 倒下才弹出、答够才关（联网游戏）
       setup(sh) {},             // 页面打开时（自己建画布、按钮）；或者写 preview(sh, lv)：外壳建好 sh.stage，房间里画选中的关
       input: {left: ['ArrowLeft', 'a'], …}, touch: [['left', '◀'], …],   // 键盘、触屏按钮交给外壳管（不写就自己管）
       ready(sh) {},             // 点了开始、倒计时之前（把画面放回起点）
       begin(ctx) {},            // 倒计时结束：开始玩
       end() {},                 // 这一关停下了（结束、换设备）：停掉循环
       onAnswer(res) {}, onXP(res) {}, onQuizOpen() {}, onQuizClose() {}, onRevive(info) {},
       revive: {verb: '接着打', done: '…'},   // 倒下答题时怎么说（不写：「复活」）。规矩所有游戏一样，游戏改不了：见下面 REVIVE_NEED
       moreHint: '人多怪兽也多',   // 房间里还能进人时，后面接的一句（不写就不说）
       quizOpts: {rightHtml, wrongHtml, nextDelay},   // 答题面板的额外选项（quiz.js：答对、答错时多显示什么，答对后隔多久出下一题）
       pause(on, ms) {},         // 暂停 / 继续了（游戏循环外壳会停，自己用墙上时钟算时间的游戏在这里把暂停的 ms 补上）
       verdict(res) {},          // 对战：这一局对我来说是赢还是输 → {result, title, why}（res 是房主报的结果）
       save() {},                // 我自己的状态，每 2 秒存到服务器（写了它，刷新、换设备才能接着玩）
       saveWorld() {}, loadWorld(w) {}, peerView(id) {}, loadMe(v) {},   // 联网：共享的世界、我这里看到的队友 id（重连时以在线的人为准）
     });
   ctx（begin 时给游戏的）：
     ctx.level 这一关（app/arena/games/：关卡文件，或者服务器按进度算的参数）；ctx.seed 大家一样的随机种子；ctx.match 我的答题对局
     ctx.quiz 答题面板；ctx.resume 接着玩时的存档 {me, world}（不是接着玩就是 null）；ctx.sh 外壳
     ctx.finish(result, report, title)   这一关结束：win / lose / draw / quit；report 是游戏报的数据（用时、分数、血量……），
                                         联网时只有开房间的人报过关，谁都可以报没过（时间到了）。report.why 写在结算上
     ctx.toast(文字, 'good' | 'warn')；ctx.elapsed() 这一关玩了多少秒
     ctx.sh.gateAsk(哪一块) / ctx.sh.gateAnswer(题, 第几个)   题板的题（往上跳、向下跳、左右飞）：每块题板各拿各的题，见下面
   联网时还有：ctx.net 连接（net.js）；ctx.me / ctx.peer / ctx.peers {id, name, avatar, role}；ctx.host 我是不是开房间的（共享的东西由他算）；
     ctx.die() 我倒下了：题目盖在游戏上，答对 1 道就起来（答错、点「这题不会」都只是换一道，不加题）；ctx.rescue() 队友救了我；
     ctx.reviving 我在答题；ctx.deaths() 大家一共倒下几次；ctx.roleIndex(r) 角色是第几个；
     ctx.slot(id) 这个人是第几个（按角色排，0 起，对应关卡里的 1～4 号出生点，人少时也是连着的）
   设备：每个页面一个设备号（刷新不变）。服务器记着每个账号在哪台设备上玩（app/arena/rooms.py）：另一台设备上正在玩，
   就先问要不要换过来；换走了的那台下次存档时被告知，停下来。 */
(function () {
  const PlayShell = window.PlayShell = {};
  const root = document.getElementById('pl');
  const $ = s => root.querySelector(s);
  const EMOTES = ['👍', '⏳', '🙌', '❤️', '❓'];
  const EMOTE_TEXT = {'👍': '好的', '⏳': '等等我', '🙌': '太棒了', '❤️': '加油', '❓': '怎么走？'};

  // 倒下答题的规矩，所有游戏都一样：不管倒下几次、答错几次，永远是答对 1 道就起来。
  // 答错不加题——一直答错的孩子最需要的是马上能回到游戏里，而不是越欠越多。
  const REVIVE_NEED = 1;

  PlayShell.init = function (def) {
    const data = JSON.parse(root.dataset.info || '{}');
    const G = data.game, TOTAL = G.total || 0, multi = G.players[1] > 1, me0 = data.me;
    const quizMode = def.quiz || 'modal';
    const BACK = data.back || '/arena', BACK_TEXT = BACK === '/arena' ? '回乐园' : '回抓词';   // 成人、做完一组送的那一局从「抓词」进学单词的游戏
    const RV = Object.assign({verb: '复活', done: '🎉 复活啦！'}, def.revive);
    const device = deviceId();
    const sh = {root, def, data, canvas: $('canvas'), hud: $('.pl-hud'), ctrl: $('.pl-ctrl'), dock: $('.pl-dock'),
                match: null, quiz: null, over: true};
    const panel = $('.pl-start .pl-panel'), over = $('.pl-start'), endEl = $('.pl-end'), stageEl = $('.pl-stage');
    sh.toast = (t, k) => Play.ui.toast($('.pl-toast'), t, k);
    sh.finish = finish;
    // 题板（画在游戏世界里的题：题板、题门、题墙）：每块各拿各的题，和倒下复活的答题面板互不相干。
    // gateAsk(哪一块) → 题；gateAnswer(题, 第几个) → {correct, right：对的是第几个, lost：没答上（过期、断网），当作看答案过}
    sh.gateAsk = async key => (await api(`/api/arena/${sh.match.match_id}/q?gate=${encodeURIComponent(key)}`)).item;
    sh.gateAnswer = async (item, i) => {
      let res = null;
      try { res = await api(`/api/arena/${sh.match.match_id}/a`, {item_id: item.id, answer: String(i)}); } catch (e) { /* 过期了、断网了 */ }
      const right = res && res.correct ? i : res && /^[A-H]\. /.test(res.answer || '') ? 'ABCDEFGH'.indexOf(res.answer[0]) : -1;
      return right < 0 ? {correct: false, right: i, lost: true} : {correct: !!res.correct, right, lost: false};
    };
    sh.hudClock = text => { const c = sh.hud.querySelector('.mp-clock'); if (c) c.textContent = text; };
    if (quizMode === 'dock') root.classList.add('has-dock');
    Play.ui.soundButtons(root, def.tempo);
    if (def.input) Play.input.init(def.input);

    let lobby = data.lobby, room = null, net = null, state = 'lobby', page = 0, poll = 0, gone = false;
    let ctx = null, cur = null, match = null, quiz = null, t0 = 0, myDeaths = 0, peerDeaths = 0, byeTimer = 0, saveTimer = 0, resuming = false;
    const online = new Set(), peerPlaying = {};
    const ROLES = Object.fromEntries(G.roles.map(r => [r.id, r]));
    const roleOf = r => ROLES[r] || {name: r, icon: ''};
    const known = {};   // 见过的关卡数据（房间的 preview、开局的 stage）：关卡可以有上千关，不一次全发下来
    const lvAt = n => { const p = room && room.preview; if (p) known[p.n] = p; return known[n]; };
    const peers = () => room ? room.members.filter(m => m.id !== me0.id) : [];
    const peerName = () => peers().map(p => p.name).join('、') || '队友';
    const allOnline = () => peers().every(p => online.has(p.id));

    // ---------------------------------------------------------------- 画布（写了 preview 的游戏由外壳建）
    let previewN = 1;
    function preview() { const lv = TOTAL && (lvAt(previewN) || (room && room.preview)); if (!cur && def.preview && lv) def.preview(sh, lv); }
    if (def.preview) { sh.stage = Play.stage(sh.canvas, {height: 450, minW: 640, maxW: 1400}); sh.stage.onResize = () => preview(); }
    if (def.setup) def.setup(sh);

    // ---------------------------------------------------------------- 和服务器说话（房间、存档、结果都带设备号）
    async function post(path, body) {
      const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.assign({device}, body || {}))});
      let d = {}; try { d = await r.json(); } catch (e) { /* 空 */ }
      if (r.status === 409 && d.moved) { takenAway(); throw new Error(d.error); }
      if (!r.ok) { const e = new Error(d.error || d.detail || ('出错了 ' + r.status)); e.status = r.status; e.data = d; throw e; }
      return d;
    }
    const roomApi = (what, body) => post(`/api/rooms/${room.id}/${what}`, body);

    // ---------------------------------------------------------------- 大厅（联网）
    // 开房间只有两种模式（app/arena/rooms.py）：公开（默认，全站都看得到；可以设 4 位密码，设了就是 kind code）、家里（只有家里人）
    // 大厅里列的都是能进的房间，点一下就进；房间名自动起，不用填
    const KIND = {public: '🌏 公开', code: '🌏 公开 · 🔒 要密码', family: '🏠 家里'};
    const MODE = {public: {icon: '🌏', name: '公开'}, family: {icon: '🏠', name: '家里'}};
    let kind = null, pinOn = false, pinVal = '';
    function showLobby() {
      state = 'lobby'; over.hidden = false; room = null;
      if (net) { net.close(); net = null; }
      const sibs = lobby.siblings, waiting = lobby.rooms.filter(r => !r.mine && r.host);
      const kinds = ['public', 'family'].filter(k => k !== 'family' || sibs.length);
      if (!kinds.includes(kind)) kind = kinds[0];
      const pinMust = !lobby.public_ok;   // 隐私模式：公开房间一定要设密码
      if (pinMust) pinOn = true;
      let h = `<div class="pl-title">${esc(G.icon)} ${esc(G.name)}</div>`;
      h += `<div class="pl-label">${waiting.length ? `🎈 正在等人的房间（${waiting.length}）：` : '🎈 还没有人开房间，你来开一个吧！'}</div>`;
      h += `<div class="mp-rooms">${waiting.map(r => `<div class="mp-invite k-${r.kind}"><canvas class="mp-face" data-av="${esc(r.host.avatar)}"></canvas>
          <div class="grow"><b>${esc(r.name)}</b><small>${KIND[r.kind]} · ${r.role ? `${roleOf(r.role).icon} ${esc(roleOf(r.role).name)} · ` : ''}${TOTAL ? `第 ${r.level} 关 · ` : ''}${r.count}/${r.max} 人</small>
          ${r.locked ? `<div class="mp-pin-row" hidden><input class="mp-pin-in" inputmode="numeric" maxlength="4" placeholder="4 位密码"><button type="button" class="btn mp-pin-go" data-id="${r.id}">进去</button></div>` : ''}</div>
          <button type="button" class="btn lg pl-go mp-join" data-id="${r.id}" data-locked="${r.locked ? 1 : ''}">${r.locked ? '🔒 加入' : '加入！'}</button></div>`).join('')}</div>`;
      h += `<div class="pl-label">${waiting.length ? '或者自己开一个房间：' : '开一个房间：'}</div>
        <div class="mp-kinds">${kinds.map(k => `<button type="button" class="mp-kind${k === kind ? ' on' : ''}" data-kind="${k}">${MODE[k].icon} ${MODE[k].name}</button>`).join('')}</div>`;
      if (kind === 'public') h += `<div class="mp-pin-set"><label><input type="checkbox" class="mp-pin-on"${pinOn ? ' checked' : ''}${pinMust ? ' disabled' : ''}> 🔒 设个密码</label>
          ${pinOn ? `<input class="mp-pin-mine" inputmode="numeric" maxlength="4" placeholder="4 位数字，不填就随机" value="${esc(pinVal)}">` : ''}</div>`;
      h += `<p class="mp-hint mp-kind-hint">${kindHint(kind, sibs, pinMust)}</p>`;
      h += G.roles.length ? `<div class="pl-label">我想当：</div>
          <div class="mp-roles">${G.roles.map(v => `<button type="button" class="mp-role" data-role="${v.id}"><span>${v.icon}</span>${esc(v.name)}</button>`).join('')}</div>`
        : `<button type="button" class="btn lg block pl-go mp-role" data-role="">开一个房间</button>`;
      h += `<p class="mp-nick">我的昵称：<b>${esc(lobby.nickname || '还没起')}</b> <button type="button" class="btn sm ghost mp-nick-edit">${lobby.nickname ? '改一改' : '起一个'}</button>
          <small>和别的家庭的小伙伴一起玩时，大家只看到昵称。</small></p>`;
      if (lobby.locked) h += `<p class="pl-locked">${esc(lobby.locked)}</p>`;
      panel.innerHTML = h + rulesHtml();
      faces(panel);
      panel.querySelectorAll('.mp-join').forEach(b => b.onclick = () => {
        if (!b.dataset.locked) { joinRoom(b.dataset.id); return; }
        const row = b.parentNode.querySelector('.mp-pin-row');   // 密码房间：就地输密码
        row.hidden = false; b.hidden = true; row.querySelector('input').focus();
      });
      panel.querySelectorAll('.mp-pin-go').forEach(b => b.onclick = () => {
        const pin = b.parentNode.querySelector('input').value.trim();
        if (!/^\d{4}$/.test(pin)) { sh.toast('密码是 4 位数字', 'warn'); return; }
        joinRoom(b.dataset.id, pin);
      });
      panel.querySelectorAll('.mp-pin-in').forEach(i => i.onkeydown = e => { if (e.key === 'Enter') i.parentNode.querySelector('.mp-pin-go').click(); });
      panel.querySelectorAll('.mp-kind').forEach(b => b.onclick = () => { kind = b.dataset.kind; Play.sfx.play('click'); showLobby(); });
      const pinBox = panel.querySelector('.mp-pin-on'), pinIn = panel.querySelector('.mp-pin-mine');
      if (pinBox) pinBox.onchange = () => { pinOn = pinBox.checked; Play.sfx.play('click'); showLobby(); if (pinOn) panel.querySelector('.mp-pin-mine').focus(); };
      if (pinIn) pinIn.oninput = () => { pinVal = pinIn.value.replace(/\D/g, '').slice(0, 4); pinIn.value = pinVal; };
      panel.querySelectorAll('.mp-role').forEach(b => b.onclick = () => {
        const locked = kind === 'public' && pinOn;
        if (locked && pinVal && pinVal.length !== 4) { sh.toast('密码是 4 位数字，不填就随机一个', 'warn'); return; }
        withNick(kind !== 'family', () => enter(post('/api/rooms', {game: G.id, role: b.dataset.role, kind: locked ? 'code' : kind, pin: locked ? pinVal : ''})));
      });
      panel.querySelector('.mp-nick-edit').onclick = () => askNick();
      clearInterval(poll);
      poll = setInterval(async () => {   // 有人开房间了就显示出来
        if (state !== 'lobby') return;
        try { const l = await (await fetch(`/api/games/${G.id}/lobby`)).json(); if (JSON.stringify(l.rooms) !== JSON.stringify(lobby.rooms)) { lobby = l; showLobby(); } } catch (e) { /* 等下次 */ }
      }, 3000);
      preview();
    }
    function kindHint(k, sibs, pinMust) {
      if (k === 'family') return `只有家里人能进。开好以后，让${sibs.map(s => esc(s.name)).join('、')}在自己的平板或电脑上打开 乐园 → ${esc(G.name)}，点「加入」。`;
      if (pinMust) return lobby.adult ? '大人开公开房间要设密码，只和认识的人一起玩。开好以后把密码告诉他们。'
        : '爸爸妈妈开了隐私模式，公开房间要设密码。开好以后把密码告诉想一起玩的小伙伴。';
      if (pinOn) return '全站的小伙伴都能看到，输对密码才能进。开好以后把密码告诉想一起玩的小伙伴。';
      return '全站的小伙伴都能看到，点「加入」就进来。';
    }
    function joinRoom(id, pin) {
      const wrap = () => enter(post(`/api/rooms/${id}/enter`, pin ? {pin} : {}), () => askNick(wrap));
      wrap();
    }
    function withNick(need, go) { if (need && !lobby.nickname) askNick(go); else go(); }
    function askNick(then) {   // 起昵称（和别的家庭的孩子一起玩、上排行榜时只显示昵称）
      state = 'nick';
      panel.innerHTML = `<div class="pl-title">😎 给自己起个昵称</div>
        <p class="mp-note">和别的家庭的小伙伴一起玩时，大家只看到你的昵称。别用自己的真名，起一个外号吧。</p>
        <input class="mp-nick-in" maxlength="10" placeholder="比如：闪电小队长" value="${esc(lobby.nickname || '')}">
        <p class="pl-locked mp-nick-err"></p>
        <button type="button" class="btn lg block pl-go mp-nick-ok">就叫这个</button>
        <button type="button" class="btn ghost block mp-nick-back" style="margin-top:8px">先不起了</button>`;
      const inp = panel.querySelector('.mp-nick-in');
      inp.focus();
      panel.querySelector('.mp-nick-back').onclick = () => showLobby();
      panel.querySelector('.mp-nick-ok').onclick = async () => {
        try { lobby.nickname = (await post('/api/arena/nickname', {nickname: inp.value})).nickname; }
        catch (e) { panel.querySelector('.mp-nick-err').textContent = e.message; return; }
        sh.toast(`好的，${lobby.nickname}！`, 'good');
        if (then) then(); else showLobby();
      };
    }
    const rulesHtml = () => `<details class="pl-how"${stageEl.clientHeight < 460 ? '' : ' open'}><summary>怎么玩</summary><ul class="pl-rules">${def.rules.map(r => `<li>${r}</li>`).join('')}</ul></details><p class="pl-keys">${esc(def.keys || '')}</p>`;
    function faces(el) { el.querySelectorAll('canvas[data-av]').forEach(c => { c.width = 96; c.height = 120; Art.portrait(c, c.dataset.av, 'win'); }); }

    // ---------------------------------------------------------------- 进房间（打开页面、加入、刷新、换设备）
    async function enter(p, needNick) {
      Play.sfx.unlock();
      try { room = await p; } catch (e) {
        if (e.data && e.data.need_nick) { if (needNick) needNick(); else askNick(); return; }
        sh.toast(e.message, 'warn');
        if (multi && !room) showLobby();
        else if (!room) panel.innerHTML += `<p class="pl-locked">${esc(e.message || '没打开，过一会儿再试试。')}</p><a class="btn block" href="${BACK}">${BACK_TEXT}</a>`;
        return;
      }
      clearInterval(poll);
      if (room.elsewhere) return askTake(room.elsewhere);
      afterEnter();
    }
    function afterEnter() {
      previewN = room.level || 1;
      if (multi && !net) connect();
      const p = room.play;
      if (p && (multi || (p.resume && def.save))) startLevel(p, p.resume || {}); else showRoom();
    }
    function askTake(n) {   // 同一个账号在另一台设备上正在玩
      state = 'take'; over.hidden = false;
      panel.innerHTML = `<div class="pl-title">📱 换到这台设备？</div>
        <p>你在另一台设备上正在玩${n > 0 ? `第 ${n} 关` : ''}。</p>
        <button type="button" class="btn lg block pl-go mp-take">在这台设备上接着玩</button>
        <button type="button" class="btn ghost block mp-keep" style="margin-top:8px">不了，接着用那台玩</button>`;
      panel.querySelector('.mp-take').onclick = async () => {
        panel.innerHTML = '<div class="pl-title">正在换过来…</div>';
        try { room = await roomApi('take'); } catch (e) { sh.toast(e.message, 'warn'); return; }
        setTimeout(() => { state = 'room'; afterEnter(); }, 2200);   // 等那台把最后一次存档存好（它每 2 秒存一次，存的时候就知道换走了）
      };
      panel.querySelector('.mp-keep').onclick = () => showGone('好的，接着在另一台设备上玩吧。');
    }
    function takenAway() {   // 我在另一台设备上接着玩了：这台停下来（进度在服务器上，不算结束）
      if (gone) return;
      gone = true;
      if (cur) { if (def.end) def.end(); Play.music.stop(); if (quiz) quiz.stop(); }
      unpause(); clearInterval(saveTimer); clearInterval(poll); cur = null; sh.over = true; t0 = 0;
      Play.input.blocked = false; Play.input.clear(); root.classList.remove('playing'); endEl.hidden = true;
      if (ownCtrl) { sh.ctrl.innerHTML = ''; sh.hud.innerHTML = ''; }
      if (net) { net.close(); net = null; }
      showGone('已经换到另一台设备上玩了。');
    }
    function showGone(text) {
      gone = true; state = 'gone'; over.hidden = false;
      if (net) { net.close(); net = null; }
      panel.innerHTML = `<div class="pl-title">👋 ${esc(text)}</div><button type="button" class="btn lg block pl-go mp-here">回到这台设备上玩</button>`;
      panel.querySelector('.mp-here').onclick = () => location.reload();
    }

    // ---------------------------------------------------------------- 联网连接
    function connect() {
      net = new MpNet(room.id);
      net.onStatus = (ok, why) => {
        if (ok) { net.send({t: 'here', playing: cur ? cur.session : 0}); refresh(); }
        if (!ok && why === 'ws') sh.toast('连不上联网服务。请爸爸妈妈看看服务器的 WebSocket 设置（docs/DEPLOY.md）', 'warn');
        if (!ok && why && why !== 'ws') { sh.toast(why, 'warn'); backToLobby(); }
        drawRoomBits();
      };
      net.on('hello', m => {   // 有人进房间了（刷新回来的也算）：告诉他我在不在玩、是不是正在答题
        if (!isPeer(m)) return;
        seen(m); net.send({t: 'here', playing: cur ? cur.session : 0});
        if (ctx && ctx.reviving) net.send({t: 'rv', need: ctx.need});
        if (!cur) refresh();
      });
      net.on('here', m => {
        if (!isPeer(m)) return;
        seen(m); peerPlaying[m.from] = m.playing || 0;
        if (!cur && m.playing && (state === 'room' || state === 'end')) rejoin();   // 队友还在玩：我回到这一局
        else if (!cur) refresh();
      });
      net.on('need_world', m => {   // 倒数还没完时游戏里还是上一关，不发
        if (isPeer(m) && cur && ctx && ctx.begun && def.saveWorld) net.send({t: 'world', s: cur.session, el: +ctx.elapsed().toFixed(1), w: def.saveWorld(), you: def.peerView ? def.peerView(m.from) : null, to: m.from, paused: pausedBy > 0 ? pausedBy : 0});
        else if (isPeer(m) && cur && ctx && ctx.begun && pausedBy > 0) net.send({t: 'pause', by: pausedBy, to: m.from});
      });
      net.on('world', m => {
        if (!isPeer(m) || !cur || m.s !== cur.session || !ctx || (m.to && m.to !== me0.id)) return;
        if (def.loadWorld) def.loadWorld(m.w);
        if (m.you && def.loadMe) def.loadMe(m.you);
        if (m.el > ctx.elapsed() + 1) t0 = (pausedBy ? pausedAt : performance.now()) - m.el * 1000;   // 时间跟还在玩的那台对齐
        if (m.paused) doPause(m.paused);
      });
      net.on('bye', m => {
        if (!isPeer(m)) return;
        online.delete(m.from); peerPlaying[m.from] = 0; drawRoomBits(); hudDraw();
        if (cur) {
          sh.toast(`${(room.members.find(x => x.id === m.from) || {}).name || '队友'}掉线了，等一等…`, 'warn');
          clearTimeout(byeTimer); byeTimer = setTimeout(() => { if (cur && !allOnline()) quit(`${peerName()}离开了`); }, 30000);
        }
      });
      net.on('*', m => { if (isPeer(m) && m.t !== 'bye') seen(m); });
      net.on('room', () => refresh());
      net.on('start', m => { if (!room.host) rejoin(true); });
      net.on('cant', m => { sh.toast(`${nameOf(m.from)}：${m.why}`, 'warn'); if (cur) quit(m.why); });
      net.on('rv', m => {
        if (!isPeer(m) || !ctx) return;
        ctx.needs[m.from] = m.need; if (m.fresh) peerDeaths++; if (m.rescued) peerDeaths = Math.max(0, peerDeaths - 1);
        hudDraw();
      });
      net.on('pause', m => { if (isPeer(m) && cur && (!m.to || m.to === me0.id)) doPause(m.by || m.from); });
      net.on('resume', m => { if (isPeer(m) && cur) doResume(m.from); });
      net.on('emote', m => { if (isPeer(m) && EMOTES.includes(m.e)) showEmote(m.e, m.from); });
      net.on('done', m => { if (!room.host && cur && m.res && m.res.session === cur.session) endLevel('win', m.res); });
      net.on('over', m => {
        if (!cur) return;
        if (room.host && !m.res) finish(m.result || 'lose', {why: m.why});   // 队友那边时间到了：我来报
        else endLevel(m.res ? m.res.result : (m.result || 'quit'), m.res, null, m.why);
      });
    }
    const isPeer = m => room && m.from !== me0.id;   // 服务器只转发房间里的人的消息；刚进来的人可能还不在 room.members 里（refresh 后就在了）
    const nameOf = id => (room && room.members.find(x => x.id === id) || {}).name || '队友';
    function seen(m) { if (!online.has(m.from)) { online.add(m.from); clearTimeout(byeTimer); drawRoomBits(); hudDraw(); } }
    async function refresh() {
      if (!room || gone) return;
      try { room = await api(`/api/rooms/${room.id}${page ? `?world=${page}` : ''}`); } catch (e) { sh.toast(e.message, 'warn'); return backToLobby(); }
      previewN = room.level || 1;
      if (state === 'room') showRoom();
    }
    async function backToLobby() {
      if (room) { try { await roomApi('leave'); } catch (e) { /* 房间已经关了 */ } }
      if (net) { net.send({t: 'room'}); net.close(); net = null; }
      online.clear();
      if (!multi) { location.href = BACK; return; }
      try { lobby = await api(`/api/games/${G.id}/lobby`); } catch (e) { /* 用旧的 */ }
      showLobby();
    }
    async function rejoin(fresh) {   // 回到正在玩的这一局（fresh：房主刚开的新一局，和房主一起倒数；不然是队友还在玩，接着玩）
      if (resuming || cur || gone) return;
      resuming = true;
      try {
        room = await api(`/api/rooms/${room.id}`);
        if (room.play) await startLevel(room.play, room.play.resume || (fresh ? null : {}), true);
        if (!cur && !gone && state !== 'play') showRoom();
      } catch (e) { /* 等下次 */ }
      resuming = false;
    }

    // ---------------------------------------------------------------- 房间
    function showRoom() {
      state = 'room'; over.hidden = false; endEl.hidden = true;
      const ps = peers(), prog = room.progress, locked = room.members.find(m => m.locked);
      let h = `<div class="pl-title">${esc(G.icon)} ${esc(G.name)}</div>`;
      if (multi) {
        const card = m => `<div class="mp-mem${m.id === me0.id ? ' me' : ''}"><canvas class="mp-face" data-av="${esc(m.avatar)}"></canvas>
            <b>${esc(m.name)}</b>${m.role ? `<span class="mp-r">${roleOf(m.role).icon} ${esc(roleOf(m.role).name)}</span>` : ''}
            ${m.id === me0.id ? '<small class="mp-dot on">我</small>' : `<small class="mp-dot" data-dot="${m.id}">…</small>`}
            ${m.locked ? `<small class="mp-lock">🔒 ${esc(m.locked)}</small>` : ''}</div>`;
        const names = room.kind === 'family' ? lobby.siblings.filter(s => !room.members.some(m => m.id === s.id)).map(s => s.name).join('或') || '队友' : '小伙伴';
        const how = room.kind === 'code' ? '告诉他密码' : `让${esc(names)}在自己的设备上打开 乐园 → ${esc(G.name)}`;
        const empty = `<div class="mp-mem empty"><div class="mp-wait">⏳</div><b>等${esc(names)}进来</b><small>${how}</small></div>`;
        const cards = room.members.map(card).concat(Array(Math.max(0, G.players[0] - room.members.length)).fill(empty));
        h += `<div class="mp-mems${room.members.length > 2 ? ' many' : ''}">${cards.join('<span class="mp-amp">＋</span>')}</div>`;
        if (room.kind === 'code') h += `<div class="mp-pin">🔒 密码 <b>${esc(room.pin)}</b><small>告诉想一起玩的小伙伴：在大厅里点「${esc(room.name)}」，输这个密码就进来了</small></div>`;
        else if (room.kind === 'public') h += `<p class="mp-hint">🌏 公开房间：全站的小伙伴都能进来，大家只看到昵称。</p>`;
        const more = G.players[1] - room.members.length;
        if (room.full && more > 0) h += `<p class="mp-hint">还能再进 ${more} 个人${def.moreHint ? `，${def.moreHint}` : ''}。开始以后就不能进人了。</p>`;
        if (room.host && G.roles.length > 1 && room.members.length > 1) h += `<button type="button" class="btn sm ghost mp-swap">🔁 换角色</button>`;
      } else {
        h = `<canvas class="pl-me" data-av="${esc(data.avatar)}"></canvas>` + h;
      }
      if (prog) h += mapHtml(prog);
      over.classList.toggle('has-map', !!prog);
      const lv = lvAt(room.level);
      if (lv && lv.tip) h += `<p class="mp-tip">💡 ${esc(lv.tip)}</p>`;
      if (room.host) h += `<button type="button" class="btn lg block pl-go mp-start">${TOTAL ? `开始第 ${room.level} 关！` : '开始！'}${data.unlimited ? '' : `<small>（今天还能玩 ${Math.floor((data.left || 0) / 60)} 分钟）</small>`}</button>`;
      else h += `<p class="mp-note">等 <b>${esc(nameOf(room.members.find(m => m.host).id))}</b> 选关、点开始。</p>`;
      h += `<p class="pl-locked">${locked ? esc(multi ? `${locked.name}：${locked.locked}` : locked.locked) : ''}</p>`;
      if (locked && locked.id === me0.id && data.need) h += `<a class="btn block" href="/today">去做任务</a>`;
      h += rulesHtml();
      if (multi) h += `<div class="mp-foot"><button type="button" class="btn sm ghost mp-leave">退出房间</button></div>`;
      panel.innerHTML = h;
      faces(panel); drawRoomBits();
      const sw = panel.querySelector('.mp-swap');
      if (sw) sw.onclick = () => act(roomApi('role'));
      panel.querySelectorAll('.pm-arr').forEach(b => b.onclick = () => { page = prog.world.n + +b.dataset.d; Play.sfx.play('click'); refresh(); });
      if (room.host) panel.querySelectorAll('.pm-lv:not(.lock)').forEach(b => b.onclick = () => { Play.sfx.play('click'); act(roomApi('level', {n: +b.dataset.n})); });
      const go = panel.querySelector('.mp-start');
      if (go) go.onclick = () => startNew(room.level);
      const lb = panel.querySelector('.mp-leave');
      if (lb) lb.onclick = () => backToLobby();
      preview();
    }
    async function act(p) {   // 房主改了房间（换角色、选关）：告诉大家
      try { room = await p; } catch (e) { sh.toast(e.message, 'warn'); return; }
      previewN = room.level || 1; page = 0;
      if (net) net.send({t: 'room'});
      showRoom();
    }
    function drawRoomBits() {
      panel.querySelectorAll('[data-dot]').forEach(d => { const on = online.has(+d.dataset.dot); d.textContent = on ? '在线' : '还没连上'; d.classList.toggle('on', on); });
      const go = panel.querySelector('.mp-start');
      if (go && room) go.disabled = !room.full || !allOnline() || !!room.members.find(m => m.locked);
    }
    function mapHtml(prog) {   // 闯关地图：一个世界一页（翻页时 refresh 带上 page，服务器只发这一个世界的关卡）
      const w = prog.world, ls = prog.levels, nw = prog.worlds_total || 1, sel = ls.find(l => l.n === room.level);
      const gift = w.unlock && ls.length ? `<div class="pm-gift${w.done ? ' got' : ''}">${w.unlock.icon} ${w.done ? `已解锁：${esc(w.unlock.name)}` : `打通第 ${ls[ls.length - 1].n} 关，解锁 <b>${esc(w.unlock.name)}</b>（${esc(w.unlock.how)}）`}</div>` : '';
      return `<div class="pl-map${multi ? ' mp-map' : ''}"><div class="pm-head"><button type="button" class="pm-arr" data-d="-1" ${w.n > 1 ? '' : 'disabled'} aria-label="上一个世界">‹</button>
          <div class="pm-name"><b>${w.name ? `世界 ${w.n} · ${esc(w.name)}` : '关卡'}${w.done ? ' ✅' : ''}</b><small>⭐ ${w.stars}/${w.max_stars} · ${multi ? '我们' : ''}已经打通 ${prog.cleared}/${prog.total} 关${prog.worlds_done ? ` · ${prog.worlds_done} 个世界` : ''}</small></div>
          <button type="button" class="pm-arr" data-d="1" ${w.n < nw ? '' : 'disabled'} aria-label="下一个世界">›</button></div>
        <div class="pm-row pm-${w.scene}">${ls.map(l => `<button type="button" class="pm-lv${l.boss ? ' boss' : ''}${l.open ? '' : ' lock'}${l.n === room.level ? ' on' : ''}${l.cleared ? ' done' : ''}" data-n="${l.n}" ${l.open && room.host ? '' : 'disabled'} title="${esc(l.name)}">
            <b>${l.boss ? '👑' : l.open ? l.n : '🔒'}</b><span>${l.open ? '★★★'.slice(0, l.stars).padEnd(3, '☆') : ''}</span></button>`).join('<i class="pm-path"></i>')}</div>
        ${multi && sel ? `<div class="mp-lvname">第 ${sel.n} 关 · ${esc(sel.name)}${sel.best ? ` · ${multi ? '我们的' : ''}纪录 ${fmtRecord(sel.best)}` : ''}</div>` : ''}${gift}</div>`;
    }
    const fmtS = s => s >= 60 ? `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒` : `${(+s).toFixed(1)} 秒`;
    const fmtRecord = v => G.record === 'seconds' ? fmtS(v) : `${Math.round(v)} 分`;

    // ---------------------------------------------------------------- 开局
    async function startNew(n) {   // 房主开一局：服务器定这一局和随机种子，告诉大家
      if (multi && !allOnline()) { sh.toast(`${peerName()}还没连上`, 'warn'); return; }
      Play.sfx.unlock(); Play.sfx.play('click');
      if (def.ready) def.ready(sh);
      try { room = await roomApi('start', {n}); } catch (e) { sh.toast(e.message, 'warn'); return; }
      if (net) net.send({t: 'start', s: room.play.session});
      startLevel(room.play, null);
    }
    async function startLevel(play, resume, joining) {
      if (cur || gone || state === 'take') return;
      Play.sfx.unlock();
      // reward：做完一组抓词送的那一局（只出那一组的词、不算游戏时间），只用一次
      try { match = await api('/api/arena/start', {game: G.id, session: play.session, resume: resume && resume.match, reward: data.reward || null}); data.reward = 0; }
      catch (e) { sh.toast(e.message, 'warn'); if (net) net.send({t: 'cant', why: e.message}); if (!joining) showRoom(); return; }
      if (play.n && play.stage) known[play.n] = play.stage;
      cur = play; previewN = play.n || 1; state = 'play'; over.hidden = true; endEl.hidden = true;
      sh.match = match;
      if (net) net.send({t: 'here', playing: cur.session});
      if (quiz) quiz.destroy();
      quiz = sh.quiz = new ArenaQuiz(Object.assign({}, def.quizOpts || {}, {matchId: match.match_id, dock: quizMode === 'dock' ? sh.dock : null,
        sticky: quizMode === 'revive', autoNext: quizMode === 'revive', mount: quizMode === 'revive' ? stageEl : null,
        onAnswer: r => { onReviveAnswer(r); if (def.onAnswer) def.onAnswer(r, sh); }, onXP: r => def.onXP && def.onXP(r, sh),
        onOpen: () => def.onQuizOpen && def.onQuizOpen(sh), onClose: () => def.onQuizClose && def.onQuizClose(sh)}));
      if (quizMode === 'revive') quiz.el.querySelector('.aq-card').insertAdjacentHTML('afterbegin', '<div class="mp-debt"></div>');
      if (quizMode === 'dock') { sh.dock.hidden = false; quiz.el.classList.add('wait'); }
      myDeaths = resume ? resume.deaths || 0 : 0; peerDeaths = resume ? resume.peer_deaths || 0 : 0;
      const ps = peers(), mine = room.members.find(m => m.id === me0.id);
      const ri = r => G.roles.findIndex(x => x.id === r);
      const order = room.members.slice().sort((a, b) => ri(a.role) - ri(b.role) || a.id - b.id).map(m => m.id);   // 出生点：按角色排第几个
      ctx = {net, me: mine, peer: ps[0] || null, peers: ps, host: room.host, level: play.stage || {}, seed: play.seed, session: play.session,
             match, quiz, sh, reviving: false, need: 0, needs: {}, resume: resume ? {me: resume.me || null, world: resume.world || null} : null,
             die, rescue, finish, toast: sh.toast, elapsed: () => t0 ? ((pausedBy ? pausedAt : performance.now()) - t0) / 1000 : 0,
             deaths: () => myDeaths + peerDeaths, roleName: r => roleOf(r).name, roleIndex: r => G.roles.findIndex(x => x.id === r),
             slot: id => order.indexOf(id)};
      root.classList.add('playing');
      buildCtrl(); hudDraw();
      const go = () => {
        if (cur !== play) return;
        t0 = performance.now() - (resume ? (resume.el || 0) * 1000 : 0);
        sh.over = false;
        Play.music.start(def.tempo);
        if (quizMode === 'dock') { quiz.el.classList.remove('wait'); quiz.load(); }
        def.begin(ctx); ctx.begun = true; pauseBtn.hidden = false;
        clearInterval(saveTimer);
        if (def.save) { saveTimer = setInterval(save, 2000); save(); }
        if (resume) {
          if (net) net.send({t: 'need_world'});
          if (resume.need > 0) { ctx.reviving = true; ctx.need = REVIVE_NEED; send({t: 'rv', need: ctx.need}); Play.input.blocked = true; debtText(); hudDraw(); askNow(); }
          sh.toast('接着玩！', 'good');
        }
      };
      if (resume) go(); else Play.ui.countdown($('.pl-cd'), go);
    }
    const send = m => { if (net) net.send(m); };

    // ---------------------------------------------------------------- 存档（刷新、换设备接着玩）
    function saveMsg() {
      const msg = {session: cur.session, el: +ctx.elapsed().toFixed(1), need: ctx.need, deaths: myDeaths, match: match && match.match_id};
      try { msg.me = def.save(); if (room.host && def.saveWorld) msg.w = def.saveWorld(); } catch (e) { /* 游戏还没准备好 */ }
      return msg;
    }
    async function save() {
      if (!cur || !ctx || !ctx.begun || !def.save || sh.over) return;
      try { await roomApi('save', saveMsg()); } catch (e) { /* 这一局结束了 / 换设备了（post 处理）/ 网断了：等下次 */ }
    }
    function saveNow() {   // 关页面、切到后台：用 sendBeacon，页面关了也送得出去
      if (!cur || !ctx || !ctx.begun || !def.save || sh.over || gone) return;
      const body = JSON.stringify(Object.assign({device}, saveMsg()));
      if (!(navigator.sendBeacon && navigator.sendBeacon(`/api/rooms/${room.id}/save`, new Blob([body], {type: 'application/json'})))) save();
    }
    addEventListener('pagehide', saveNow);
    document.addEventListener('visibilitychange', () => { if (document.hidden) saveNow(); });

    // ---------------------------------------------------------------- 倒下答题复活（联网）
    function die() {
      if (!ctx || ctx.reviving) return;
      ctx.reviving = true; ctx.need = REVIVE_NEED; myDeaths++;
      send({t: 'rv', need: ctx.need, fresh: true});
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
    setInterval(() => { if (cur && ctx && ctx.reviving && quiz && !quiz.isOpen) askNow(); }, 1000);   // 万一题目没弹出来
    function onReviveAnswer(res) {
      if (!ctx || !ctx.reviving) return;
      if (!res.correct) { debtText(res); return; }   // 答错不加题，题目自己换下一道
      ctx.need = 0;
      send({t: 'rv', need: 0});
      debtText(res); hudDraw();
      setTimeout(() => revived(false), 650);
    }
    function debtText(res) {
      const el = quiz && quiz.el.querySelector('.mp-debt'); if (!el) return;
      el.innerHTML = ctx.need <= 0 ? RV.done : `${res && !res.correct ? '没关系，换一道。' : ''}答对 <b>1</b> 道就${RV.verb}`;
    }
    function revived(rescued) {
      if (!ctx || !ctx.reviving) return;
      ctx.reviving = false; ctx.need = 0;
      quiz.close(true); quiz.item = null;
      Play.input.blocked = false; Play.input.clear();
      send({t: 'rv', need: 0, rescued});
      if (rescued) myDeaths = Math.max(0, myDeaths - 1);
      hudDraw();
      if (def.onRevive) def.onRevive({rescued});
    }
    function rescue() { revived(true); }

    // ---------------------------------------------------------------- 状态条、表情、触屏按钮（联网的状态条、写了 touch 的按钮由外壳画）
    const ownCtrl = multi || !!def.touch;
    function hudDraw() {
      if (!multi) return;
      if (!room || !cur) { sh.hud.innerHTML = ''; return; }
      const chip = m => {
        const mine = m.id === me0.id, need = mine ? ctx.need : ctx.needs[m.id], off = !mine && !online.has(m.id);
        return `<div class="mp-chip${mine ? ' me' : ''}${need ? ' dead' : ''}${off ? ' off' : ''}" data-id="${m.id}"><span>${roleOf(m.role).icon}</span><b>${esc(m.name)}</b>
          <small>${off ? '掉线了' : need ? '答题中 · 答对就回来' : '在玩'}</small><i class="mp-emote"></i></div>`;
      };
      const clock = sh.hud.querySelector('.mp-clock');
      sh.hud.innerHTML = `<div class="mp-chips">${room.members.map(chip).join('')}</div><div class="grow"></div><div class="mp-clock">${clock ? esc(clock.textContent) : ''}</div>`;
    }
    function showEmote(e, id) {
      const el = sh.hud.querySelector(`.mp-chip[data-id="${id}"] .mp-emote`);
      if (el) { el.textContent = `${e} ${EMOTE_TEXT[e]}`; el.classList.remove('on'); void el.offsetWidth; el.classList.add('on'); }
      Play.sfx.play('pop');
    }
    function buildCtrl() {
      if (!ownCtrl) return;
      const touch = (def.touch || []).map(([k, label, cls]) => `<button type="button" class="pl-btn ${cls || ''}" data-k="${k}">${label}</button>`);
      const half = def.touchSplit ?? Math.ceil(touch.length / 2);
      sh.ctrl.innerHTML = `<div class="pl-pad mp-touch">${touch.slice(0, half).join('')}</div>
        ${multi ? `<div class="mp-emotes">${EMOTES.map(e => `<button type="button" class="mp-em" data-e="${e}" title="${EMOTE_TEXT[e]}">${e}</button>`).join('')}</div>` : ''}
        <div class="pl-pad mp-touch">${touch.slice(half).join('')}</div>`;
      sh.ctrl.querySelectorAll('[data-k]').forEach(b => Play.input.hold(b, b.dataset.k));
      sh.ctrl.querySelectorAll('.mp-em').forEach(b => b.onclick = () => { send({t: 'emote', e: b.dataset.e}); showEmote(b.dataset.e, me0.id); });
      if (!matchMedia('(pointer:coarse)').matches) sh.ctrl.querySelectorAll('.mp-touch').forEach(x => x.classList.add('mouse'));
    }

    // ---------------------------------------------------------------- 暂停：谁都能暂停、谁都能继续（不怕暂停的人走开或掉线），
    // 继续时大家一起倒数 3-2-1。暂停时游戏循环停住（Play.frozen）、这一关的用时停住，也不算今天玩游戏的时间（服务器记在各自的对局上）。
    let pausedBy = 0, pausedAt = 0;   // pausedBy：谁暂停的（-1：正在倒数继续）
    const pauseBtn = root.querySelector('[data-pause]'), pauseEl = $('.pl-paused');
    if (pauseBtn) pauseBtn.onclick = () => { if (cur && ctx && ctx.begun && !pausedBy) { doPause(me0.id); send({t: 'pause', by: me0.id}); } };
    const pauseApi = on => { if (match) api(`/api/arena/${match.match_id}/pause`, {on}).catch(() => {}); };
    function doPause(by) {
      if (!cur || !ctx || pausedBy > 0) return;
      if (pausedBy < 0) { Play.ui.countdown($('.pl-cd'), null, true); } else { pausedAt = performance.now(); pauseApi(true); }
      pausedBy = by; Play.frozen = true; Play.input.clear(); Play.input.blocked = true; Play.music.stop();
      if (def.pause) def.pause(true, 0);
      const who = by === me0.id ? '你' : nameOf(by);
      pauseEl.querySelector('.pl-panel').innerHTML = `<div class="pl-title">⏸ ${esc(who)}暂停了</div>
        <p class="small">${multi ? '谁都可以点继续，大家一起倒数 3 秒再开始。' : '休息一下，准备好了点继续，倒数 3 秒再开始。'}暂停的时间不算用时，也不算今天玩游戏的时间。</p>
        <button type="button" class="btn lg block pl-go pl-resume">▶ 继续</button>`;
      pauseEl.querySelector('.pl-resume').onclick = () => { send({t: 'resume'}); doResume(me0.id); };
      pauseEl.hidden = false; pauseBtn.hidden = true;
      Play.sfx.play('click');
    }
    function doResume() {
      if (!cur || pausedBy <= 0) return;
      pausedBy = -1; pauseEl.hidden = true;
      Play.ui.countdown($('.pl-cd'), () => {
        if (pausedBy !== -1 || !cur) return;
        const ms = performance.now() - pausedAt;
        t0 += ms; pausedBy = 0; Play.frozen = false; pauseBtn.hidden = false;
        Play.input.clear(); Play.input.blocked = !!(ctx && ctx.reviving);
        Play.music.start(def.tempo); pauseApi(false);
        if (def.pause) def.pause(false, ms);
      });
    }
    function unpause() {   // 这一关停下了：暂停也一起结束
      if (pausedBy) pauseApi(false);
      pausedBy = 0; Play.frozen = false; pauseEl.hidden = true; pauseBtn.hidden = true;
    }

    // ---------------------------------------------------------------- 结束
    async function finish(result, report, title) {
      if (!ctx || ctx.finishing || !cur) return;
      report = report || {};
      if (multi && !room.host) {   // 联网：过关由开房间的人报；没过（时间到了）谁发现都行，告诉房主来报
        if (result !== 'win') { send({t: 'over', why: report.why, result}); endLevel(result, null, title, report.why); }
        return;
      }
      ctx.finishing = true;
      const rep = Object.assign({seconds: +ctx.elapsed().toFixed(1), deaths: ctx.deaths()}, report);
      let res;
      try { res = await roomApi('finish', {session: cur.session, result, report: rep}); }
      catch (e) { res = {error: e.message, n: cur.n, result, ...rep, stars: 0, checks: {}}; }
      res.session = cur.session;
      send(result === 'win' ? {t: 'done', res} : {t: 'over', why: report.why, res});
      endLevel(result, res, title, report.why, report);
    }
    function quit(why) {   // 这一关先停下了（队友走了、队友开不了局）：房主把这一局记成退出
      if (!cur) return;
      if (room.host) roomApi('save', {session: cur.session, end: 1}).catch(() => {});
      send({t: 'over', why, result: 'quit', res: {result: 'quit', session: cur.session}});
      endLevel('quit', null, null, why);
    }
    function stopPlay() {
      clearInterval(saveTimer); unpause();
      if (def.end) def.end();
      Play.music.stop(); Play.input.blocked = false; Play.input.clear();
      if (quiz) { quiz.stop(); if (quizMode === 'dock') quiz.el.classList.add('wait'); }
      root.classList.remove('playing');
      if (ownCtrl) { sh.ctrl.innerHTML = ''; sh.hud.innerHTML = ''; }
      const p = cur; cur = null; t0 = 0; sh.over = true;
      send({t: 'here', playing: 0});
      return p;
    }
    async function endLevel(result, res, title, why, stats) {
      if (!cur) return;
      const p = stopPlay();
      if (def.verdict && res && !res.error) ({result, title, why} = Object.assign({result, title, why}, def.verdict(res, result)));
      Play.sfx.play(result === 'win' ? 'win' : result === 'lose' ? 'lose' : 'pop');
      if (result === 'win' && window.Juice) Juice.buzz('done');
      let mine = null;
      try { mine = await api(`/api/arena/${match.match_id}/end`, {result, stats: stats || {}}); } catch (e) { /* 对局已经结束了 */ }
      const head = title || (multi ? {win: '🎉 我们一起过关了！', lose: '💪 差一点！', draw: '🤝 平局', quit: '这一关先停下了'}
                                   : {win: '🏆 你赢了！', lose: '💪 差一点！再来一局', draw: '🤝 平局', quit: '这局结束了'})[result];
      let h = `<div class="pl-title ${result === 'win' ? 'win' : 'lose'}">${head}</div>${why ? `<p>${esc(why)}</p>` : ''}`;
      if (res && res.n) h += stageHtml(res);
      if (res && res.error) h += `<p class="err">${esc(res.error)}</p>`;
      h += personal(mine);
      showEnd(h, res, p);
      const st = [].concat(((res && res.stickers) || {})[String(me0.id)] || [], (mine && mine.stickers) || []);
      if (res && res.stars) Play.sfx.play('star');
      if (st.length) stickers(st);
      if (res && res.progress && room) { room.progress = res.progress; if (res.next) room.level = res.next; }
    }
    function stageHtml(res) {
      const lv = lvAt(res.n) || {name: res.name};
      const stars = G.stars.map((s, i) => `<span class="${res.checks && res.checks[s.key] ? 'on' : ''}" style="animation-delay:${0.25 + i * 0.3}s">★</span>`).join('');
      const how = G.stars.map(s => `<span class="${res.checks && res.checks[s.key] ? 'ok' : ''}">${res.checks && res.checks[s.key] ? '✓' : '·'} ${esc(s.key === 'time' && lv.par ? `${lv.par} 秒内` : s.label)}</span>`).join('');
      let rec = '';
      if (res.result === 'win' && G.record && res.value != null) rec = `<div class="mp-time">${G.record === 'seconds' ? '⏱' : '🏅'} ${fmtRecord(res.value)}${res.new_best && res.prev_best ? ' <b class="ps-best">新纪录！</b>' : res.prev_best ? `<small>（${multi ? '我们的' : ''}纪录 ${fmtRecord(res.best)}）</small>` : ''}</div>`;
      else if (res.new_best && res.prev_best == null && !G.record && res.first_clear === false) rec = '<div class="ps-best">新纪录！</div>';
      return `<div class="pl-stage-res"><div class="ps-n">第 ${res.n} 关${lv.name && lv.name !== `第 ${res.n} 关` ? ` · ${esc(lv.name)}` : ''}</div><div class="ps-stars">${stars}</div><div class="ps-how">${how}</div>${rec}
        ${res.result !== 'win' ? `<p class="small">${esc(G.stars[0] ? G.stars[0].label : '过关')}就能拿第 1 颗星。</p>` : ''}
        ${res.unlocked ? `<div class="ps-unlock">${res.unlocked.icon} 解锁了 <b>${esc(res.unlocked.name)}</b>：${esc(res.unlocked.how)}</div>` : ''}
        ${res.all_clear && res.first_clear ? `<div class="ps-unlock">🏆 全部 ${TOTAL} 关都打通了！</div>`
          : res.world && res.world.done ? `<div class="ps-unlock">🗺️ ${multi ? '我们' : ''}打通了世界 ${res.world.n}${res.world.name ? `「${esc(res.world.name)}」` : ''}！</div>`
          : res.world && res.world.left ? `<p class="small">再过 ${res.world.left} 关，就打通${res.world.name ? `「${esc(res.world.name)}」` : '这个世界'}。</p>` : ''}</div>`;
    }
    function personal(r) {
      if (!r) return '';
      if (quizMode === 'revive' && !r.answered) return `<p class="small muted">${esc(def.noQuiz || '这一关我没有倒下，一道题都不用答！')}</p>`;
      const lv = r.level;
      return `<div class="pl-stats"><div class="focus" title="100 = 平时的你。这局比平时多答对，就超过 100"><b>${r.focus ?? '—'}</b><span>💎 ${multi ? '我的' : ''}专注值</span></div><div><b>${r.right}/${r.answered}</b><span>${multi ? '我' : ''}答对 / 答题</span></div><div><b>${r.best_streak || 0}</b><span>最长连对</span></div></div>
        ${r.topics && r.topics.length ? `<div class="pl-topics">这局练了：${r.topics.map(t => `<span>${esc(t.topic)} <b>${t.right}/${t.n}</b></span>`).join('')}</div>` : ''}
        ${r.tips && r.tips.length ? `<ul class="pl-tips">${r.tips.slice(0, multi ? 2 : 4).map(t => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
        ${lv ? `<div class="pl-lv"><span>乐园 Lv.${lv.level}</span><i><b style="width:${lv.pct}%"></b></i><small>再答对 ${lv.to_next} 题升级</small></div>` : ''}
        ${r.unlimited ? '' : `<p class="pl-left">今天还能玩 ${Math.floor((r.seconds_left || 0) / 60)} 分钟</p>`}`;
    }
    function showEnd(h, res, p) {
      state = 'end';
      endEl.querySelector('.pl-sum').innerHTML = h;
      const row = endEl.querySelector('.pl-row');
      const n = (res && res.n) || p.n, next = res && res.next;
      if (room.host) {
        const again = TOTAL ? (res && res.stars ? '再打一次' : '再试一次') : '再来一局';
        row.innerHTML = `${next ? `<button type="button" class="btn lg pl-next" data-n="${next}">下一关 →</button>` : ''}
          <button type="button" class="btn lg pl-again" data-n="${n}">${again}</button>
          ${multi || TOTAL ? `<button type="button" class="btn ghost lg mp-back">${multi ? '回房间' : '选关'}</button>` : ''}
          ${multi ? '' : `<a class="btn ghost lg" href="${BACK}">${BACK_TEXT}</a>`}`;
        row.querySelectorAll('[data-n]').forEach(b => b.onclick = async () => {
          if (TOTAL) { try { room = await roomApi('level', {n: +b.dataset.n}); } catch (e) { sh.toast(e.message, 'warn'); return; } }
          startNew(+b.dataset.n);
        });
      } else {
        row.innerHTML = `<p class="mp-note grow">等 <b>${esc(nameOf(room.members.find(m => m.host).id))}</b> 开始下一局…</p><button type="button" class="btn ghost lg mp-back">回房间</button>`;
      }
      const back = row.querySelector('.mp-back');
      if (back) back.onclick = () => { endEl.hidden = true; state = 'room'; page = 0; refresh().then(showRoom); };
      endEl.hidden = false;
    }
    function stickers(list) {
      const pop = document.createElement('div'); pop.className = 'pl-sticker-pop';
      pop.innerHTML = `<div class="pl-sticker-card"><div class="pl-label">得到新贴纸！</div>${list.map(s =>
        `<div class="pl-sticker"><span class="ico">${s.icon}</span><b>${esc(s.name)}</b><small>${esc(s.how)}</small>${s.avatar ? '<em>解锁了新角色，回乐园首页换上吧</em>' : ''}</div>`).join('')}
        <button type="button" class="btn lg block">太棒了！</button></div>`;
      document.body.appendChild(pop); Play.sfx.play('sticker');
      pop.querySelector('button').onclick = () => pop.remove();
    }

    // ---------------------------------------------------------------- 打开页面：回到我的房间（刷新了、换了设备），正在玩就接着玩
    if (data.locked) over.classList.add('locked');
    if (!multi) { panel.innerHTML = `<div class="pl-title">${esc(G.icon)} ${esc(G.name)}</div>`; enter(post('/api/rooms', {game: G.id})); }
    else if (data.room) { panel.innerHTML = '<div class="pl-title">正在回到房间…</div>'; enter(post(`/api/rooms/${data.room}/enter`)); }
    else showLobby();
    return sh;
  };

  function deviceId() {   // 这个页面的设备号：刷新不变，另一个标签页、另一台设备不一样
    let id = null;
    try { id = sessionStorage.getItem('play.device'); } catch (e) { /* 隐私模式 */ }
    if (!id) { id = Math.random().toString(36).slice(2, 12); try { sessionStorage.setItem('play.device', id); } catch (e) { /* 隐私模式 */ } }
    return id;
  }
})();

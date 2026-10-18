/* 联网游戏的连接层（所有联网游戏共用，不依赖任何库）。服务器只转发（app/routes/play.py /ws/room/<房间>），不模拟游戏。
     const net = new MpNet(roomId);
     net.on('s', m => …);         // 收到某种消息（m.from 是发送人 id，服务器盖的，改不了）
     net.on('*', m => …);         // 收到任何消息；net.off('s', 'gem') 清掉
     net.onStatus = ok => …;      // 连上 / 断开（断了会自己重连：1、2、4… 秒，最多 8 秒）
     net.send({t: 'gem', i: 3});  // 发给房间里的其他人（t 是消息类型）
     net.stream(() => ({t: 's', x, y}), 15);   // 每秒发 15 次（位置这类连续状态）
   MpNet.Track：对方 / 怪兽的位置。收到就 push，画的时候 get() 拿 100 毫秒前的插值位置（看起来顺滑，不会一顿一顿）。
   MpNet.rng(seed)：同一个种子两台设备得到同样的随机数（关卡里砖块掉什么道具这类）。
   怎么分工：每个人的角色由自己的设备算（操作没有延迟），发位置给对方；怪兽、豆子、砖块这些共享的东西由开房间的设备算，
   另一台跟着显示。 */
class MpNet {
  constructor(roomId) {
    this.roomId = roomId; this.handlers = {}; this.queue = []; this.closed = false; this.tries = 0; this.ok = false;
    this.onStatus = null; this.streams = [];
    this.connect();
    this.pinger = setInterval(() => this.send({t: 'ping'}), 10000);
  }
  connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    try { ws = new WebSocket(`${proto}//${location.host}/ws/room/${this.roomId}`); } catch (e) { return this.retry(); }
    this.ws = ws;
    ws.onopen = () => {
      this.tries = 0; this.ok = true; this.everOk = true;
      for (const q of this.queue.splice(0)) ws.send(q);
      this.onStatus && this.onStatus(true);
    };
    ws.onmessage = e => {
      let m; try { m = JSON.parse(e.data); } catch (err) { return; }
      (this.handlers[m.t] || []).forEach(f => f(m));
      (this.handlers['*'] || []).forEach(f => f(m));
    };
    ws.onclose = e => {
      const was = this.ok; this.ok = false;
      if (was) this.onStatus && this.onStatus(false);
      if (e.code === 4403) { this.closed = true; this.onStatus && this.onStatus(false, '这个房间进不去了'); return; }
      this.retry();
    };
    ws.onerror = () => { /* onclose 会处理 */ };
  }
  retry() {
    if (this.closed) return;
    this.tries++;
    if (this.tries === 4 && !this.everOk && this.onStatus) this.onStatus(false, 'ws');   // 一次都没连上：多半是服务器没开 WebSocket 转发
    clearTimeout(this.rt); this.rt = setTimeout(() => this.connect(), Math.min(8000, 500 * Math.pow(2, this.tries)));
  }
  on(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); return this; }
  off(...ts) { ts.forEach(t => delete this.handlers[t]); return this; }   // 新的一关开始前清掉上一关的游戏消息
  send(obj) {
    const s = JSON.stringify(obj);
    if (this.ws && this.ws.readyState === 1) this.ws.send(s);
    else if (obj.t !== 's' && obj.t !== 'ping' && obj.t !== 'w') this.queue.push(s);   // 位置这类过时就没用，不排队
  }
  stream(fn, hz) {
    const id = setInterval(() => { const m = fn(); if (m) this.send(m); }, 1000 / hz);
    this.streams.push(id); return id;
  }
  stopStreams() { this.streams.forEach(clearInterval); this.streams = []; }
  close() { this.closed = true; this.stopStreams(); clearInterval(this.pinger); clearTimeout(this.rt); try { this.ws && this.ws.close(); } catch (e) { /* 已经关了 */ } }
}

MpNet.Track = class {
  constructor(delay) { this.buf = []; this.delay = delay ?? 100; }
  push(s) { s._t = performance.now(); this.buf.push(s); if (this.buf.length > 30) this.buf.shift(); }
  get last() { return this.buf[this.buf.length - 1] || null; }
  get age() { const l = this.last; return l ? performance.now() - l._t : Infinity; }
  /* 拿 delay 毫秒之前的位置（在两次收到之间插值）；keys 是要插值的数字字段，其他字段取较新的那次。 */
  get(keys) {
    const b = this.buf; if (!b.length) return null;
    const t = performance.now() - this.delay;
    if (t <= b[0]._t) return b[0];
    for (let i = b.length - 1; i > 0; i--) {
      const a = b[i - 1], c = b[i];
      if (t >= a._t) {
        if (t >= c._t) return c;
        const k = (t - a._t) / Math.max(1, c._t - a._t), o = Object.assign({}, c);
        for (const key of keys || ['x', 'y']) if (typeof a[key] === 'number' && typeof c[key] === 'number') o[key] = a[key] + (c[key] - a[key]) * k;
        return o;
      }
    }
    return b[b.length - 1];
  }
  clear() { this.buf = []; }
};

MpNet.rng = function (seed) {   // mulberry32
  let a = (seed >>> 0) || 1;
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
};

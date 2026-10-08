// beejoy 划词查词：在网页上选中文字后显示「查」按钮和释义气泡
(() => {
  if (window.__aistudy) return;
  window.__aistudy = true;

  const host = document.createElement("div");
  host.style.cssText = "position:absolute;top:0;left:0;z-index:2147483647;";
  const root = host.attachShadow({mode: "open"});
  root.innerHTML = `<style>
    *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
    .btn{position:absolute;padding:4px 10px;border-radius:99px;border:none;background:#2f6f5e;color:#fff;font-size:13px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25)}
    .bub{position:absolute;width:320px;max-width:calc(100vw - 20px);background:#fff;color:#22252b;border:1px solid #e6e2d8;border-radius:14px;padding:14px;font-size:14px;line-height:1.5;box-shadow:0 8px 30px rgba(0,0,0,.18);animation:pop .2s ease-out}
    @keyframes pop{from{transform:scale(.9);opacity:0}to{transform:none;opacity:1}}
    .w{font-size:20px;font-weight:700}.ph{color:#6b7079;margin-left:6px}.pos{color:#2f6f5e;margin-right:6px}
    .m{font-size:16px;margin:6px 0}.sub{color:#6b7079;font-size:13px}.ex{background:#f7f5f0;border-radius:8px;padding:6px 8px;margin-top:8px}
    .row{display:flex;gap:8px;align-items:center;margin-top:10px}
    button.a{border:1px solid #2f6f5e;background:#2f6f5e;color:#fff;border-radius:99px;padding:6px 14px;cursor:pointer;font-size:14px}
    button.g{border:1px solid #e6e2d8;background:#fff;color:#22252b;border-radius:99px;padding:6px 12px;cursor:pointer;font-size:14px}
    button:disabled{opacity:.6;cursor:default}.err{color:#b4442b}.x{position:absolute;right:10px;top:6px;border:none;background:none;font-size:18px;color:#999;cursor:pointer}
    .tag{display:inline-block;background:#e9f2ee;color:#2f6f5e;border-radius:99px;padding:0 8px;margin:2px 4px 0 0;font-size:12px}
    @media (prefers-color-scheme:dark){.bub{background:#1f2228;color:#e8e8ea;border-color:#30343c}.ex{background:#16181c}button.g{background:#1f2228;color:#e8e8ea;border-color:#30343c}}
  </style><div id="box"></div>`;
  const box = root.getElementById("box");
  let mounted = false, lastSel = null, bubbleOn = true;
  const mount = () => { if (!mounted) { document.documentElement.appendChild(host); mounted = true; } };
  const clear = () => { box.innerHTML = ""; };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const send = msg => new Promise(res => chrome.runtime.sendMessage(msg, r => res(r || {ok: false, error: chrome.runtime.lastError?.message || "插件出错"})));
  send({type: "settings"}).then(r => { if (r.ok) bubbleOn = r.data.bubble !== false; });

  function say(text, lang) {
    if (!window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(text); u.lang = lang === "zh" ? "zh-CN" : "en-US"; u.rate = .85;
    speechSynthesis.cancel(); speechSynthesis.speak(u);
  }

  function selectionInfo() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    const text = sel.toString().trim();
    if (!text || text.length > 80 || text.split(/\s+/).length > 6) return null;
    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    let context = "";
    const node = range.startContainer.nodeType === 3 ? range.startContainer.parentElement : range.startContainer;
    if (node) context = (node.closest("p,li,td,h1,h2,h3,h4,div,span") || node).innerText || "";
    context = context.replace(/\s+/g, " ").slice(0, 400);
    return {text, context, x: rect.left + window.scrollX, y: rect.bottom + window.scrollY, top: rect.top + window.scrollY};
  }

  function showButton(info) {
    mount(); clear();
    const b = document.createElement("button");
    b.className = "btn"; b.textContent = "🔍 查";
    b.style.left = Math.max(4, info.x) + "px"; b.style.top = (info.y + 6) + "px";
    b.onmousedown = e => { e.preventDefault(); e.stopPropagation(); };
    b.onclick = e => { e.stopPropagation(); lookup(info); };
    box.appendChild(b);
  }

  async function lookup(info) {
    mount(); clear();
    const bub = document.createElement("div");
    bub.className = "bub";
    const left = Math.min(Math.max(8, info.x), window.scrollX + document.documentElement.clientWidth - 330);
    bub.style.left = left + "px"; bub.style.top = (info.y + 8) + "px";
    bub.innerHTML = `<button class="x" title="关闭">×</button><div class="w">${esc(info.text)}</div><div class="sub">AI 正在查…</div>`;
    bub.onmousedown = e => e.stopPropagation();
    box.appendChild(bub);
    bub.querySelector(".x").onclick = clear;
    const r = await send({type: "lookup", text: info.text, context: info.context, url: location.href});
    if (!r.ok) { bub.innerHTML = `<button class="x">×</button><div class="err">${esc(r.error)}</div>`; bub.querySelector(".x").onclick = clear; return; }
    const {lang, result: d, saved} = r.data;
    const word = d.word || info.text;
    const syn = (d.synonyms || d.near || []).filter(Boolean);
    bub.innerHTML = `<button class="x" title="关闭">×</button>
      <div><span class="w">${esc(word)}</span><span class="ph">${esc(d.phonetic || d.pinyin || "")}</span></div>
      <div class="m">${d.pos ? `<span class="pos">${esc(d.pos)}</span>` : ""}${esc(d.meaning)}</div>
      ${d.simple_en ? `<div class="sub">${esc(d.simple_en)}</div>` : ""}
      ${syn.length ? `<div class="sub">近义：${syn.map(s => `<span class="tag">${esc(s)}</span>`).join("")}</div>` : ""}
      ${(d.other_meanings || []).length ? `<div class="sub">其他意思：${esc(d.other_meanings.join("；"))}</div>` : ""}
      ${d.example ? `<div class="ex">${esc(d.example)}${d.example_zh ? `<div class="sub">${esc(d.example_zh)}</div>` : ""}</div>` : ""}
      <div class="row"><button class="g say">🔊 读</button><button class="a save" ${saved ? "disabled" : ""}>${saved ? "✓ 已在单词本" : "⭐ 加入单词本"}</button></div>`;
    bub.querySelector(".x").onclick = clear;
    bub.querySelector(".say").onclick = () => say(word, lang);
    if (lang !== "zh") say(word, lang);
    const sv = bub.querySelector(".save");
    sv.onclick = async () => {
      sv.disabled = true; sv.textContent = "保存中…";
      const s = await send({type: "save", card: {word, meaning: d.meaning, phonetic: d.phonetic || d.pinyin || "", pos: d.pos || "",
        example: d.example || "", example_zh: d.example_zh || "", context: info.context, url: location.href}});
      sv.textContent = s.ok ? "✓ 已加入，明天开始复习" : "保存失败：" + s.error;
      if (!s.ok) sv.disabled = false;
    };
  }

  document.addEventListener("mouseup", e => {
    if (host.contains(e.target) || e.composedPath().includes(host)) return;
    setTimeout(() => {
      const t = e.target;
      if (t && (t.closest?.("input,textarea,[contenteditable=true]"))) return;
      const info = selectionInfo();
      lastSel = info;
      if (info && bubbleOn) showButton(info); else if (!info) clear();
    }, 10);
  });
  document.addEventListener("mousedown", e => { if (!e.composedPath().includes(host)) clear(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") clear(); });

  chrome.runtime.onMessage.addListener(msg => {
    if (msg.type !== "show") return;
    const info = selectionInfo() || lastSel || {text: msg.text, context: "", x: window.scrollX + 40, y: window.scrollY + 40};
    info.text = msg.text || info.text;
    lookup(info);
  });
})();

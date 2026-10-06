// AIStudy 划词查词：后台负责调用 AIStudy 服务器（带连接码），以及右键菜单
importScripts("config.js");

async function settings() {
  const s = await chrome.storage.sync.get({server: AISTUDY_DEFAULT_SERVER, token: "", bubble: true});
  s.server = (s.server || "").replace(/\/+$/, "");
  return s;
}

async function call(path, body) {
  const s = await settings();
  if (!s.server || !s.token) throw new Error("还没有设置：点浏览器右上角的 AIStudy 图标 → 设置，填网站地址和连接码");
  const r = await fetch(s.server + path, {
    method: body ? "POST" : "GET",
    headers: Object.assign({"Authorization": "Bearer " + s.token}, body ? {"Content-Type": "application/json"} : {}),
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || data.detail || ("服务器出错 " + r.status));
  return data;
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const run = {
    lookup: () => call("/ext/lookup", {text: msg.text, context: msg.context, url: msg.url}),
    save: () => call("/ext/save", msg.card),
    me: () => call("/ext/me"),
    settings: () => settings(),
  }[msg.type];
  if (!run) return false;
  run().then(data => reply({ok: true, data})).catch(e => reply({ok: false, error: e.message}));
  return true;  // 异步回复
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({id: "aistudy-lookup", title: "用 AIStudy 查「%s」", contexts: ["selection"]});
  chrome.storage.sync.get({token: ""}).then(s => { if (!s.token) chrome.runtime.openOptionsPage(); });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "aistudy-lookup" && tab && tab.id >= 0) {
    chrome.tabs.sendMessage(tab.id, {type: "show", text: info.selectionText});
  }
});

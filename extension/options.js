const $ = id => document.getElementById(id);
chrome.storage.sync.get({server: AISTUDY_DEFAULT_SERVER, token: "", bubble: true}).then(s => {
  $("server").value = s.server; $("token").value = s.token; $("bubble").checked = s.bubble;
});
$("save").onclick = async () => {
  const server = $("server").value.trim().replace(/\/+$/, "");
  await chrome.storage.sync.set({server, token: $("token").value.trim(), bubble: $("bubble").checked});
  $("msg").className = ""; $("msg").textContent = "测试中…";
  chrome.runtime.sendMessage({type: "me"}, r => {
    if (r && r.ok) { $("msg").className = "ok"; $("msg").textContent = `✓ 连接成功：${r.data.name}，单词本里有 ${r.data.words} 个词。现在去任意网页选中一个单词试试！`; }
    else { $("msg").className = "err"; $("msg").textContent = "✗ " + ((r && r.error) || "连接失败"); }
  });
};

const $ = id => document.getElementById(id);
$("opt").onclick = () => chrome.runtime.openOptionsPage();
chrome.runtime.sendMessage({type: "me"}, r => {
  if (!r || !r.ok) { $("info").textContent = (r && r.error) || "还没有设置"; return; }
  $("info").innerHTML = `${r.data.name} · 单词本 ${r.data.words} 个 · 今天待复习 <b>${r.data.due_words}</b> 个`;
  chrome.runtime.sendMessage({type: "settings"}, s => {
    $("review").style.display = "block";
    $("review").onclick = () => chrome.tabs.create({url: s.data.server + "/review?group=words"});
  });
});

-- 0039 一口气（app/focus.py，docs/DESIGN.md §5.36）：把学习切成一段一段，长度按孩子自己能撑住的时间一点点加长。
-- focus_blocks：一口气一条。target_ms 这口气的目标长度；active_ms 实际专心的时间；away_ms 中间离开的总时长；
-- longest_ms 中间没离开的最长一段；held 撑住了（中间没有离开超过 15 秒、没有提前收尾）；
-- ended：target 到点结束 / early 掉线后提前收尾 / quit 没做完就走了（open 还在进行）；
-- segs：这口气里「在线 / 掉线」的分段 [[毫秒, 1在线 0掉线], ...]，孩子在结束卡上看到的那条线。
CREATE TABLE focus_blocks (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  target_ms INTEGER NOT NULL,
  active_ms INTEGER NOT NULL DEFAULT 0,
  away_ms INTEGER NOT NULL DEFAULT 0,
  longest_ms INTEGER NOT NULL DEFAULT 0,
  n_items INTEGER NOT NULL DEFAULT 0,
  n_right INTEGER NOT NULL DEFAULT 0,
  held INTEGER NOT NULL DEFAULT 0,
  ended TEXT NOT NULL DEFAULT 'open',
  segs TEXT NOT NULL DEFAULT '[]',
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_focus_blocks_user ON focus_blocks(user_id, day);
-- focus_leaves：每次离开。kind：away 切到别的页面或应用 / idle 页面开着但很久没动；
-- what：当时在做什么（after_wrong 刚答错 / waiting 在等题 / reading 在读 / item 在做题）；task：哪一项任务。
CREATE TABLE focus_leaves (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  block_id INTEGER,
  day TEXT NOT NULL,
  ms INTEGER NOT NULL,
  kind TEXT NOT NULL,
  what TEXT NOT NULL DEFAULT '',
  task TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_focus_leaves_user ON focus_leaves(user_id, day);
-- 每道题答之前，系统估计的答对概率（app/ability.py 的 expect）：「会的题突然错了」是走神的信号之一
ALTER TABLE attempts ADD COLUMN p_exp {{FLOAT}};

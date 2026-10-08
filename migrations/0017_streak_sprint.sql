-- 0017 游戏化：保底和补签卡、冲刺模式（app/streak.py、app/sprint.py）。
-- streak_freezes：补签卡。kind = earned（连续天数每满 7 天得一张，day 是满 7 天那天）
--   或 used（某天没达到保底，用卡保住连续天数，day 是被补上的那天）。手里最多留 2 张。
CREATE TABLE streak_freezes (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, day, kind)
);
-- sprint_runs：做完今天的任务以后的「冲刺」，题目不限量。一天可以冲很多次。
-- points = 冲刺分（每答对一题得「当前倍数」分）；data：待答的题、热度、倍数、开始时间。
CREATE TABLE sprint_runs (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  points INTEGER DEFAULT 0,
  answered INTEGER DEFAULT 0,
  right_n INTEGER DEFAULT 0,
  best_tier INTEGER DEFAULT 1,
  data TEXT
);
CREATE INDEX idx_sprint_runs_user ON sprint_runs(user_id, day);

-- 0009 跟自己比：每做完一组（热身、单词复习、错题回顾、知识点练习、阅读）记一笔，用来算个人最好（PB）、
-- 「上周的我」的速度、最长专注时间和每周进步卡
CREATE TABLE runs (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  day TEXT NOT NULL,
  n_items INTEGER DEFAULT 0,
  n_right INTEGER DEFAULT 0,
  ms_active INTEGER DEFAULT 0,
  ms_total INTEGER DEFAULT 0,
  best_combo INTEGER DEFAULT 0,
  pbs TEXT DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_runs_user ON runs(user_id, kind, day);

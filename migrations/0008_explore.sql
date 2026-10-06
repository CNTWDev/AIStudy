-- 0008 摸底探索：每天穿插「以前学过的」小题，慢慢摸清孩子过去的掌握情况
-- probes：要测 / 测过的旧知识点和旧单词。kind=kp（知识点，ref 为空）或 word（ref=单词，kp_id 为词表 id）
CREATE TABLE probes (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'kp',
  kp_id TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',
  reason TEXT DEFAULT '',
  from_kp TEXT DEFAULT '',
  depth INTEGER DEFAULT 0,
  priority INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued',
  result INTEGER,
  created_at TEXT NOT NULL,
  done_at TEXT
);
CREATE INDEX idx_probes_user ON probes(user_id, status, kind);
-- lights：孩子第一次真正掌握（点亮）某个知识点的日子
CREATE TABLE lights (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kp_id TEXT NOT NULL,
  day TEXT NOT NULL,
  PRIMARY KEY (user_id, kp_id)
);
CREATE INDEX idx_lights_day ON lights(user_id, day);

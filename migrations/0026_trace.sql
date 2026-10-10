-- 0026 追根：一个知识点今天卡住了（错两次 / 点「还不会」），当场用几道小题沿前置往回找「根」在哪。
-- steps：每一步测了什么、对没对（JSON 列表）；result：root（根在某个前置）/ self（基础没问题，是这个点本身）/ lang（中文会、英文不会：根在术语）
CREATE TABLE traces (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kp_id TEXT NOT NULL,
  day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  item_id TEXT DEFAULT '',
  hint_kp TEXT DEFAULT '',
  steps TEXT DEFAULT '[]',
  root_kp TEXT DEFAULT '',
  result TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  done_at TEXT
);
CREATE INDEX idx_traces_user ON traces(user_id, day);

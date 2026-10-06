-- 0003 每日任务：阅读/单词进度（tracks）、阅读记录、做题「还不会」、每日心情
-- tracks.kind: read_zh（中文名著接着读）/ read_en（英文分级读物）/ words（每天学新词）
CREATE TABLE tracks (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  ref TEXT DEFAULT '',
  title TEXT NOT NULL,
  unit_name TEXT DEFAULT '章',
  units TEXT DEFAULT '[]',
  total_units INTEGER DEFAULT 0,
  position INTEGER DEFAULT 0,
  daily_amount INTEGER DEFAULT 1,
  daily_minutes INTEGER DEFAULT 15,
  active INTEGER DEFAULT 1,
  last_day TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX idx_tracks_user ON tracks(user_id, active);
CREATE TABLE reading_logs (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  track_id INTEGER,
  day TEXT NOT NULL,
  title TEXT DEFAULT '',
  from_pos INTEGER DEFAULT 0,
  to_pos INTEGER DEFAULT 0,
  pages TEXT DEFAULT '',
  minutes INTEGER DEFAULT 0,
  summary TEXT DEFAULT '',
  feeling TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_reading_logs_user ON reading_logs(user_id, day);
ALTER TABLE attempts ADD COLUMN dont_know INTEGER DEFAULT 0;
ALTER TABLE days ADD COLUMN mood TEXT DEFAULT '';
CREATE INDEX idx_cards_created ON cards(user_id, created_at);
CREATE INDEX idx_lookups_user ON lookups(user_id, created_at);
-- 浏览器划词插件等外部工具用的个人令牌（只存 sha256）
CREATE TABLE api_tokens (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  last_used TEXT
);
CREATE INDEX idx_api_tokens_user ON api_tokens(user_id);
-- 课程进度：孩子 / 家长自己更新「学校学到哪了」
ALTER TABLE enrollments ADD COLUMN progress_kp TEXT;
ALTER TABLE enrollments ADD COLUMN progress_at TEXT;
CREATE TABLE kp_taught (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kp_id TEXT NOT NULL,
  marked_at TEXT NOT NULL,
  PRIMARY KEY (user_id, kp_id)
);

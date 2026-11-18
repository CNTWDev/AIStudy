-- 0052 口语「开口练」（app/speaking.py、app/stt/，docs/DESIGN.md §5.63）：独立功能，平台核心不依赖这几张表。
-- speak_sessions：一次练习。kind = scene（一个场景走四步）/ chat（和 AI 聊下去）/ review（只做复习，保底那 3 分钟）。
--   ref：场景 id、聊天的局 id，或 own（「我有件事要说」，场景本身存在 data.scene 里，只属于本人，永远不进公共库）。
--   data：每一步的结果（第一遍、地道版、跟读、再说一遍、对比的几个数），只属于本人。
-- speak_clips：每一段录音。声音文件在 DATA_DIR/speaking/<user_id>/<id>.wav（只有本人能回听）；这里存转出来的文字、
--   发音评测结果、算出来的几个数。secs 也用来算每天送去识别了多少秒（限额）。
-- speak_chunks：公共的表达块词典。一个表达只存一份（key = 规整后的写法），挂到多个场景上；课程包里写好的和
--   从个人练习里学到的（只存表达本身和通用例句，不带个人内容）都在这里。
-- speak_memory：每个人记表达块的情况（1/2/4/7 天复习），used = 不看稿自由说时主动用出来的次数。
CREATE TABLE speak_sessions (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  pack_id TEXT NOT NULL DEFAULT '',
  ref TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '{}',
  done_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_speak_sessions_user ON speak_sessions(user_id, day);
CREATE TABLE speak_clips (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id INTEGER,
  step TEXT NOT NULL DEFAULT '',
  day TEXT NOT NULL,
  secs {{FLOAT}} NOT NULL DEFAULT 0,
  transcript TEXT NOT NULL DEFAULT '{}',
  assess TEXT NOT NULL DEFAULT '',
  metrics TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_speak_clips_user ON speak_clips(user_id, day);
CREATE TABLE speak_chunks (
  id {{ID}},
  key TEXT NOT NULL UNIQUE,
  en TEXT NOT NULL,
  zh TEXT NOT NULL DEFAULT '',
  example TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE speak_memory (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chunk_id INTEGER NOT NULL REFERENCES speak_chunks(id) ON DELETE CASCADE,
  box INTEGER NOT NULL DEFAULT 0,
  due TEXT NOT NULL,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0,
  scene TEXT NOT NULL DEFAULT '',
  learned_at TEXT NOT NULL,
  seen_at TEXT,
  PRIMARY KEY (user_id, chunk_id)
);
CREATE INDEX idx_speak_memory_due ON speak_memory(user_id, due);

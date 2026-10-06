-- 0005 「问小艾」：孩子随时提问，AI 引导式回答（不直接给答案）。一次对话 = 一个 thread
CREATE TABLE ask_threads (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  page TEXT DEFAULT '',
  title TEXT DEFAULT '',
  kp_id TEXT,
  item_id TEXT,
  context TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_ask_threads_user ON ask_threads(user_id, updated_at);
CREATE TABLE ask_messages (
  id {{ID}},
  thread_id INTEGER NOT NULL REFERENCES ask_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_ask_messages_thread ON ask_messages(thread_id, id);

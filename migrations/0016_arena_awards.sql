-- 0016 乐园贴纸：达成条件自动发（见 app/arena/awards.py），一张只得一次。角色选择存在 users.settings.avatar。
CREATE TABLE arena_awards (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);

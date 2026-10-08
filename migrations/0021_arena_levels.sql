-- 0021 乐园闯关：每关最好的星星数、打了几次、什么时候第一次过关（见 app/arena/levels.py）。
CREATE TABLE arena_levels (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  game TEXT NOT NULL,
  level INTEGER NOT NULL,
  stars INTEGER NOT NULL DEFAULT 0,
  plays INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  cleared_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, game, level)
);

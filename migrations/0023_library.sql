-- 0023 书库（内置公版名著）：每个孩子每本书读到第几页、听了多久；每天读了几页、听了几秒（每日任务用）。
-- 原文不进数据库，在 data/library/<书>.json；每章的导读（关键词、上回说到、读后小题）存在 contents（kind='book_guide'），所有孩子共用。
CREATE TABLE book_progress (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  book_id TEXT NOT NULL,
  page INTEGER DEFAULT 0,            -- 已经读完的页数（下次从 page+1 开始）
  listen_page INTEGER DEFAULT 0,     -- 听书听到第几页
  pages_read INTEGER DEFAULT 0,
  listen_seconds INTEGER DEFAULT 0,
  chapters_done TEXT DEFAULT '[]',   -- 读完并答过小题的章（贴纸）
  started_at TEXT,
  updated_at TEXT,
  finished_at TEXT,
  PRIMARY KEY (user_id, book_id)
);
CREATE TABLE book_days (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  book_id TEXT NOT NULL,
  pages INTEGER DEFAULT 0,
  listen_seconds INTEGER DEFAULT 0,
  PRIMARY KEY (user_id, day, book_id)
);

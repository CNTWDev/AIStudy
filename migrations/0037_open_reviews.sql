-- 0037 开放题的 AI 点评和追问（app/coach.py，docs/DESIGN.md §4.6）：学员写下答案，AI 按评分要点点评并追问，
-- 一次作答一条记录；rounds 记每一轮的回答、点评和追问，最后的结论（good / partial / miss）决定这次算不算答对。
CREATE TABLE open_reviews (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  kp_id TEXT NOT NULL,
  rounds TEXT NOT NULL DEFAULT '[]',
  verdict TEXT NOT NULL DEFAULT '',
  done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_open_reviews_user ON open_reviews(user_id, kp_id);

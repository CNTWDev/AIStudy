-- 0054 开口练累计「听」了多久（app/speaking.py add_listen / totals；DESIGN 5.67）：
-- 说了多久从 speak_clips.secs 加起来；听（地道版、跟读原句、回听录音）在页面上计时，按天累加到这里。
CREATE TABLE speak_listen (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  secs REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);

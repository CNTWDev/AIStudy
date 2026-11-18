-- 0053 语音引擎出错的记录（app/speaking.py record_error；DESIGN 5.67）：页面上的报错一闪就没了，
-- 这里留下最近的错误（哪家、哪一步、厂商原话、请求编号），后台系统页「语音引擎」卡片上能看到。只存错误文字，不存录音和说的内容。
CREATE TABLE stt_errors (
  id {{ID}},
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  step TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX stt_errors_created ON stt_errors(created_at);

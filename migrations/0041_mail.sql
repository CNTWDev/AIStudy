-- 0041 发信队列（app/mailer.py）：系统要发的每一封邮件先写成一行，后台 worker 再通过发信服务（SMTP）发出去。
-- kind：信的种类（weekly 每周学习报告……）；ref：同一种信里的哪一封（每周报告 = 那一周的周一），
-- 同一个人、同一种信、同一个 ref 只会有一行，worker 重启、多个进程同时跑都不会重复发。
-- status：queued 等着发 / sent 发出了 / failed 重试几次还不行，放弃了；tries 已经试了几次，next_at 下次什么时候试。
CREATE TABLE mail_outbox (
  id {{ID}},
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  to_addr TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL,
  body_text TEXT NOT NULL,
  body_html TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  tries INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  next_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE UNIQUE INDEX idx_mail_once ON mail_outbox(user_id, kind, ref);
CREATE INDEX idx_mail_due ON mail_outbox(status, next_at);

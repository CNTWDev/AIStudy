-- 0044 决策日志（app/journal.py，docs/DESIGN.md §4.8）：学员在做一个决定之前写下理由和认错条件，到期回来对照结果复盘。
-- 一个决定一条记录。pack_id：哪门课的日志（字段由课程包的 journal 声明）；subject：决定什么；fields：课程包声明的各栏 {id: 文字}；
-- confidence：几成把握（10-100）；review_on：回看日期。回看后填 reviewed_at、outcome（good 结果好 / bad 结果差）、
-- judgment（sound 当时的判断站得住 / flawed 当时的判断有问题）、traps（踩到的陷阱，课程包里的知识点 id）、review（复盘的话）。
CREATE TABLE journal (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pack_id TEXT NOT NULL,
  subject TEXT NOT NULL,
  fields TEXT NOT NULL DEFAULT '{}',
  confidence INTEGER NOT NULL DEFAULT 50,
  review_on TEXT NOT NULL,
  reviewed_at TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL DEFAULT '',
  judgment TEXT NOT NULL DEFAULT '',
  traps TEXT NOT NULL DEFAULT '[]',
  review TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_journal_user ON journal(user_id, review_on);

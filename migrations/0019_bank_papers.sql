-- 0019 公共卷库：管理员导入的真题、名校卷、名师卷、回忆版（见 app/bankpapers.py）。
-- 导入后是草稿（题目 status='draft'，不出给学习者），管理员逐题核对后发布：题目进公共题库（items.source='bank'），
-- 练习时优先出；学习者也可以把整套卷子当模拟考做（复用试卷流程，papers.bank_paper_id 指回来源）。
-- 卷子只存在服务器的数据库和数据目录里，不进代码仓库。
CREATE TABLE bank_papers (
  id {{ID}},
  pack_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  stage TEXT DEFAULT '',
  title TEXT NOT NULL,
  exam TEXT DEFAULT '',
  year TEXT DEFAULT '',
  region TEXT DEFAULT '',
  org TEXT DEFAULT '',
  minutes INTEGER DEFAULT 0,
  images TEXT DEFAULT '[]',
  raw_text TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  status TEXT DEFAULT 'draft',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  published_at TEXT
);
CREATE INDEX idx_bank_papers_pack ON bank_papers(pack_id, status);
CREATE TABLE bank_paper_items (
  id {{ID}},
  bank_paper_id INTEGER NOT NULL REFERENCES bank_papers(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  label TEXT DEFAULT '',
  item_id TEXT NOT NULL,
  points {{FLOAT}},
  page INTEGER
);
CREATE INDEX idx_bank_paper_items ON bank_paper_items(bank_paper_id, seq);
ALTER TABLE papers ADD COLUMN bank_paper_id INTEGER;

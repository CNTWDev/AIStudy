-- 0004 试卷：拍照 / 粘贴导入 → AI 拆题并对应知识点 → 原卷得失分 + 在线重做 → 诊断
-- papers.status: ready（已解析，待做）/ done（已出诊断）
CREATE TABLE papers (
  id {{ID}},
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pack_id TEXT NOT NULL,
  title TEXT NOT NULL,
  exam_date TEXT DEFAULT '',
  source TEXT DEFAULT 'photo',
  images TEXT DEFAULT '[]',
  raw_text TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  status TEXT DEFAULT 'ready',
  report TEXT DEFAULT '{}',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX idx_papers_user ON papers(user_id, created_at);
-- 卷子里的每道题；题目本身存在 items 表（source='paper'），这里记题号、分值、原卷对错和在线作答
CREATE TABLE paper_items (
  id {{ID}},
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  label TEXT DEFAULT '',
  item_id TEXT NOT NULL,
  kp_id TEXT,
  points {{FLOAT}},
  page INTEGER,
  orig TEXT DEFAULT '',
  orig_answer TEXT DEFAULT '',
  answer TEXT,
  correct INTEGER,
  dont_know INTEGER DEFAULT 0,
  flagged INTEGER DEFAULT 0,
  answered_at TEXT
);
CREATE INDEX idx_paper_items_paper ON paper_items(paper_id, seq);

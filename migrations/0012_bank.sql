-- 0012 题库沉淀：AI 现场生成的题目、讲解、背景、短文全部存下来，挂到知识点上，以后先用库里的。
-- items：补上语言、年级、出题目的、生成记录（模型 / 提示词版本）、去重指纹、状态和做题统计。
-- status：active（在用）/ review（被标记有问题，暂停使用，等管理员看）/ retired（下架）
ALTER TABLE items ADD COLUMN lang TEXT DEFAULT '';
ALTER TABLE items ADD COLUMN grade TEXT DEFAULT '';
ALTER TABLE items ADD COLUMN purpose TEXT DEFAULT '';
ALTER TABLE items ADD COLUMN gen_meta TEXT DEFAULT '{}';
ALTER TABLE items ADD COLUMN qhash TEXT;
ALTER TABLE items ADD COLUMN status TEXT DEFAULT 'active';
ALTER TABLE items ADD COLUMN n_attempts INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN n_correct INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN n_dont_know INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN n_timed INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN total_ms BIGINT DEFAULT 0;
ALTER TABLE items ADD COLUMN n_flags INTEGER DEFAULT 0;
ALTER TABLE items ADD COLUMN updated_at TEXT;
CREATE INDEX idx_items_qhash ON items(qhash);
UPDATE items SET
  n_attempts = (SELECT COUNT(*) FROM attempts a WHERE a.item_id = items.id),
  n_correct = (SELECT COUNT(*) FROM attempts a WHERE a.item_id = items.id AND a.correct = 1),
  n_dont_know = (SELECT COUNT(*) FROM attempts a WHERE a.item_id = items.id AND a.dont_know = 1),
  n_timed = (SELECT COUNT(*) FROM attempts a WHERE a.item_id = items.id AND a.ms IS NOT NULL),
  total_ms = (SELECT COALESCE(SUM(a.ms), 0) FROM attempts a WHERE a.item_id = items.id);

-- 一道题考哪些知识点（取代 items.kp_ids 上的 LIKE 查询）。role：main 主考点 / also 也涉及
CREATE TABLE item_kps (
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kp_id TEXT NOT NULL,
  role TEXT DEFAULT 'main',
  PRIMARY KEY (item_id, kp_id)
);
CREATE INDEX idx_item_kps_kp ON item_kps(kp_id);
INSERT INTO item_kps(item_id, kp_id, role) SELECT id, kp_id, 'main' FROM items;

-- 题目以外的生成内容：teach 分步讲解 / context 背景和用处 / passage 阅读短文（含阅读题）。
-- body 是 JSON；qhash 去重；uses 被用了几次。和孩子个人有关的部分（比如「你在数学里学过……」）不存在这里，用的时候再拼。
CREATE TABLE contents (
  id {{ID}},
  kind TEXT NOT NULL,
  kp_id TEXT DEFAULT '',
  lang TEXT DEFAULT '',
  grade TEXT DEFAULT '',
  topic TEXT DEFAULT '',
  title TEXT DEFAULT '',
  body TEXT NOT NULL,
  origin TEXT DEFAULT 'ai',
  gen_meta TEXT DEFAULT '{}',
  qhash TEXT NOT NULL,
  uses INTEGER DEFAULT 0,
  n_flags INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active',
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT
);
CREATE UNIQUE INDEX idx_contents_qhash ON contents(qhash);
CREATE INDEX idx_contents_kp ON contents(kind, kp_id);
CREATE INDEX idx_contents_lang ON contents(kind, lang, grade);

-- 孩子 / 家长标记「这道题有问题」。两个不同的人标记过就暂停使用，等管理员处理。
CREATE TABLE flags (
  id {{ID}},
  user_id INTEGER NOT NULL,
  target TEXT NOT NULL,
  target_id TEXT NOT NULL,
  reason TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE (user_id, target, target_id)
);
CREATE INDEX idx_flags_target ON flags(target, target_id);

ALTER TABLE readings ADD COLUMN content_id INTEGER;

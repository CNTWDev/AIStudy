-- 0047 迭代日记（app/diary.py，docs/DESIGN.md §5.50）：系统每一次重要的调整，背后是对孩子的一次观察、对学习的一次理解。
-- 日记由 Claude 写、管理员读和定稿；内容是家里的私事，不进公开仓库：放在服务器 DATA_DIR/diary/*.md，或在后台上传，读进这张表。
-- slug：文件名（不带 .md），同一篇日记的身份；day：这件事发生的那天；quote：放在最上面的那一句；changes：那天改了什么；
-- prs：对应的 PR 号（JSON 数组）；tags：标签（JSON 数组）；status：draft 草稿 / kept 收下 / dropped 不要；
-- audience：谁能看（现在只有 admin，以后可以开放给家里人或所有人）；edited：在后台改过的，文件再变也不覆盖。
CREATE TABLE diary (
  id {{ID}},
  slug TEXT UNIQUE NOT NULL,
  day TEXT NOT NULL,
  title TEXT NOT NULL,
  quote TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  changes TEXT NOT NULL DEFAULT '',
  prs TEXT NOT NULL DEFAULT '[]',
  tags TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft',
  audience TEXT NOT NULL DEFAULT 'admin',
  edited INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_diary_day ON diary(day);

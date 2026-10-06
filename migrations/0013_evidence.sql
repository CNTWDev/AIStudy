-- 0013 掌握判定升级：多证据交叉验证 + 遗忘模型（见 app/evidence.py）
-- mastery.score 现在是「真懂的概率」（贝叶斯知识追踪 BKT）；
-- stability：记忆稳定性（天），越大忘得越慢；difficulty：这个知识点对这个孩子有多难（1-10）；
-- last_ev：最近一次有作答证据的时间；evidence：答对过的题、答对的日子、题型（JSON），用来判断是不是交叉验证过。
ALTER TABLE mastery ADD COLUMN stability {{FLOAT}} DEFAULT 0;
ALTER TABLE mastery ADD COLUMN difficulty {{FLOAT}} DEFAULT 5;
ALTER TABLE mastery ADD COLUMN last_ev TEXT;
ALTER TABLE mastery ADD COLUMN evidence TEXT;
-- 复习卡片也改用遗忘模型排期（不再是固定的 1/2/4/7 天）
ALTER TABLE cards ADD COLUMN stability {{FLOAT}} DEFAULT 0;
ALTER TABLE cards ADD COLUMN difficulty {{FLOAT}} DEFAULT 5;

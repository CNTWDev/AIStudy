-- 0011 学习时长自动记录：页面上真正在学的秒数（有操作、页面在前台；发呆、切走不算），
-- 加上孩子自己登记的线下学习（读纸质书等）。days.minutes = 两者之和，不再手填。
ALTER TABLE days ADD COLUMN active_seconds INTEGER DEFAULT 0;
ALTER TABLE days ADD COLUMN offline_minutes INTEGER DEFAULT 0;
ALTER TABLE days ADD COLUMN first_at TEXT;
ALTER TABLE days ADD COLUMN last_at TEXT;

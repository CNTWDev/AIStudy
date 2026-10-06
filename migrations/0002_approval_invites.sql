-- 0002 账号审批与邀请关系
-- users.status 增加 pending（等待审批）/ rejected（未通过）
ALTER TABLE users ADD COLUMN invited_by INTEGER;
ALTER TABLE users ADD COLUMN invite_code TEXT;
ALTER TABLE users ADD COLUMN approved_at TEXT;
ALTER TABLE users ADD COLUMN approved_by INTEGER;
ALTER TABLE users ADD COLUMN apply_note TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN last_active_at TEXT;
CREATE INDEX idx_users_invited_by ON users(invited_by);
CREATE INDEX idx_users_status ON users(status);

-- 0007 临时密码：管理员给账号设了临时密码后，对方下次登录必须先改成自己的密码
ALTER TABLE users ADD COLUMN must_change_pw INTEGER NOT NULL DEFAULT 0;

-- 0033 联网游戏的进度存档（docs/DESIGN.md 5.25）：正在玩的这一关、两个人各自的状态、共享的世界。
-- 每台设备每 2 秒存一次；刷新页面、换一台设备进来时从这里接着玩。这一关结束就清空。
ALTER TABLE mp_rooms ADD COLUMN play TEXT;

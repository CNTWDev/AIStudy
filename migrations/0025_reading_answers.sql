-- 0025 阅读小题的作答存下来：当天再进这篇文章，直接看到自己的答案、对错和解析，不用再做一遍、也不会再生成一篇。
ALTER TABLE readings ADD COLUMN answers TEXT;

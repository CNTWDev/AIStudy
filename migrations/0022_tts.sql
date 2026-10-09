-- 0022 朗读缓存：每段文字（规整后连同声音、语速等算 MD5）一条记录，音频文件在 data/tts/ab/cd/<key>.mp3。
-- status：pending（登记了还没生成）/ ready（有文件）/ failed（上次生成失败，下次播放会重试）
CREATE TABLE tts_clips (
  key TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  lang TEXT,
  voice TEXT,
  model TEXT,
  speed {{FLOAT}},
  chars INTEGER DEFAULT 0,
  fmt TEXT,
  status TEXT DEFAULT 'pending',
  bytes INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  hits INTEGER DEFAULT 0,
  created_by TEXT,
  created_at {{FLOAT}},
  generated_by TEXT,
  generated_at {{FLOAT}},
  last_played_at {{FLOAT}}
);
CREATE INDEX idx_tts_generated ON tts_clips(generated_at);

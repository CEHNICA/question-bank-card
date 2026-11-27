-- 题有据社区 D1 表结构。可以重复执行：只建不存在的表，不删数据。
-- 时间统一存 ISO 8601（UTC）文本；会话和限流用毫秒时间戳方便比较。

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
  bio TEXT NOT NULL DEFAULT '',
  -- 收款方式由作者自己填，只给登录用户在付费题包页看；平台不经手钱。
  pay_note TEXT NOT NULL DEFAULT '',
  pay_qr_key TEXT,
  banned_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

-- 题包：先审后发。status 只有 approved 才对外可见。
CREATE TABLE IF NOT EXISTS packs (
  id TEXT PRIMARY KEY,
  author_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  stage TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  year INTEGER,
  tags_text TEXT NOT NULL DEFAULT '',
  -- 价格以分为单位；付费题包必须声明原创且不带原卷。
  price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
  originality TEXT NOT NULL DEFAULT 'compiled' CHECK (originality IN ('original', 'compiled')),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('uploading', 'pending', 'approved', 'rejected', 'taken_down', 'withdrawn')),
  review_note TEXT NOT NULL DEFAULT '',
  r2_key TEXT NOT NULL,
  cover_key TEXT,
  r2_purged_at TEXT,
  cold_key TEXT,
  r2_cached INTEGER NOT NULL DEFAULT 1,
  last_downloaded_at TEXT,
  archived_at TEXT,
  archive_error TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  question_count INTEGER NOT NULL DEFAULT 0,
  paper_count INTEGER NOT NULL DEFAULT 0,
  include_originals INTEGER NOT NULL DEFAULT 0,
  rights_confirmed_at TEXT NOT NULL,
  downloads INTEGER NOT NULL DEFAULT 0,
  reviewed_by INTEGER REFERENCES users(id),
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  CHECK (price_cents = 0 OR (originality = 'original' AND include_originals = 0))
);
CREATE INDEX IF NOT EXISTS packs_status_created ON packs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS packs_author ON packs(author_id);
CREATE INDEX IF NOT EXISTS packs_r2_active ON packs(r2_purged_at, status, created_at);
CREATE INDEX IF NOT EXISTS packs_hot_lru ON packs(r2_cached, r2_purged_at, status, last_downloaded_at, created_at);

-- 预览由桌面版导出时生成（community-preview.json），网站只存清洗后的副本，不解析 sqlite。
CREATE TABLE IF NOT EXISTS pack_previews (
  pack_id TEXT PRIMARY KEY REFERENCES packs(id) ON DELETE CASCADE,
  preview_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  author_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  pack_id TEXT REFERENCES packs(id),
  status TEXT NOT NULL DEFAULT 'visible' CHECK (status IN ('visible', 'hidden')),
  like_count INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS posts_status_created ON posts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS posts_pack ON posts(pack_id);

CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  author_id INTEGER NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'visible' CHECK (status IN ('visible', 'hidden')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS comments_post ON comments(post_id, created_at);

CREATE TABLE IF NOT EXISTS likes (
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  PRIMARY KEY (post_id, user_id)
);

-- 兑换码只存哈希；明文只在生成那一刻返回给作者一次。hint 是末四位，方便作者对账。
CREATE TABLE IF NOT EXISTS redeem_codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id TEXT NOT NULL REFERENCES packs(id),
  code_hash TEXT NOT NULL UNIQUE,
  hint TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  used_by INTEGER REFERENCES users(id),
  used_at TEXT,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS redeem_codes_pack ON redeem_codes(pack_id, batch_id);

CREATE TABLE IF NOT EXISTS entitlements (
  user_id INTEGER NOT NULL REFERENCES users(id),
  pack_id TEXT NOT NULL REFERENCES packs(id),
  source TEXT NOT NULL DEFAULT 'redeem' CHECK (source IN ('redeem', 'purchase', 'grant')),
  code_id INTEGER REFERENCES redeem_codes(id),
  request_id INTEGER REFERENCES purchase_requests(id),
  -- 开通那一刻的标价，只用来给作者估算收入；实际收款以作者自己的记录为准。
  price_cents INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  PRIMARY KEY (user_id, pack_id)
);
CREATE INDEX IF NOT EXISTS entitlements_pack ON entitlements(pack_id);

-- 买家站外付款后点「我已付款」，作者核对到账后开通。平台只记这条通知，不碰钱。
CREATE TABLE IF NOT EXISTS purchase_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id TEXT NOT NULL REFERENCES packs(id),
  buyer_id INTEGER NOT NULL REFERENCES users(id),
  author_id INTEGER NOT NULL REFERENCES users(id),
  price_cents INTEGER NOT NULL,
  buyer_note TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'granted', 'declined', 'cancelled')),
  decline_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS purchase_requests_author ON purchase_requests(author_id, status, created_at);
CREATE INDEX IF NOT EXISTS purchase_requests_buyer ON purchase_requests(buyer_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS purchase_requests_one_pending
  ON purchase_requests(buyer_id, pack_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reporter_id INTEGER NOT NULL REFERENCES users(id),
  target_type TEXT NOT NULL CHECK (target_type IN ('pack', 'post', 'comment')),
  target_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
  resolution_note TEXT NOT NULL DEFAULT '',
  resolved_by INTEGER REFERENCES users(id),
  resolved_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS reports_status ON reports(status, created_at);

-- 简单限流：注册、登录失败、兑换失败各记一笔，按窗口数。
CREATE TABLE IF NOT EXISTS rate_events (
  key TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_events_key_at ON rate_events(key, at);

-- 桌面端直传授权令牌：只存 SHA-256 哈希，用户在社区网页登录后授权签发，可随时在「我的」吊销。
CREATE TABLE IF NOT EXISTS desktop_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label TEXT NOT NULL DEFAULT '题有据桌面端',
  last_used_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS desktop_tokens_user ON desktop_tokens(user_id);

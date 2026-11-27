-- 为已部署的 D1 packs 表增加 R2 热缓存 + 123 网盘 WebDAV 冷归档分层字段。
ALTER TABLE packs ADD COLUMN cold_key TEXT;
ALTER TABLE packs ADD COLUMN r2_cached INTEGER NOT NULL DEFAULT 1;
ALTER TABLE packs ADD COLUMN last_downloaded_at TEXT;
ALTER TABLE packs ADD COLUMN archived_at TEXT;
ALTER TABLE packs ADD COLUMN archive_error TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS packs_hot_lru ON packs(r2_cached, r2_purged_at, status, last_downloaded_at, created_at);

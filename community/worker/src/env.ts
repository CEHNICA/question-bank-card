import type { D1Database, Fetcher, R2Bucket } from "@cloudflare/workers-types";

export type PackStorageKey = string;

export type Env = {
  DB: D1Database;
  PACKS: R2Bucket;
  ASSETS?: Fetcher;
  SITE_NAME?: string;
  TURNSTILE_SITE_KEY?: string;
  TURNSTILE_SECRET?: string;
  DOWNLOAD_SECRET?: string;
  MAX_PACK_MB?: string;
  MAX_TOTAL_R2_MB?: string;
  MAX_TOTAL_WEBDAV_MB?: string;
  USER_STORAGE_QUOTA_MB?: string;
  MAX_DAILY_UPLOADS_PER_USER?: string;
  PACK_STORAGE_MODE?: string;
  WEBDAV_BASE_URL?: string;
  WEBDAV_DIR?: string;
  WEBDAV_USERNAME?: string;
  WEBDAV_PASSWORD?: string;
  WEBDAV_FETCH?: typeof fetch;
};

export type SessionUser = {
  id: number;
  username: string;
  display_name: string;
  role: "user" | "admin";
  banned_at: string | null;
};

export type AppBindings = {
  Bindings: Env;
  Variables: { user: SessionUser | null };
};

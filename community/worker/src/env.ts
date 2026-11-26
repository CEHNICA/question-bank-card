import type { D1Database, Fetcher, R2Bucket } from "@cloudflare/workers-types";

export type Env = {
  DB: D1Database;
  PACKS: R2Bucket;
  ASSETS?: Fetcher;
  SITE_NAME?: string;
  TURNSTILE_SITE_KEY?: string;
  TURNSTILE_SECRET?: string;
  DOWNLOAD_SECRET?: string;
  MAX_PACK_MB?: string;
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

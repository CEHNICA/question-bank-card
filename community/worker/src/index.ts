import { Hono } from "hono";
import type { AppBindings } from "./env.ts";
import { HttpError } from "./util.ts";
import { authRoutes, loadSessionUser } from "./auth.ts";
import { packRoutes } from "./packs.ts";
import { postRoutes } from "./posts.ts";
import { redeemRoutes } from "./redeem.ts";
import { salesRoutes } from "./sales.ts";
import { adminRoutes } from "./admin.ts";

export function createApp() {
  const app = new Hono<AppBindings>();

  app.onError((error, c) => {
    if (error instanceof HttpError) {
      return c.json({ error: error.message }, error.status as 400);
    }
    console.error(error);
    return c.json({ error: "服务器出错了，请稍后再试。" }, 500);
  });

  // 写操作只接受同源请求：带自定义头会触发跨域预检，而我们不放行任何跨域来源。
  app.use("/api/*", async (c, next) => {
    const method = c.req.method;
    if (method !== "GET" && method !== "HEAD") {
      if (c.req.header("X-TYJ") !== "1") {
        return c.json({ error: "请求来源不对。" }, 403);
      }
      const origin = c.req.header("Origin");
      if (origin && origin !== new URL(c.req.url).origin) {
        return c.json({ error: "请求来源不对。" }, 403);
      }
    }
    c.set("user", await loadSessionUser(c));
    await next();
    c.header("Cache-Control", "no-store");
  });

  app.get("/api/config", (c) =>
    c.json({
      site_name: c.env.SITE_NAME || "题有据社区",
      turnstile_site_key: c.env.TURNSTILE_SITE_KEY || "",
      max_pack_mb: Number(c.env.MAX_PACK_MB || 50),
    }),
  );

  app.route("/api", authRoutes);
  app.route("/api", packRoutes);
  app.route("/api", postRoutes);
  app.route("/api", redeemRoutes);
  app.route("/api", salesRoutes);
  app.route("/api/admin", adminRoutes);

  app.all("/api/*", (c) => c.json({ error: "没有这个接口。" }, 404));
  app.all("*", (c) => (c.env.ASSETS ? c.env.ASSETS.fetch(c.req.raw as never) : c.notFound()) as never);

  return app;
}

export default createApp();

# 题有据社区

老师之间分享结构化数学题包和教学心得的网站。部署在 Cloudflare 免费套餐上，**独立于桌面版，不进安装包**。

- 题包：桌面版「导出为社区题包」生成的 zip，上传后**先审后发**；详情页只预览前 3 道题的题干，不含答案解析。
- 售卖：**只有声明原创、且不带原卷的题包能定价**（元，1～999）。作者在「我的 → 收款方式」填收款说明或上传收款码（只给登录用户看）；买家直接付给作者后点「我已付款」，作者核对到账后一键开通。兑换码保留给团购、线下发放，只在生成时显示一次，库里只存哈希。**平台不代收钱、不存余额、不抽成**；作者「我的」页按开通时标价估算收入，仅供参考。
- 表结构改动没有迁移脚本：上线前本地库可以直接删掉 `worker/.wrangler/state` 重建；上线后再改表需要另写 `ALTER TABLE`。
- 心得：Markdown + KaTeX 帖子，评论、点赞，可挂题包。
- 举报与下架、封号由管理员处理。

## 结构

| 路径 | 内容 |
| --- | --- |
| `worker/` | Hono + TypeScript 的 Worker：`/api/*` 接口，并托管网页静态资源 |
| `web/` | Vite + 原生 JS + Tailwind 前端，打包到 `web/dist` |
| `schema.sql` | D1 表结构（可重复执行，只建不删） |
| `test/` | `node --test` 测试：用 `node:sqlite` 模拟 D1、内存模拟 R2 |
| `scripts/e2e.mjs` | 对着运行中的站点走完整流程的端到端验收脚本 |

网页和接口放在**同一个 Worker**（Workers 静态资源）里，同源才能用 HttpOnly 会话 cookie；效果等同 Pages + Workers，少配一套路由。

## 本机开发

需要 Node.js 23.6 以上。

```powershell
cd community
npm install
Copy-Item worker/.dev.vars.example worker/.dev.vars   # 测试用密钥，Turnstile 永远通过
npm run db:local                                       # 建本地 D1 表
npm run dev                                            # 打包网页并启动 wrangler dev：http://127.0.0.1:8787
```

测试与类型检查：

```powershell
npm test
npm run typecheck
```

端到端验收（另开一个终端，站点保持运行；题包用桌面版对虚构数据导出）：

```powershell
node scripts/e2e.mjs http://127.0.0.1:8787 <题有据社区题包-xxx.zip> --local
```

## 上线到 Cloudflare

以下步骤要用你自己的 Cloudflare 账号，密钥只通过 `wrangler secret` 设置，不写进仓库。

1. **买一个域名**（十几元 / 年），在 Cloudflare 添加站点并把域名的 NS 改到 Cloudflare。`*.workers.dev` 在国内经常打不开，所以这一步必需；不需要 ICP 备案。
2. 登录并创建资源：

   ```powershell
   npx wrangler login
   npx wrangler d1 create tiyouju-community        # 把输出的 database_id 填进 worker/wrangler.toml
   npx wrangler r2 bucket create tiyouju-community-packs
   npm run db:remote
   ```

3. 在 Cloudflare 控制台「Turnstile」新建一个站点（填你的域名），把**站点密钥**填进 `worker/wrangler.toml` 的 `TURNSTILE_SITE_KEY`，**服务端密钥**用命令设置：

   ```powershell
   npx wrangler secret put TURNSTILE_SECRET --config worker/wrangler.toml
   npx wrangler secret put DOWNLOAD_SECRET --config worker/wrangler.toml   # 随便一段 32 位以上的随机串
   ```

4. 部署：`npm run deploy`。
5. 在 Cloudflare 控制台的 Worker →「设置 → 域和路由」添加自定义域名（如 `community.你的域名`）。
6. 注册你自己的账号，然后提升为管理员：

   ```powershell
   npx wrangler d1 execute tiyouju-community --remote --config worker/wrangler.toml --command "UPDATE users SET role = 'admin' WHERE username = '你的用户名'"
   ```

7. 用虚构题包验收：Turnstile 用的是正式密钥时，脚本无法自动注册。可以临时把站点切到 Cloudflare 官方测试密钥跑一遍 `node scripts/e2e.mjs https://community.你的域名 <zip>`（没有 `--local`，需先按第 6 步手动提升脚本注册的作者），也可以在网页上手动走一遍：上传 → 审核 → 设价 → 生成兑换码 → 另一个账号兑换 → 下载 → 桌面版「从备份合并」导入。

## 免费额度

Workers 每天 10 万次请求、D1 5 GB、R2 10 GB 存储且下载流量免费，起步够用。单个题包上限由 `MAX_PACK_MB`（默认 50）控制，不要超过 Workers 免费版 100 MB 的请求体上限。

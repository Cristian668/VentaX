# PWA CORS 代理部署说明

## 问题
ventax.pages.dev 访问 Render API 时被 CORS 拦截，产品无法加载。

## 方案
使用 Cloudflare Pages Functions 作为同源代理，前端请求 ventax.pages.dev/api/*，由 Function 转发到 Render。

## 部署步骤

1. 推送代码到 GitHub，触发 Cloudflare Pages 重新部署
2. 确保 `functions/` 和 `_routes.json` 在项目根目录（或 build output 包含它们）
3. 部署完成后访问 https://ventax.pages.dev/pwa_cart/ 验证
4. 清除浏览器缓存或 Ctrl+Shift+R 强制刷新

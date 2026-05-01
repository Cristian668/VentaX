// PWA Cart 运行时配置
// Cloudflare 部署建议：统一走同源 /api（Cloudflare Functions/Worker 代理）
// 如需手动覆盖，可设置：window.PWA_CONFIG.api_base_url = 'https://你的域名/api'
window.PWA_CONFIG = window.PWA_CONFIG || {};
// CHANGE: 版本标识（部署时可由 CI/手工改成 commit 短哈希或日期）
window.PWA_CONFIG.build_id = window.PWA_CONFIG.build_id || '2026-05-01-category-sync';
// 可选：首页分类标签顺序与颜色（默认 8 类 + 后端多出的分类自动追加）。示例：
// window.PWA_CONFIG.catalog_pinned_categories = [
//   { name: 'JUGUETES', bg: '#E91E63', color: '#fff' },
//   { name: 'NUEVA', bg: '#5C6BC0', color: '#fff' }
// ];
if (!window.PWA_CONFIG.api_base_url && typeof location !== 'undefined') {
    window.PWA_CONFIG.api_base_url = (location.origin || 'https://ventax.pages.dev') + '/api';
}
// 可选：R2 公网图片前缀（用于 /api/images 失败时自动回退）
// 例：window.PWA_CONFIG.r2_image_base_url = 'https://pub-xxxx.r2.dev';
window.PWA_CONFIG.r2_image_base_url = window.PWA_CONFIG.r2_image_base_url || 'https://pub-5f01a50f60654ab5942bacf812a6506e.r2.dev';

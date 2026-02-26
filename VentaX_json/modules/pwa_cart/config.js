// PWA Cart 运行时配置
// 留空 = 本机打开页面时用 http://127.0.0.1:5000/api（需运行「启动PWA购物车服务器.bat」）
// 部署到 ventax.pages.dev 时务必填写 api_base_url，否则无法加载产品与图片。例如：api_base_url: "https://pwa-cart-api.onrender.com/api"
// 若后端部署在 Render，改为你的实际地址，如 https://你的服务名.onrender.com/api
// CHANGE: ventax.pages.dev 用同源 /api（Function 代理），避免 CORS；本地/其他环境仍用 Render 直连
window.PWA_CONFIG = window.PWA_CONFIG || {};
if (!window.PWA_CONFIG.api_base_url && typeof location !== 'undefined') {
    var h = (location.hostname || '').toLowerCase();
    if (h === 'ventax.pages.dev' || h === 'ventaxpages.com') {
        window.PWA_CONFIG.api_base_url = (location.origin || 'https://ventax.pages.dev') + '/api';
    } else {
        window.PWA_CONFIG.api_base_url = 'https://ventax-46bs.onrender.com/api';
    }
}

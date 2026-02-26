// ===== PWA Service Worker =====

// ===== PWA Service Worker =====

const CACHE_NAME = 'ventax-cart-v11';  // CHANGE: v11 强制更新（CORS 修复后刷新）
const STATIC_CACHE_URLS = [
    './',
    './index.html',
    './styles.css',
    './app.js',
    './manifest.json'
];

// CHANGE: 检查 URL 是否可以缓存（只允许 http:// 和 https://）
function canCacheUrl(url) {
    try {
        const urlObj = new URL(url);
        const protocol = urlObj.protocol.toLowerCase();
        // 只允许 http:// 和 https:// 协议的请求被缓存
        // 不允许 chrome-extension://, chrome://, data:, blob: 等
        return protocol === 'http:' || protocol === 'https:';
    } catch (e) {
        // URL 解析失败，不允许缓存
        return false;
    }
}

// 安装Service Worker
self.addEventListener('install', (event) => {
    console.log('Service Worker: 安装中...');
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('Service Worker: 缓存静态资源');
                return cache.addAll(STATIC_CACHE_URLS);
            })
            .then(() => {
                console.log('Service Worker: 安装完成');
                return self.skipWaiting(); // 立即激活新的Service Worker
            })
            .catch((error) => {
                console.error('Service Worker: 安装失败', error);
            })
    );
});

// 激活Service Worker
self.addEventListener('activate', (event) => {
    console.log('Service Worker: 激活中...');
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('Service Worker: 删除旧缓存', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        })
        .then(() => {
            console.log('Service Worker: 激活完成');
            return self.clients.claim(); // 立即控制所有客户端
        })
    );
});

// 拦截网络请求
self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);
    
    // CHANGE: 跨域 API（如 onrender.com）不拦截，让浏览器原生处理，避免 SW 导致 CORS 预检异常
    const isCrossOriginApi = url.hostname && (url.hostname.includes('onrender.com') || url.hostname.includes('render.com'));
    if (isCrossOriginApi) {
        return;  // 不调用 respondWith，浏览器直接处理
    }
    // 同源 API 直连网络
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/pwa_cart/api/')) {
        event.respondWith(fetch(request));
        return;
    }
    
    // 以下为原「API 缓存/离线」逻辑，已改为上面直连，保留注释供恢复
    // API请求：网络优先，失败时返回缓存
    // ⚠️ 重要：POST/PUT/DELETE等写操作请求不应该被缓存
    if (false && url.pathname.startsWith('/api/')) {
        // 对于POST/PUT/DELETE等写操作，直接通过网络请求，不缓存
        if (request.method !== 'GET') {
            event.respondWith(
                fetch(request)
                    .catch(() => {
                        // 网络失败，返回错误响应
                        return new Response(
                            JSON.stringify({ error: 'Modo offline, por favor verifique su conexión de red' }),
                            {
                                headers: { 'Content-Type': 'application/json' },
                                status: 503
                            }
                        );
                    })
            );
            return;
        }
        
        // 只有GET请求才缓存
        event.respondWith(
            fetch(request)
                .then((response) => {
                    // CHANGE: 只缓存成功的GET响应，且URL协议支持缓存
                    if (response.status === 200 && request.method === 'GET' && canCacheUrl(request.url)) {
                        const responseClone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(request, responseClone).catch((err) => {
                                // 忽略缓存错误（某些请求可能无法缓存）
                                console.warn('Service Worker: 缓存失败（可忽略）', err);
                            });
                        });
                    }
                    return response;
                })
                .catch(() => {
                    // 网络失败，尝试从缓存获取（仅GET请求）
                    if (request.method === 'GET') {
                        return caches.match(request).then((cachedResponse) => {
                            if (cachedResponse) {
                                return cachedResponse;
                            }
                        });
                    }
                    // 如果缓存也没有，返回离线响应
                    return new Response(
                        JSON.stringify({ error: 'Modo offline, por favor verifique su conexión de red' }),
                        {
                            headers: { 'Content-Type': 'application/json' },
                            status: 503
                        }
                    );
                })
        );
        return;
    }
    
    // CHANGE: 产品图 network-first，避免 Android 缓存 404
    const path = url.pathname || '';
    const isProductImage = path.indexOf('Ya%20Subio') !== -1 || path.indexOf('Ya Subio') !== -1 ||
        /\.(jpg|jpeg|png|gif|webp)(\?|$)/i.test(path);
    if (request.method !== 'GET') {
        event.respondWith(fetch(request));
        return;
    }
    
    if (isProductImage) {
        event.respondWith(
            fetch(request)
                .then((response) => {
                    if (response.status === 200 && canCacheUrl(request.url)) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((c) => c.put(request, clone).catch(() => {}));
                    }
                    return response;
                })
                .catch(() => caches.match(request))
        );
        return;
    }
    
    event.respondWith(
        caches.match(request)
            .then((cachedResponse) => {
                if (cachedResponse) return cachedResponse;
                return fetch(request).then((response) => {
                    // CHANGE: 只缓存成功的GET响应，且URL协议支持缓存
                    if (response.status === 200 && request.method === 'GET' && canCacheUrl(request.url)) {
                        const responseClone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(request, responseClone).catch((err) => {
                                // 忽略缓存错误（某些请求可能无法缓存）
                                console.warn('Service Worker: 缓存失败（可忽略）', err);
                            });
                        });
                    }
                    return response;
                });
            })
    );
});

// 后台同步（如果浏览器支持）
self.addEventListener('sync', (event) => {
    if (event.tag === 'sync-cart') {
        event.waitUntil(
            // 这里可以添加后台同步购物车的逻辑
            console.log('Service Worker: Sincronizando carrito en segundo plano')
        );
    }
});

// 推送通知（如果浏览器支持）
self.addEventListener('push', (event) => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'VentaX Carrito';
    const options = {
        body: data.body || 'Tienes un nuevo mensaje',
        icon: './icon-192.svg',
        badge: './icon-192.svg',
        tag: data.tag || 'default',
        data: data.url || './'
    };
    
    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// 通知点击处理
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    
    event.waitUntil(
        clients.openWindow(event.notification.data || './')
    );
});

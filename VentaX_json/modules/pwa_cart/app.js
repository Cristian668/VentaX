// ===== Script principal de la aplicación PWA del carrito =====
// BUILD_TAG: host-fix-20260324-v28

// CHANGE: 生产环境静默 console.log/info/debug，减少主线程开销
(function(){
    try {
        var h = (typeof location !== 'undefined' && location.hostname) || '';
        if (h !== '127.0.0.1' && h !== 'localhost') {
            var noop = function(){};
            console.log = console.info = console.debug = noop;
        }
    } catch (e) {}
})();

// CHANGE: 全局兜底，防止第三方/注入脚本异常导致页面完全不可用
(function installGlobalRuntimeGuard() {
    if (typeof window === 'undefined') return;
    if (window.__pwaRuntimeGuardInstalled) return;
    window.__pwaRuntimeGuardInstalled = true;

    function showFatalHint(msg) {
        try {
            var grid = document.getElementById('productsGrid');
            if (!grid) return;
            if (grid.children && grid.children.length > 0 && !/Cargando/i.test(grid.textContent || '')) return;
            grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;text-align:center;padding:2rem;color:var(--text-light);">' +
                '<h3 style="margin-bottom:.5rem;">Se detectó un error de script del navegador</h3>' +
                '<p style="margin:0 0 .5rem 0;">Recargue la página. Si persiste, limpie caché/SW y vuelva a abrir.</p>' +
                '<small style="opacity:.8;">' + String(msg || '').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</small>' +
                '</div>';
        } catch (e) {}
    }

    function _isExtensionNoise(msg) {
        var s = String(msg || '').toLowerCase();
        return s.indexOf('receiving end does not exist') !== -1 ||
               s.indexOf('could not establish connection') !== -1 ||
               s.indexOf('message port closed') !== -1 ||
               s.indexOf('chrome-extension://') !== -1 ||
               s.indexOf('moz-extension://') !== -1;
    }

    window.addEventListener('error', function(evt) {
        var m = String((evt && evt.message) || '');
        if (!m) return;
        // CHANGE: 浏览器扩展 postMessage 噪音不再触发页面级报错提示
        if (_isExtensionNoise(m)) return;
        if (m.indexOf('not iterable') !== -1 || m.indexOf('_0x') !== -1) {
            showFatalHint(m);
        }
    });

    window.addEventListener('unhandledrejection', function(evt) {
        var reason = evt && evt.reason;
        var m = reason && (reason.message || String(reason));
        if (!m) return;
        // CHANGE: 忽略扩展注入脚本 rejection（不影响业务）
        if (_isExtensionNoise(m)) return;
        if (String(m).indexOf('not iterable') !== -1 || String(m).indexOf('_0x') !== -1) {
            showFatalHint(m);
        }
    });

    // CHANGE: 某些浏览器扩展会向页面广播异常 message 对象；此处仅做防御性读取，避免触发二次脚本错误
    window.addEventListener('message', function(evt) {
        try {
            var d = evt && evt.data;
            if (!d) return;
            var t = (typeof d === 'object' && d.type) ? String(d.type).toLowerCase() : '';
            if (!t) return;
            if (t.indexOf('webpack') !== -1 || t.indexOf('extension') !== -1 || t.indexOf('devtools') !== -1) {
                return;
            }
        } catch (_) {
            // ignore all cross-context message parse issues
        }
    }, true);
})();

// 配置（API 基址）
// CHANGE: 云端部署用 config.js 的 api_base_url（如 Render）；本地打开页面时优先用本机 5000，无需改 config
function _getApiBase() {
    if (typeof window === 'undefined' || !window.location || !window.location.origin) return 'http://127.0.0.1:5000/api';
    var origin = window.location.origin;
    var path = (window.location.pathname || '');
    var host = (typeof window !== 'undefined' && window.location && window.location.hostname) ? String(window.location.hostname).toLowerCase() : '';
    var isVentaxPagesHost = (host === 'ventax.pages.dev' || host.indexOf('.ventax.pages.dev') !== -1 || host === 'ventaxpages.com');
    // 本机打开（127.0.0.1 / localhost）时一律用本地 API，方便本地调试
    if (origin.indexOf('127.0.0.1') !== -1 || origin.indexOf('localhost') !== -1) return 'http://127.0.0.1:5000/api';
    // Pages（含分支预览域名）一律走同源 /api，避免直连 Render 触发 CORS
    if (isVentaxPagesHost) return origin + '/api';
    // 已配置云端 API 时使用（部署到 Render 后云端页面用此地址，无需再开本机 .bat）
    if (typeof window !== 'undefined' && window.PWA_CONFIG && window.PWA_CONFIG.api_base_url) {
        var url = String(window.PWA_CONFIG.api_base_url).replace(/\/$/, '');
        if (url) return url;
    }
    // CHANGE: Pages 站点默认走同源 /api（Cloudflare Functions 代理），避免浏览器直连 Render 触发 CORS
    if (isVentaxPagesHost) {
        return origin + '/api';
    }
    if (path.indexOf('/pwa_cart') !== -1) return origin + '/pwa_cart/api';
    return origin + '/api';
}
// CHANGE: 未登录用 0，禁止用固定 1 导致所有未登录用户共享同一购物车
const CONFIG = {
    get API_BASE_URL() { return _getApiBase(); },
    DEFAULT_USER_ID: 0,
    SHIPPING_COST: 8.00
};

// 搜索栏下方固定展示的分类标签（可再在 config.js 的 PWA_CONFIG.catalog_pinned_categories 覆盖）
var DEFAULT_CATALOG_PINNED = [
    { name: 'JUGUETES', bg: '#E91E63', color: '#fff' },
    { name: 'FIESTA', bg: '#FF9800', color: '#fff' },
    { name: 'HOGAR', bg: '#43A047', color: '#fff' },
    { name: 'UTILES ESCOLAR', bg: '#1E88E5', color: '#fff' },
    { name: 'FERRETERIA', bg: '#546E7A', color: '#fff' },
    { name: 'TEMPORADA', bg: '#8E24AA', color: '#fff' },
    { name: 'KAWAII', bg: '#EC407A', color: '#fff' },
    { name: 'DEPORTE', bg: '#00ACC1', color: '#fff' }
];
var CATALOG_EXTRA_TAG_COLORS = [
    { bg: '#5C6BC0', color: '#fff' },
    { bg: '#D84315', color: '#fff' },
    { bg: '#00897B', color: '#fff' },
    { bg: '#F4511E', color: '#fff' },
    { bg: '#6D4C41', color: '#fff' },
    { bg: '#3949AB', color: '#fff' }
];

function _normCatalogName(s) {
    var v = String(s || '').trim().toLowerCase();
    // CHANGE: 统一去掉分类外围装饰符，兼容「【FIESTA】」「[FIESTA]」「(FIESTA)」等历史数据
    v = v
        .replace(/^【+|】+$/g, '')
        .replace(/^\[+|\]+$/g, '')
        .replace(/^\(+|\)+$/g, '')
        .replace(/^（+|）+$/g, '')
        .trim();
    return v;
}

function _isInvalidPinnedCategoryName(name) {
    var n = String(name || '').trim();
    if (!n) return true;
    var lower = n.toLowerCase();

    // 文件名/图片名误入分类（例如 xxx.jpg、xxx.JPG (1)、whatsapp image ... .jpg）
    if (/\.(jpg|jpeg|png|webp|gif|bmp|avif|svg)\b/i.test(lower)) return true;
    if (/(^|[_-])(jpg|jpeg|png|webp|gif|bmp|avif|svg)($|[_-])/i.test(lower)) return true;

    // 明显是路径或产图标识，不应作为分类
    if (lower.indexOf('/') !== -1 || lower.indexOf('\\') !== -1) return true;
    if (lower.indexOf('no_white_no_hay_precio') !== -1) return true;
    if (lower.indexOf('ya subio') !== -1) return true;

    // 带分隔符的复合分类（如 "FIESTA, HOGAR" 或 "A/B"）不应作为单一标签
    if (/[;,|/]/.test(lower)) return true;

    // 像 importadora_xxx_12345_no_white 这类文件名模式
    if (/\d{3,}[_-]/.test(lower) && lower.indexOf('_') !== -1) return true;

    // 常见占位/脏分类（如 IMP158）
    if (/^imp\s*\d{2,}$/i.test(n)) return true;

    return false;
}

function _escAttr(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

function _getPinnedCatalogDefs() {
    var cfg = (typeof window !== 'undefined' && window.PWA_CONFIG) ? window.PWA_CONFIG : {};
    var raw = [];
    var LS_KEY = 'pwa_catalog_pinned_categories_v1';
    var LS_CLEAN_FLAG_KEY = 'pwa_catalog_pinned_categories_cleaned_v1';

    // CHANGE: 优先读取 localStorage（运营后台图2写入），确保首页图1颜色/顺序实时生效
    // key: pwa_catalog_pinned_categories_v1
    // CHANGE: 一次性清理 localStorage 中历史脏分类（例如 *.JPG、路径名等），避免旧脏数据反复出现
    if (typeof window !== 'undefined' && window.localStorage) {
        try {
            var localRaw = window.localStorage.getItem(LS_KEY);
            var parsed = JSON.parse(localRaw || '[]');
            if (Array.isArray(parsed) && parsed.length) {
                var sanitized = [];
                for (var i = 0; i < parsed.length; i++) {
                    var item = parsed[i];
                    var name = (item && typeof item === 'object' && item.name) ? item.name : item;
                    if (_isInvalidPinnedCategoryName(name)) continue;
                    if (item && typeof item === 'object' && item.name) {
                        sanitized.push({
                            name: String(item.name),
                            bg: item.bg,
                            color: item.color
                        });
                    } else {
                        sanitized.push(String(item));
                    }
                }

                raw = sanitized;

                var cleanedAlready = window.localStorage.getItem(LS_CLEAN_FLAG_KEY) === '1';
                var changed = (sanitized.length !== parsed.length) || (JSON.stringify(parsed) !== JSON.stringify(sanitized));
                if (changed || !cleanedAlready) {
                    if (sanitized.length) {
                        window.localStorage.setItem(LS_KEY, JSON.stringify(sanitized));
                    } else {
                        window.localStorage.removeItem(LS_KEY);
                    }
                    window.localStorage.setItem(LS_CLEAN_FLAG_KEY, '1');
                }
            }
        } catch (e) {
            // ignore localStorage parse errors
        }
    }

    // localStorage 为空时再回退到 config.js
    if ((!Array.isArray(raw) || !raw.length) && Array.isArray(cfg.catalog_pinned_categories)) {
        raw = cfg.catalog_pinned_categories;
    }

    if (Array.isArray(raw) && raw.length) {
        var mapped = raw.map(function(item, i) {
            if (item && typeof item === 'object' && item.name) {
                return {
                    name: String(item.name),
                    bg: item.bg || (DEFAULT_CATALOG_PINNED[i % DEFAULT_CATALOG_PINNED.length] || {}).bg || '#607D8B',
                    color: item.color || '#fff'
                };
            }
            var fallback = DEFAULT_CATALOG_PINNED[i % DEFAULT_CATALOG_PINNED.length] || { bg: '#607D8B', color: '#fff' };
            return { name: String(item), bg: fallback.bg || '#607D8B', color: fallback.color || '#fff' };
        }).filter(function(x) {
            return !_isInvalidPinnedCategoryName(x && x.name);
        });

        if (mapped.length) return mapped;

        // 全部无效时清除脏 localStorage，回退默认分类
        if (typeof window !== 'undefined' && window.localStorage) {
            try { window.localStorage.removeItem('pwa_catalog_pinned_categories_v1'); } catch (e) {}
        }
    }
    return DEFAULT_CATALOG_PINNED.slice();
}

// CHANGE: 监听后台管理页写入 localStorage，首页分类标签实时联动（无需手动刷新）
function handlePinnedCategoriesStorageChange(evt) {
    try {
        if (!evt || evt.key !== 'pwa_catalog_pinned_categories_v1') return;
        renderCatalogCategoryTags();
        if (AppState && AppState._catalogMode && AppState.catalogCategoryName) {
            var exists = _buildCatalogTagRows().some(function(r) {
                return _normCatalogName(r.filter) === _normCatalogName(AppState.catalogCategoryName);
            });
            if (!exists) {
                AppState._catalogMode = false;
                AppState.catalogCategoryName = '';
                if (typeof fetchProducts === 'function') fetchProducts('Cristy');
            }
        }
    } catch (e) {
        // ignore storage event errors
    }
}

function _categoryCountMap() {
    var map = {};
    var list = Array.isArray(AppState.categories) ? AppState.categories : [];
    list.forEach(function(c) {
        if (!c || typeof c !== 'object') return;
        var n = String(c.name || '').trim();
        if (!n) return;
        map[_normCatalogName(n)] = Number(c.count || 0);
    });
    return map;
}

function _buildCatalogTagRows() {
    var counts = _categoryCountMap();
    var apiByNorm = {};
    var list = Array.isArray(AppState.categories) ? AppState.categories : [];
    list.forEach(function(c) {
        if (!c || typeof c !== 'object') return;
        var n = String(c.name || '').trim();
        if (!n) return;
        var k = _normCatalogName(n);
        if (!apiByNorm[k]) apiByNorm[k] = n;
    });
    var pinned = _getPinnedCatalogDefs();
    var pinnedNorms = {};
    var rows = [];
    pinned.forEach(function(p) {
        var pn = _normCatalogName(p.name);
        pinnedNorms[pn] = true;
        var filterName = apiByNorm[pn] || String(p.name).trim();
        rows.push({
            label: String(p.name).trim(),
            filter: filterName,
            bg: p.bg,
            color: p.color,
            count: counts[_normCatalogName(filterName)] != null ? counts[_normCatalogName(filterName)] : 0
        });
    });
    var extras = list.filter(function(c) {
        var n = String((c && c.name) || '').trim();
        // CHANGE: 过滤 API 返回的脏分类（如 whatsapp image...jpg），避免出现在首页彩色标签
        return n && !_isInvalidPinnedCategoryName(n) && !pinnedNorms[_normCatalogName(n)];
    }).slice().sort(function(a, b) {
        var an = String((a && a.name) || '');
        var bn = String((b && b.name) || '');
        if (an === 'Cristy') return -1;
        if (bn === 'Cristy') return 1;
        if (an === 'Otros') return 1;
        if (bn === 'Otros') return -1;
        return Number((b && b.count) || 0) - Number((a && a.count) || 0);
    });
    extras.forEach(function(c, i) {
        var n = String(c.name || '').trim();
        var col = CATALOG_EXTRA_TAG_COLORS[i % CATALOG_EXTRA_TAG_COLORS.length];
        rows.push({
            label: n,
            filter: n,
            bg: col.bg,
            color: col.color,
            count: Number(c.count || 0)
        });
    });
    return rows;
}

function renderCatalogCategoryTags() {
    var wrap = document.getElementById('catalogCategoryTags');
    if (!wrap) return;
    var searchEl = document.getElementById('searchInput');
    var searchVal = (searchEl && searchEl.value) ? String(searchEl.value).trim() : '';
    var searching = searchVal.length > 0;
    var activeFilter = AppState.catalogCategoryName || '';
    var parts = [];
    var allActive = !searching && !AppState._catalogMode && !activeFilter;
    parts.push('<button type="button" class="catalog-category-tag catalog-category-tag--all' + (allActive ? ' active' : '') + '" data-category="">Todos</button>');
    var rows = _buildCatalogTagRows();
    if (!rows.length && (!AppState.categories || !AppState.categories.length)) {
        parts.push('<span class="catalog-category-tag catalog-category-tag--muted">Cargando categorías…</span>');
        wrap.innerHTML = parts.join('');
        return;
    }
    rows.forEach(function(r) {
        var isActive = !searching && AppState._catalogMode && activeFilter && _normCatalogName(activeFilter) === _normCatalogName(r.filter);
        var cls = 'catalog-category-tag' + (isActive ? ' active' : '');
        var st = 'background:' + r.bg + ';color:' + r.color + ';';
        var countHtml = typeof r.count === 'number' && r.count > 0 ? ' <span style="font-weight:600;opacity:.9">(' + r.count + ')</span>' : '';
        parts.push('<button type="button" class="' + cls + '" style="' + st.replace(/"/g, '&quot;') + '" data-category="' + _escAttr(r.filter) + '">' + String(r.label).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + countHtml + '</button>');
    });
    wrap.innerHTML = parts.join('');
}

async function fetchAllProductsByCategory(categoryName, loadTimeoutMs, supplier) {
    const PAGE_LIMIT = 500;
    const MAX_OFFSET = 50000;
    let offset = 0;
    let total = null;
    let merged = [];
    var uiPage = getUiPageSize();

    while (true) {
        let url = '/products?limit=' + PAGE_LIMIT + '&offset=' + offset + '&category=' + encodeURIComponent(categoryName);
        if (supplier) url += '&supplier=' + encodeURIComponent(supplier);
        url += '&_=' + (Date.now ? Date.now() : 0);

        const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => reject(new Error('Tiempo de espera agotado. Compruebe la conexión o intente más tarde.')), loadTimeoutMs);
        });

        const result = await Promise.race([apiRequest(url), timeoutPromise]);
        const ok = result && (result.success === true || Array.isArray(result.data));
        if (!ok) return result;

        const pageRows = Array.isArray(result.data) ? result.data : [];
        merged = merged.concat(pageRows);

        var totalRaw = (result.total != null && result.total !== '') ? result.total : (result.pagination && result.pagination.total);
        const totalNum = Number(totalRaw);
        if (Number.isFinite(totalNum) && totalNum >= 0) total = totalNum;

        if (pageRows.length < PAGE_LIMIT) break;
        if (total != null && merged.length >= total) break;

        offset += PAGE_LIMIT;
        if (offset > MAX_OFFSET) break;
    }

    return { success: true, data: merged, total: total != null ? total : merged.length };
}

async function fetchAllProductsBySearch(searchText, loadTimeoutMs) {
    const PAGE_LIMIT = 500;
    const MAX_OFFSET = 50000;
    let offset = 0;
    let total = null;
    let merged = [];

    while (true) {
        let url = '/products?limit=' + PAGE_LIMIT + '&offset=' + offset + '&search=' + encodeURIComponent(searchText);
        url += '&_=' + (Date.now ? Date.now() : 0);

        const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => reject(new Error('Tiempo de espera agotado. Compruebe la conexión o intente más tarde.')), loadTimeoutMs);
        });

        const result = await Promise.race([apiRequest(url), timeoutPromise]);
        const ok = result && (result.success === true || Array.isArray(result.data));
        if (!ok) return result;

        const pageRows = Array.isArray(result.data) ? result.data : [];
        merged = merged.concat(pageRows);

        var totalRaw = (result.total != null && result.total !== '') ? result.total : (result.pagination && result.pagination.total);
        const totalNum = Number(totalRaw);
        if (Number.isFinite(totalNum) && totalNum >= 0) total = totalNum;

        if (pageRows.length < PAGE_LIMIT) break;
        if (total != null && merged.length >= total) break;

        offset += PAGE_LIMIT;
        if (offset > MAX_OFFSET) break;
    }

    return { success: true, data: merged, total: total != null ? total : merged.length };
}

async function fetchAllProductsUnfiltered(loadTimeoutMs, supplier) {
    const PAGE_LIMIT = 500;
    const MAX_OFFSET = 50000;
    let offset = 0;
    let total = null;
    let merged = [];

    while (true) {
        let url = '/products?limit=' + PAGE_LIMIT + '&offset=' + offset;
        if (supplier) url += '&supplier=' + encodeURIComponent(supplier);
        url += '&_=' + (Date.now ? Date.now() : 0);

        const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => reject(new Error('Tiempo de espera agotado. Compruebe la conexión o intente más tarde.')), loadTimeoutMs);
        });

        const result = await Promise.race([apiRequest(url), timeoutPromise]);
        const ok = result && (result.success === true || Array.isArray(result.data));
        if (!ok) return result;

        const pageRows = Array.isArray(result.data) ? result.data : [];
        merged = merged.concat(pageRows);

        var totalRaw = (result.total != null && result.total !== '') ? result.total : (result.pagination && result.pagination.total);
        const totalNum = Number(totalRaw);
        if (Number.isFinite(totalNum) && totalNum >= 0) total = totalNum;

        if (pageRows.length < PAGE_LIMIT) break;
        if (total != null && merged.length >= total) break;

        offset += PAGE_LIMIT;
        if (offset > MAX_OFFSET) break;
    }

    return { success: true, data: merged, total: total != null ? total : merged.length };
}

function _splitCatalogCategoryTokens(rawCategory) {
    return String(rawCategory || '')
        .split(/[;,|\/，、]+/)
        .map(function(x) { return _normCatalogName(x); })
        .filter(Boolean);
}

function _productMatchesCatalogCategory(product, categoryName) {
    var target = _normCatalogName(categoryName);
    if (!target) return true;
    if (!product || typeof product !== 'object') return false;

    var tokens = _splitCatalogCategoryTokens(product.category || '');
    if (tokens.indexOf(target) !== -1) return true;

    // 后备：某些历史数据 category 会有不规则分隔，做一次包含匹配
    var raw = _normCatalogName(product.category || '');
    return !!raw && raw.indexOf(target) !== -1;
}

function _forceFilterRowsByCatalogCategory(rows, categoryName) {
    if (!Array.isArray(rows) || !rows.length) return [];
    return rows.filter(function(p) {
        return _productMatchesCatalogCategory(p, categoryName);
    });
}

async function loadProductsByCatalogCategory(filterName) {
    var raw = (filterName != null) ? String(filterName).trim() : '';
    if (!raw) {
        AppState._catalogMode = false;
        AppState.catalogCategoryName = '';
        // CHANGE: 退出分类筛选时恢复分页拉取状态
        AppState._productsHasMore = true;
        AppState._productsNextOffset = Number(AppState.products.length || 0);
        renderCatalogCategoryTags();
        if (AppState.currentView === 'products') return fetchProducts('others');
        return fetchProducts('Cristy');
    }
    var grid = document.getElementById('productsGrid');
    if (grid) {
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;text-align:center;padding:3rem 1.5rem;color:var(--text-light);">Cargando productos…</div>';
    }
    AppState._catalogMode = true;
    AppState.catalogCategoryName = raw;
    renderCatalogCategoryTags();
    try {
        const LOAD_TIMEOUT_MS = 90000;
        // CHANGE: 分类页按双供应商并集拉取，避免只命中默认供应商导致 FIESTA 等分类缺商品
        var mergedByCategory = [];
        var seenByCategory = {};
        for (const sp of ['Cristy', 'others']) {
            var part = await fetchAllProductsByCategory(raw, LOAD_TIMEOUT_MS, sp);
            var partRows = (part && part.success && Array.isArray(part.data)) ? part.data : [];
            for (var i = 0; i < partRows.length; i++) {
                var pr = partRows[i];
                var k = _dedupeKey(pr);
                if (k && seenByCategory[k]) continue;
                if (k) seenByCategory[k] = true;
                mergedByCategory.push(pr);
            }
        }
        var rows = mergedByCategory;

        // 前端兜底：优先使用严格过滤；若后端已按 category 返回但记录缺少/污染 category 字段，避免被误过滤成 0
        var strictRows = _forceFilterRowsByCatalogCategory(rows, raw);
        var categoryRows = strictRows.length ? strictRows : rows;

        // CHANGE: 仅在「category API 本身为空」时才回退，避免出现“先有结果又被后续请求覆盖成空”
        if (!categoryRows.length) {
            // 1) 先用 search 回退
            var fallback = await fetchAllProductsBySearch(raw, LOAD_TIMEOUT_MS);
            rows = (fallback && fallback.success && Array.isArray(fallback.data)) ? fallback.data : [];
            strictRows = _forceFilterRowsByCatalogCategory(rows, raw);
            // search 结果若缺少 category 字段，严格过滤会变 0；此时直接用 search 结果兜底
            categoryRows = strictRows.length ? strictRows : rows;
            if (categoryRows.length) {
                console.log('🔍 [catalog] backend category empty, fallback+search:', raw, categoryRows.length);
            }

            // 2) search 仍为空：再做全量拉取并前端按 category 严格过滤（最后兜底，修复后端 category/search 都不稳定）
            if (!categoryRows.length) {
                var mergedAll = [];
                var fullCristy = await fetchAllProductsUnfiltered(LOAD_TIMEOUT_MS, 'Cristy');
                var fullOthers = await fetchAllProductsUnfiltered(LOAD_TIMEOUT_MS, 'others');
                if (fullCristy && fullCristy.success && Array.isArray(fullCristy.data)) mergedAll = mergedAll.concat(fullCristy.data);
                if (fullOthers && fullOthers.success && Array.isArray(fullOthers.data)) mergedAll = mergedAll.concat(fullOthers.data);
                categoryRows = _forceFilterRowsByCatalogCategory(mergedAll, raw);
                if (categoryRows.length) {
                    console.log('🔍 [catalog] fallback full-scan strict-match:', raw, categoryRows.length);
                }
            }
        }

        // 分类模式下保留无图商品（前端会显示占位图），避免计数与列表不一致
        // CHANGE: 分类页不能按 OCR 占位规则二次过滤，否则会把真实分类商品误删成只剩 3 个或 0 个
        AppState.products = dedupeProductsByCode(categoryRows, true);
        AppState.productsVisibleCount = getUiPageSize();
        AppState.currentPage = 1;
        AppState._lastProductsSupplier = null;
        AppState._productsLoading = false;
        // CHANGE: 分类模式下禁用全量分页追加，避免把其他供应商产品混入当前分类结果
        AppState._productsHasMore = false;
        AppState._productsNextOffset = AppState.products.length;
        AppState._productsPrefetchBuffer = null;
        AppState._productsPrefetchPromise = null;
        renderProducts();
        requestAnimationFrame(function() {
            requestAnimationFrame(function() { applyProductHashAnchor(); });
        });
    } catch (e) {
        console.error('Error al cargar por categoría:', e);
        if (grid) grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;text-align:center;padding:2rem;">Error al cargar la categoría</div>';
        showToast('Error al cargar la categoría', 'error');
    }
}

// CHANGE: 页面版本标识（用于快速判断线上/本地是否最新）
function getVersionMeta() {
    var cfg = (typeof window !== 'undefined' && window.PWA_CONFIG) ? window.PWA_CONFIG : {};
    var build = (cfg && (cfg.build_id || cfg.version || cfg.commit || cfg.release)) || '';
    var host = (typeof location !== 'undefined' && location.hostname) ? location.hostname.toLowerCase() : '';
    var isLocal = host === '127.0.0.1' || host === 'localhost';
    var source = isLocal ? 'LOCAL' : 'PROD';
    var stamp = build ? String(build) : 'dev';
    return {
        text: source + '-' + stamp,
        isLocal: isLocal
    };
}

function renderVersionBadge() {
    var el = document.getElementById('versionBadge');
    if (!el) return;
    var meta = getVersionMeta();
    el.textContent = meta.text;
    el.classList.remove('is-local', 'is-prod');
    el.classList.add(meta.isLocal ? 'is-local' : 'is-prod');
}
// CHANGE: 相对路径产品图用 API 所在域名，云端部署时图片从后端（如 Render）加载
function _getImageBase() {
    if (typeof window === 'undefined' || !window.location) return '';
    var api = CONFIG.API_BASE_URL;
    if (!api || api.indexOf('127.0.0.1') !== -1 || api.indexOf('localhost') !== -1) return window.location.origin;
    try { return new URL(api).origin; } catch (e) { return window.location.origin; }
}

// CHANGE: 图片调试开关
// 开启方式（任选其一）：
// 1) URL 加 ?debug_images=1
// 2) 控制台执行 localStorage.setItem('pwa_debug_images','1')
// 3) 控制台执行 window.PWA_DEBUG_IMAGES = true
// 失败专注模式（减少噪音）：
// - URL 加 ?debug_images_fail_only=1
// - 或 localStorage.setItem('pwa_debug_images_fail_only','1')
var IMAGE_DEBUG_STATS = {
    total: 0,
    failed: 0,
    rules: {},
    lastPrintAt: 0,
    sinceLastPrint: 0
};

function isImageDebugEnabled() {
    try {
        if (typeof window === 'undefined') return false;
        if (window.PWA_DEBUG_IMAGES === true) return true;
        var q = new URLSearchParams(window.location.search || '');
        if (q.get('debug_images') === '1') return true;
        var ls = localStorage.getItem('pwa_debug_images');
        return ls === '1' || ls === 'true';
    } catch (e) {
        return false;
    }
}

function isImageDebugFailOnly() {
    try {
        if (typeof window === 'undefined') return false;
        if (window.PWA_DEBUG_IMAGES_FAIL_ONLY === true) return true;
        var q = new URLSearchParams(window.location.search || '');
        if (q.get('debug_images_fail_only') === '1') return true;
        var ls = localStorage.getItem('pwa_debug_images_fail_only');
        return ls === '1' || ls === 'true';
    } catch (e) {
        return false;
    }
}

function isImageDebugFailureRule(rule, extra) {
    var r = String(rule || '').toLowerCase();
    if (r.indexOf('error-') === 0) return true;
    if (extra && (extra.failed === true || extra.isFailed === true)) return true;
    return false;
}

function collectImageDebugStat(rule, isFailed) {
    var key = String(rule || 'unknown');
    IMAGE_DEBUG_STATS.total += 1;
    IMAGE_DEBUG_STATS.sinceLastPrint += 1;
    if (isFailed) IMAGE_DEBUG_STATS.failed += 1;
    if (!IMAGE_DEBUG_STATS.rules[key]) {
        IMAGE_DEBUG_STATS.rules[key] = { hits: 0, failed: 0 };
    }
    IMAGE_DEBUG_STATS.rules[key].hits += 1;
    if (isFailed) IMAGE_DEBUG_STATS.rules[key].failed += 1;
}

function printImageDebugSummary(force) {
    if (!isImageDebugEnabled()) return;
    try {
        var now = Date.now();
        if (!force) {
            if (IMAGE_DEBUG_STATS.sinceLastPrint < 30 && (now - IMAGE_DEBUG_STATS.lastPrintAt) < 10000) return;
        }

        var rows = [];
        for (var k in IMAGE_DEBUG_STATS.rules) {
            if (!Object.prototype.hasOwnProperty.call(IMAGE_DEBUG_STATS.rules, k)) continue;
            var item = IMAGE_DEBUG_STATS.rules[k] || { hits: 0, failed: 0 };
            rows.push({ rule: k, hits: item.hits || 0, failed: item.failed || 0 });
        }
        rows.sort(function(a, b) {
            if (b.failed !== a.failed) return b.failed - a.failed;
            return b.hits - a.hits;
        });

        console.log('[IMG-DEBUG-SUMMARY] total=' + IMAGE_DEBUG_STATS.total + ' failed=' + IMAGE_DEBUG_STATS.failed);
        if (rows.length && console.table) console.table(rows);
        IMAGE_DEBUG_STATS.lastPrintAt = now;
        IMAGE_DEBUG_STATS.sinceLastPrint = 0;
    } catch (e) {
        // no-op
    }
}

function logImageDebug(rule, inputPath, outputPath, extra) {
    if (!isImageDebugEnabled()) return;
    try {
        var isFailed = isImageDebugFailureRule(rule, extra);
        collectImageDebugStat(rule, isFailed);

        var failOnly = isImageDebugFailOnly();
        if (failOnly && !isFailed) {
            printImageDebugSummary(false);
            return;
        }

        var payload = {
            rule: rule || 'unknown',
            failed: isFailed,
            input: inputPath || '',
            output: outputPath || ''
        };
        if (extra && typeof extra === 'object') {
            for (var k in extra) {
                if (Object.prototype.hasOwnProperty.call(extra, k)) payload[k] = extra[k];
            }
        }
        console.log('[IMG-DEBUG]', payload);
        printImageDebugSummary(false);
    } catch (e) {
        // no-op
    }
}

if (typeof window !== 'undefined') {
    window.printImageDebugSummary = function() { printImageDebugSummary(true); };
    window.addEventListener('storage', handlePinnedCategoriesStorageChange);
}
// CHANGE: 当 API 返回 /api/images/xxx 且页面在 Pages 上时，用当前站点 base 拼出 Pages 图片 URL（后端未设 PAGES_IMAGE_BASE_URL 时的前端回退）
// productOrSupplier: 可选，product 对象或 'Cristy' 字符串；Cristy 用 Ya Subio/Cristy/，其他用 Ya Subio/（PRODUCTOS 图在根目录）
function _resolveImageSrc(imagePath, productOrSupplier) {
    if (!imagePath || typeof imagePath !== 'string') {
        logImageDebug('empty-input', imagePath || '', '', { productHint: (productOrSupplier && productOrSupplier.codigo_proveedor) || productOrSupplier || '' });
        return '';
    }
    var raw = imagePath.trim();

    function _getPagesBase() {
        var host0 = (window.location.hostname || '').toLowerCase();
        // CHANGE: 预览域名是 <hash>.ventax.pages.dev，也应识别为 Pages 站点并补 /pwa_cart
        var isPages0 = host0.indexOf('ventaxpages.com') !== -1 || /(^|\.)ventax\.pages\.dev$/.test(host0);
        var pathname0 = (window.location.pathname || '').replace(/\/$/, '');
        var basePath0 = '/';
        if (isPages0) basePath0 = '/pwa_cart';
        else if (pathname0.indexOf('/pwa_v') !== -1) basePath0 = '/pwa_v';
        else if (pathname0.indexOf('/pwa_cart') !== -1) basePath0 = '/pwa_cart';
        return window.location.origin + basePath0;
    }

    function _isLocalHostRuntime() {
        var h = (window.location.hostname || '').toLowerCase();
        return h === '127.0.0.1' || h === 'localhost';
    }

    function _encodeRelPath(relPath) {
        var rel = String(relPath || '').replace(/^\/+/, '');
        // CHANGE: 云端图片常见历史后缀 *_no_white_no_hay_precio.* 在 Pages/R2 上多数不存在，
        // 统一优先回退到 *_no_white.*，减少首轮 404 噪音
        rel = rel.replace(/_no_white_no_hay_precio\./ig, '_no_white.');
        return rel.split('/').filter(Boolean).map(function(part) {
            try { return encodeURIComponent(decodeURIComponent(part)); } catch (e) { return encodeURIComponent(part); }
        }).join('/');
    }

    function _joinPagesPath(relPath) {
        var encoded = _encodeRelPath(relPath);
        if (_isLocalHostRuntime()) {
            var stripped = encoded.replace(/^Ya%20Subio\//i, '').replace(/^Ya\s*Subio\//i, '');
            try { stripped = decodeURIComponent(stripped); } catch (e) {}
            return '/api/images/' + stripped;
        }
        var base = _getPagesBase();
        return base + (base.slice(-1) === '/' ? '' : '/') + encoded;
    }

    function _joinR2Path(relPath) {
        var cfg = (typeof window !== 'undefined' && window.PWA_CONFIG) ? window.PWA_CONFIG : {};
        var r2Base = String((cfg && cfg.r2_image_base_url) || '').trim();
        if (!r2Base) return '';
        r2Base = r2Base.replace(/\/+$/, '');
        var encoded = _encodeRelPath(relPath);
        if (!encoded) return '';

        // CHANGE: R2 key 真实结构：
        // - PRODUCTOS 在根目录：WONI_IMPORT_AND_EXPORT_xxx.jpg
        // - Cristy 在 Cristy/ 子目录：Cristy/886983._AI.jpg
        // relPath 形如 "Ya Subio/xxx" 或 "Ya Subio/Cristy/xxx"
        var relDec = String(relPath || '').replace(/^\/+/, '');
        var relLower = relDec.toLowerCase();
        if (relLower.indexOf('ya subio/cristy/') === 0) {
            return r2Base + '/' + _encodeRelPath(relDec.replace(/^ya\s*subio\//i, ''));
        }
        if (relLower.indexOf('ya subio/') === 0) {
            return r2Base + '/' + _encodeRelPath(relDec.replace(/^ya\s*subio\//i, ''));
        }
        return r2Base + '/' + encoded;
    }

    function _preferR2(r2Url, pagesUrl) {
        if (!_isLocalHostRuntime() && r2Url) return r2Url;
        return pagesUrl || r2Url || '';
    }

    function _extractYaSubioRelative(inputPath) {
        var normalized = String(inputPath || '').replace(/\\/g, '/');
        var lower = normalized.toLowerCase();

        // 已经包含 Ya Subio 路径
        var markerYa = '/ya subio/';
        var idxYa = lower.indexOf(markerYa);
        if (idxYa !== -1) {
            var relYa = normalized.slice(idxYa + 1);
            // 统一纠正 cristy 子目录大小写（Pages 上 Linux 大小写敏感）
            relYa = relYa.replace(/^ya\s*subio\//i, 'Ya Subio/').replace(/^Ya\s*Subio\/cristy\//i, 'Ya Subio/Cristy/').replace(/\/cristy\//gi, '/Cristy/');
            return relYa;
        }

        // output_images 子目录映射到 Ya Subio 子目录
        var markerOut = '/output_images/';
        var idxOut = lower.indexOf(markerOut);
        if (idxOut !== -1) {
            var relOut = normalized.slice(idxOut + markerOut.length).replace(/^\/+/, '');
            relOut = relOut.replace(/^cristy\//i, 'Cristy/').replace(/^Cristy\//i, 'Cristy/').replace(/\/cristy\//gi, '/Cristy/');
            return 'Ya Subio/' + relOut;
        }

        // Cristy 旧路径映射
        var markerCristy = '/cristy/procesado/';
        var idxCristy = lower.indexOf(markerCristy);
        if (idxCristy !== -1) {
            var relCristy = normalized.slice(idxCristy + markerCristy.length).replace(/^\/+/, '');
            return 'Ya Subio/Cristy/' + relCristy;
        }

        return '';
    }

    // 绝对本地路径（Windows/Unix）：优先保留子目录结构
    var isAbsoluteLocalPath = /^[a-zA-Z]:\\/.test(raw) || raw.indexOf('\\') !== -1 || raw.startsWith('D:/') || raw.startsWith('/');
    if (isAbsoluteLocalPath && !/^https?:\/\//i.test(raw)) {
        var rel = _extractYaSubioRelative(raw);
        if (rel) {
            var outAbsPages = _joinPagesPath(rel);
            var outAbsR2 = _joinR2Path(rel);
            var outAbs = _preferR2(outAbsR2, outAbsPages);
            logImageDebug('absolute-local->ya-subio', raw, outAbs, { rel: rel, prefer: outAbsR2 ? 'r2' : 'pages' });
            return outAbs;
        }
    }

    if (raw.startsWith('http://') || raw.startsWith('https://')) {
        try {
            var u = new URL(raw);
            var pathDec = decodeURIComponent(u.pathname || '');
            // 已是 Ya Subio 静态路径：本地调试强制改走 /api/images/，避免继续请求旧静态目录
            if (pathDec.toLowerCase().indexOf('/ya subio/') !== -1) {
                if (_isLocalHostRuntime()) {
                    var tailLocal = decodeURIComponent((u.pathname || '').replace(/^.*\/ya%20?subio\//i, '').replace(/^.*\/ya\s*subio\//i, '').replace(/^\/+/, ''));
                    var fixedLocal = '/api/images/' + _encodeRelPath(tailLocal || pathDec.split('/').pop() || '');
                    logImageDebug('absolute-ya-subio-local-to-api-images', raw, fixedLocal, { origin: u.origin });
                    return fixedLocal;
                }
                if (u.origin.indexOf('ventax.pages.dev') !== -1 && pathDec.indexOf('/pwa_cart') === -1) {
                    var fixedPages = u.origin + '/pwa_cart' + (u.pathname || '');
                    logImageDebug('absolute-ya-subio-add-pwa_cart', raw, fixedPages, { origin: u.origin });
                    return fixedPages;
                }
                logImageDebug('absolute-ya-subio-keep', raw, raw, { origin: u.origin });
                return raw;
            }
            // CHANGE: 若后端返回绝对 URL 且路径是 /api/images/xxx，前端改走 Pages 静态目录，避免 429/503
            if ((u.pathname || '').indexOf('/api/images/') !== -1) {
                var tailAbs = decodeURIComponent((u.pathname || '').replace(/^.*\/api\/images\//, '').replace(/^\/+/, ''));
                if (tailAbs) {
                    if (tailAbs.indexOf('/') !== -1) {
                        var relAbsNested = 'Ya Subio/' + tailAbs;
                        var outAbsNestedPages = _joinPagesPath(relAbsNested);
                        var outAbsNestedR2 = _joinR2Path(relAbsNested);
                        var outAbsNested = _preferR2(outAbsNestedR2, outAbsNestedPages);
                        logImageDebug('absolute-api-images->ya-subio-nested', raw, outAbsNested, { tail: tailAbs, prefer: outAbsNestedR2 ? 'r2' : 'pages' });
                        return outAbsNested;
                    }
                    var hintAbs = '';
                    if (typeof productOrSupplier === 'string') hintAbs = productOrSupplier;
                    else if (productOrSupplier && typeof productOrSupplier === 'object') hintAbs = String(productOrSupplier.codigo_proveedor || '').trim();
                    var subAbs = (String(hintAbs || '').toLowerCase() === 'cristy') ? 'Cristy/' : '';
                    var relAbsFlat = 'Ya Subio/' + subAbs + tailAbs;
                    var outAbsFlatPages = _joinPagesPath(relAbsFlat);
                    var outAbsFlatR2 = _joinR2Path(relAbsFlat);
                    var outAbsFlat = _preferR2(outAbsFlatR2, outAbsFlatPages);
                    logImageDebug('absolute-api-images->ya-subio-flat', raw, outAbsFlat, { tail: tailAbs, supplierHint: hintAbs || '', prefer: outAbsFlatR2 ? 'r2' : 'pages' });
                    return outAbsFlat;
                }
            }
        } catch (e) { /* ignore */ }
        logImageDebug('absolute-url-keep', raw, raw, {});
        return raw;
    }

    // /api/images/xxx：若 xxx 含子路径则保留；否则按供应商映射
    if (raw.startsWith('/api/images/')) {
        var tail = raw.replace('/api/images/', '').split('?')[0].trim();
        if (!tail) {
            var outNoTail = _getImageBase() + (raw.startsWith('/') ? raw : '/' + raw);
            logImageDebug('api-images-empty-tail-fallback', raw, outNoTail, {});
            return outNoTail;
        }
        var decodedTail = tail;
        try { decodedTail = decodeURIComponent(tail); } catch (e) {}

        if (decodedTail.indexOf('/') !== -1) {
            var relNested = 'Ya Subio/' + decodedTail.replace(/^\/+/, '');
            var outNestedPages = _joinPagesPath(relNested);
            var outNestedR2 = _joinR2Path(relNested);
            var outNested = _preferR2(outNestedR2, outNestedPages);
            logImageDebug('api-images->ya-subio-nested', raw, outNested, { tail: decodedTail, prefer: outNestedR2 ? 'r2' : 'pages' });
            return outNested;
        }

        var hint = '';
        if (typeof productOrSupplier === 'string') hint = productOrSupplier;
        else if (productOrSupplier && typeof productOrSupplier === 'object') hint = String(productOrSupplier.codigo_proveedor || '').trim();
        var lowerHint = (hint || '').toLowerCase();
        var subDir = (lowerHint === 'cristy') ? 'Cristy/' : '';
        var relFlat = 'Ya Subio/' + subDir + decodedTail;
        var outFlatPages = _joinPagesPath(relFlat);
        var outFlatR2 = _joinR2Path(relFlat);
        var outFlat = _preferR2(outFlatR2, outFlatPages);
        logImageDebug('api-images->ya-subio-flat', raw, outFlat, { tail: decodedTail, supplierHint: hint || '', prefer: outFlatR2 ? 'r2' : 'pages' });
        return outFlat;
    }

    // CHANGE: 数据库常见相对路径（如 "Ya Subio/xxx.jpg"、"Ya%20Subio/xxx.jpg"）统一映射到 Pages 静态目录
    var rawNorm = raw.replace(/\\/g, '/');
    var rawNormDec = rawNorm;
    try { rawNormDec = decodeURIComponent(rawNorm); } catch (e) {}
    if (/^\/?ya\s*subio\//i.test(rawNormDec)) {
        // CHANGE: 本地 127.0.0.1/localhost 调试时，保留原相对路径，交给 Flask 静态目录处理
        // 并优先规避常见坏文件名后缀，减少后端 500
        if (_isLocalHostRuntime()) {
            var localRel = rawNormDec.replace(/^\/+/, '');
            var localOut = '/' + localRel;
            if (/_no_white_no_hay_precio\./i.test(localRel)) {
                localOut = '/' + localRel.replace(/_no_white_no_hay_precio\./i, '_no_white.');
                logImageDebug('relative-ya-subio-local-normalize-nohayprecio', raw, localOut, {});
            } else {
                logImageDebug('relative-ya-subio-local-keep', raw, localOut, {});
            }
            return localOut;
        }

        var relYa2 = rawNormDec.replace(/^\/+/, '');
        relYa2 = relYa2.replace(/^ya\s*subio\//i, 'Ya Subio/').replace(/^Ya\s*Subio\/cristy\//i, 'Ya Subio/Cristy/').replace(/\/cristy\//gi, '/Cristy/');
        // CHANGE: 云端常见坏文件名后缀统一回退
        relYa2 = relYa2.replace(/_no_white_no_hay_precio\./ig, '_no_white.');
        var outYaPages = _joinPagesPath(relYa2);
        var outYaR2 = _joinR2Path(relYa2);
        var outYa = _preferR2(outYaR2, outYaPages);
        logImageDebug('relative-ya-subio->pages', raw, outYa, { prefer: outYaR2 ? 'r2' : 'pages' });
        return outYa;
    }

    var fallback = _getImageBase() + (raw.startsWith('/') ? raw : '/' + raw);
    logImageDebug('fallback-image-base', raw, fallback, {});
    return fallback;
}

// CHANGE: PWA 安装提示（Chrome/Edge 会触发 beforeinstallprompt，保存后供「添加到主屏幕」按钮使用）
let deferredInstallPrompt = null;

// 应用状态
// CHANGE: 默认视图改为 ultimo（自家产品）
const INITIAL_PAGE_SIZE = 100;   // 首屏每页最多 100
const PAGE_SIZE = 100;           // API 分页批次 100（网络侧）
function getUiPageSize() {
    try {
        var dm = Number((typeof navigator !== 'undefined' && navigator.deviceMemory) || 0);
        var ua = (typeof navigator !== 'undefined' && navigator.userAgent) ? String(navigator.userAgent).toLowerCase() : '';
        // CHANGE: 低配 Android 设备减少单屏渲染数量，避免 2k+ 商品时主线程卡顿
        if (dm > 0 && dm <= 4) return 48;
        if (ua.indexOf('android') !== -1 && dm === 0) return 60;
        return PAGE_SIZE;
    } catch (e) {
        return PAGE_SIZE;
    }
}
const AppState = {
    products: [],
    productsVisibleCount: PAGE_SIZE,
    currentPage: 1,
    cart: [],
    orders: [],
    currentView: 'ultimo',
    lastOrderId: null,
    lastOrderSummary: null,
    lastOrderCart: null,
    _lastRenderProductsToRender: [],
    _refillLastAt: 0,
    _refillTimer: null,
    _badImagePaths: {},
    _rerenderAfterImageErrorsTimer: null,
    /** true：正在拉取产品列表，renderProducts 空列表时应显示「加载中」而非「无产品」 */
    _productsLoading: false,
    // CHANGE: 记录 hash 直达自动滚动已执行的 segment，避免重复自动回滚到同一产品
    _hashAutoScrolledSegment: '',
    categories: [],
    /** 当前按分类筛选时 API 使用的分类名（与后端 categoria 一致） */
    catalogCategoryName: '',
    /** 是否为「仅看某分类」模式（与 Ultimo/Productos 全量列表区分） */
    _catalogMode: false,
    /** 搜索结果是否处于激活状态（激活时禁止滚动触发全量 render 覆盖） */
    _searchActive: false,
    _productsPrefetchPromise: null,
    _productsPrefetchBuffer: null,
    // CHANGE: 分片渲染任务号，防止旧任务晚到覆盖新页面
    _renderChunkJobId: 0
};

// CHANGE: 产品列表本地缓存（先秒开再后台刷新），减少每次进入都长时间等待
var PRODUCTS_CACHE_TTL_MS = 1000 * 60 * 20; // 20 分钟
function _productsCacheKey(supplier) {
    var sp = (supplier != null && supplier !== '') ? String(supplier) : 'Cristy';
    return 'pwa_products_cache_v1_' + sp;
}

function readProductsCache(supplier) {
    try {
        if (typeof localStorage === 'undefined') return null;
        var raw = localStorage.getItem(_productsCacheKey(supplier));
        if (!raw) return null;
        var parsed = JSON.parse(raw);
        if (!parsed || !Array.isArray(parsed.data) || !parsed.ts) return null;
        if ((Date.now() - Number(parsed.ts || 0)) > PRODUCTS_CACHE_TTL_MS) return null;
        return parsed.data;
    } catch (e) {
        return null;
    }
}

function writeProductsCache(supplier, data) {
    try {
        if (typeof localStorage === 'undefined') return;
        if (!Array.isArray(data) || !data.length) return;
        localStorage.setItem(_productsCacheKey(supplier), JSON.stringify({
            ts: Date.now(),
            data: data
        }));
    } catch (e) {
        // ignore cache write errors
    }
}

// CHANGE: 免登录模式 - 用 session_id 标识购物车/订单，自动记录客户资料
function getOrCreateSessionId() {
    try {
        let sid = localStorage.getItem('pwa_session_id');
        if (!sid) {
            sid = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : 's_' + Date.now() + '_' + Math.random().toString(36).slice(2);
            localStorage.setItem('pwa_session_id', sid);
        }
        return sid;
    } catch (e) {
        return 's_' + Date.now() + '_' + Math.random().toString(36).slice(2);
    }
}

// CHANGE: 免登录模式 - 不再需要认证相关函数，保留空实现避免引用错误
function updateUserUI() {
    const userInfo = document.getElementById('userInfo');
    const loginBtn = document.getElementById('loginBtn');
    if (userInfo) userInfo.classList.add('hidden');
    if (loginBtn) loginBtn.classList.add('hidden');
}

// ===== API调用函数 =====

// CHANGE: Failed to fetch / 429 / 503 时做多次退避重试；并对同一 GET endpoint 做并发去重，减少限流
var _apiInFlightGet = {};
async function apiRequest(endpoint, options = {}) {
    const url = `${CONFIG.API_BASE_URL}${endpoint}`;
    const method = options.method || 'GET';
    const retryCount = options._retryCount || 0;
    const isRetry = retryCount > 0;
    const maxRetry = (typeof options._maxRetry === 'number') ? options._maxRetry : 3;

    console.log(`📡 [API] ${method} ${url}` + (isRetry ? ' (reintento)' : ''));
    if (options.body) {
        console.log('📤 请求体:', options.body);
    }

    try {
        var dedupeKey = '';
        if (method === 'GET' && !options._skipDedupe) {
            dedupeKey = endpoint;
            if (_apiInFlightGet[dedupeKey]) return _apiInFlightGet[dedupeKey];
        }

        var runner = (async function() {
        var headers = { ...(options.headers || {}) };
        // CHANGE: 仅购物车/订单/结账需 session，产品列表不加自定义头避免 CORS 预检
        if (/^\/(cart|checkout|orders)/.test(endpoint)) {
            headers['X-Session-Id'] = getOrCreateSessionId();
        }
        if (method !== 'GET' && options.body) {
            headers['Content-Type'] = 'application/json';
        }
        var opts = { headers: headers };
        if (options.method) opts.method = options.method;
        if (options.body !== undefined) opts.body = options.body;
        if (options.signal) opts.signal = options.signal;
        if (options.mode) opts.mode = options.mode;
        if (options.credentials) opts.credentials = options.credentials;
        var response = await fetch(url, opts);
        var upstreamSource = response.headers.get('x-api-proxy-source') || '';
        var upstreamTarget = response.headers.get('x-api-proxy-target') || '';
        var upstreamAttempts = response.headers.get('x-api-proxy-attempts') || '';
        var upstreamCache = response.headers.get('x-api-cache') || '';
        console.log('📥 [API] 响应状态: ' + response.status + ' ' + (response.statusText || ''));
        if (upstreamSource || upstreamTarget || upstreamAttempts || upstreamCache) {
            console.log('📥 [API] 上游信息:', {
                source: upstreamSource,
                target: upstreamTarget,
                attempts: upstreamAttempts,
                cache: upstreamCache,
                endpoint: endpoint
            });
        }
        if (typeof window !== 'undefined') {
            try {
                window.__lastApiResponseMeta = {
                    endpoint: endpoint,
                    method: method,
                    status: Number(response.status || 0),
                    source: upstreamSource || '',
                    target: upstreamTarget || '',
                    attempts: upstreamAttempts || '',
                    cache: upstreamCache || '',
                    when: Date.now()
                };
            } catch (_) {}
        }
        var responseText = await response.text();
        if (responseText && responseText.length <= 200) console.log('📥 响应内容:', responseText.substring(0, 200));

        var trimmed = (responseText || '').trim();
        var isHtmlResponse = trimmed.startsWith('<!DOCTYPE') || trimmed.startsWith('<!doctype') || trimmed.startsWith('<html');

        // CHANGE: 429/502/503/504 即使返回 HTML（例如 Cloudflare/Render 错误页）也要重试，避免直接抛错
        if ((response.status === 429 || response.status === 502 || response.status === 503 || response.status === 504) && method === 'GET' && retryCount < maxRetry) {
            var nextRetry = retryCount + 1;
            var retryDelay = (response.status === 429)
                ? (2000 + (nextRetry * 2500))
                : (3000 + (nextRetry * 3000));
            return new Promise(function(resolve, reject) {
                setTimeout(function() {
                    apiRequest(endpoint, Object.assign({}, options, { _retryCount: nextRetry, _skipDedupe: true, _maxRetry: maxRetry })).then(resolve).catch(reject);
                }, retryDelay);
            });
        }

        var data = null;
        if (!isHtmlResponse) {
            try {
                data = JSON.parse(responseText);
            } catch (e) {
                console.error('❌ JSON解析失败:', e);
                throw new Error('响应不是有效的JSON: ' + response.status + ' ' + (response.statusText || ''));
            }
        } else {
            console.error('❌ 服务器返回了HTML错误页面而不是JSON');
        }

        if (!response.ok) {
            var errMsg = data && (data.error || data.message)
                ? (data.error || data.message)
                : (isHtmlResponse ? '上游服务暂时不可用（HTML错误页）' : (responseText || '').substring(0, 100));

            // CHANGE: 404/5xx 直接显示底部技术状态条，便于快速复制给后端/Cloudflare
            if (response.status === 404 || response.status >= 500) {
                showTechStatusBar({
                    endpoint: endpoint,
                    status: Number(response.status || 0),
                    source: upstreamSource || upstreamTarget || 'unknown',
                    attempts: upstreamAttempts || '1'
                });
            }

            throw new Error('API错误: ' + response.status + ' - ' + errMsg);
        }

        // CHANGE: 请求恢复成功时自动收起技术状态条
        hideTechStatusBar();

        if (!data) {
            throw new Error('API错误: ' + response.status + ' - 预期JSON但收到空响应');
        }

        console.log('✅ [API] 请求成功:', data);
        return data;
        })();

        if (dedupeKey) {
            _apiInFlightGet[dedupeKey] = runner;
            return await runner.finally(function() { delete _apiInFlightGet[dedupeKey]; });
        }
        return await runner;
    } catch (error) {
        console.error('❌ [API] 请求失败:', error);
        var isFailedFetch = (error && (error.message === 'Failed to fetch' || error.name === 'TypeError')) || (error.message && String(error.message).indexOf('fetch') !== -1);
        if (!options.silent) {
            if (isFailedFetch && typeof showToast === 'function') {
                showToast('No se pudo conectar con la API. Verifique Cloudflare Functions y la red.', 'error');
            } else if (typeof showToast === 'function') {
                showToast('Error de red, por favor intente más tarde', 'error');
            }
            if (typeof window !== 'undefined') {
                try {
                    window.__lastApiErrorMeta = {
                        endpoint: endpoint,
                        method: method,
                        message: String((error && error.message) || ''),
                        when: Date.now()
                    };
                    console.error('❌ [API] 错误定位:', window.__lastApiErrorMeta);

                    var msg = String((error && error.message) || '');
                    var statusMatch = msg.match(/API错误:\s*(\d{3})/);
                    var statusNum = statusMatch ? Number(statusMatch[1]) : 0;
                    var upstreamMeta = window.__lastApiResponseMeta || {};
                    if (statusNum === 404 || statusNum >= 500 || isFailedFetch) {
                        showTechStatusBar({
                            endpoint: endpoint,
                            status: statusNum || 'network',
                            source: upstreamMeta.source || upstreamMeta.target || (isFailedFetch ? 'network' : 'unknown'),
                            attempts: upstreamMeta.attempts || '1'
                        });
                    }
                } catch (_) {}
            }
        }
        // GET 且未重试过则 3 秒后自动重试一次（Render 冷启动）
        if (method === 'GET' && retryCount < maxRetry) {
            var nextRetry = retryCount + 1;
            var delay = 2000 + (nextRetry * 2500);
            return new Promise(function(resolve, reject) {
                setTimeout(function() {
                    apiRequest(endpoint, Object.assign({}, options, { _retryCount: nextRetry, _skipDedupe: true, _maxRetry: maxRetry })).then(resolve).catch(reject);
                }, delay);
            });
        }
        throw error;
    }
}

// CHANGE: 按 product_code（或 id）去重，同一产品只保留一条，避免成本/产品重复显示
// NOTE: 规范化 key（去 ._AI 后缀、小写）以应对多供应商并集搜索时 X27/x27/X27._AI 等视为同一产品
function _dedupeKey(p) {
    var code = (p.product_code != null && String(p.product_code).trim() !== '') ? String(p.product_code).trim() : '';
    var raw = code || String(p.id != null ? p.id : '').trim();
    if (!raw) return '';
    var norm = raw.replace(/\._A[Ii]\s*$/i, '').trim().toLowerCase();
    return norm || raw.toLowerCase();
}
function dedupeProductsByCode(arr, keepPlaceholder) {
    if (!Array.isArray(arr)) return [];
    var seen = {};
    var out = arr.filter(function(p) {
        if (!p || typeof p !== 'object') return false;
        if (!keepPlaceholder && isPlaceholderProduct(p)) return false;
        var key = _dedupeKey(p);
        if (!key) return true;
        if (seen[key]) return false;
        seen[key] = true;
        return true;
    });
    return out;
}

function hasUsableImagePath(product) {
    if (!product || typeof product !== 'object') return false;
    var raw = String(product.image_path || '').trim();
    if (!raw) return false;
    if (raw.indexOf('data:image') !== -1) return false;
    var key = raw.toLowerCase();
    if (AppState && AppState._badImagePaths && AppState._badImagePaths[key]) return false;

    // CHANGE: 已确认本地这批编号段图片无像素，渲染前直接过滤，避免继续显示空白卡片
    var m = raw.match(/woni_import_and_export_(\d+)(?:_no_white)?\./i);
    if (m) {
        var n = Number(m[1]);
        if ((n >= 2209 && n <= 2210) ||
            (n >= 2216 && n <= 2219) ||
            (n >= 2222 && n <= 2227) ||
            (n >= 2232 && n <= 2235) ||
            (n >= 2243 && n <= 2250) ||
            n === 2257) {
            return false;
        }
    }
    return true;
}

function _buildCategoryCountMapFromRows(rows) {
    var map = {};
    var list = Array.isArray(rows) ? rows : [];
    // CHANGE: 首页分类数量只统计“可实际展示”的去重产品，避免 OCR 占位/无效图记录把数量抬高到远超实际可见商品数
    var uniqueRows = dedupeProductsByCode(list, false).filter(function(p) {
        return hasUsableImagePath(p);
    });
    uniqueRows.forEach(function(p) {
        if (!p || typeof p !== 'object') return;
        var cats = _splitCatalogCategoryTokens(p.category || '');
        if (!cats.length) return;
        cats.forEach(function(cat) {
            var n = _normCatalogName(cat);
            if (!n) return;
            map[n] = Number(map[n] || 0) + 1;
        });
    });
    return map;
}

async function fetchProductsForCategoryCounts() {
    // CHANGE: 云端 Worker 可能限制单次 limit；分页拉完整数据，避免分类数量只按第一页统计。
    var allRows = [];
    var limit = 1000;
    var offset = 0;
    var guard = 0;
    while (guard < 20) {
        var rowsResult = await apiRequest('/products?limit=' + limit + '&offset=' + offset + '&_=' + (Date.now ? Date.now() : 0), { silent: true });
        var rows = (rowsResult && rowsResult.success && Array.isArray(rowsResult.data)) ? rowsResult.data : [];
        if (!rows.length) break;
        allRows = allRows.concat(rows);
        if (rows.length < limit) break;
        offset += rows.length;
        guard += 1;
    }
    return allRows;
}

async function fetchCategoryCountsSnapshot() {
    if (typeof window === 'undefined' || !window.location) return null;
    var host = String(window.location.hostname || '').toLowerCase();
    var isLocal = host === '127.0.0.1' || host === 'localhost';
    if (isLocal) return null;
    try {
        // CHANGE: 同步脚本会生成本地网页口径的分类快照，云端优先用它避免 API 口径差异。
        var res = await fetch('category_counts_snapshot.json?_=' + (Date.now ? Date.now() : 0), { cache: 'no-store' });
        if (!res || !res.ok) return null;
        var json = await res.json();
        return (json && json.success && Array.isArray(json.data)) ? json.data : null;
    } catch (e) {
        return null;
    }
}

async function fetchCategories() {
    try {
        var result = await apiRequest('/categories', { silent: true });
        var apiList = (result && result.success && Array.isArray(result.data)) ? result.data.slice() : [];

        // CHANGE: 分类数量以“可实际展示的去重产品”为准，避免重复记录、OCR 占位图、无效图片路径把数量抬高。
        var rows = await fetchProductsForCategoryCounts();
        var counted = _buildCategoryCountMapFromRows(rows);
        var snapshotList = await fetchCategoryCountsSnapshot();
        if (Array.isArray(snapshotList) && snapshotList.length) {
            apiList = snapshotList;
            counted = {};
        }

        var merged = {};
        apiList.forEach(function(c) {
            if (!c || typeof c !== 'object') return;
            var name = String(c.name || '').trim();
            if (!name) return;
            var key = _normCatalogName(name);
            merged[key] = {
                name: name,
                count: (counted[key] != null) ? Number(counted[key] || 0) : Number(c.count || 0)
            };
        });

        Object.keys(counted).forEach(function(key) {
            if (merged[key]) return;
            merged[key] = { name: key.toUpperCase(), count: Number(counted[key] || 0) };
        });

        AppState.categories = Object.keys(merged).map(function(k) { return merged[k]; }).sort(function(a, b) {
            var an = String((a && a.name) || '');
            var bn = String((b && b.name) || '');
            if (an === 'Cristy') return -1;
            if (bn === 'Cristy') return 1;
            if (an === 'Otros') return 1;
            if (bn === 'Otros') return -1;
            return Number((b && b.count) || 0) - Number((a && a.count) || 0);
        });

        renderCatalogCategoryTags();
    } catch (e) {
        console.error('Error al cargar categorías:', e);
        var wrap = document.getElementById('catalogCategoryTags');
        if (wrap) wrap.innerHTML = '<span class="catalog-category-tag catalog-category-tag--muted">No se pudieron cargar las categorías</span>';
    }
}


// CHANGE: 过滤 OCR 占位产品（常见为 -SNlez2S_xxx + Producto Nuevo + 全部价格为 0）
// 这些记录通常无有效图片/价格，展示出来会大量出现“Sin imagen / Consultar precio”
function isPlaceholderProduct(p) {
    if (!p || typeof p !== 'object') return false;
    var code = String(p.product_code || '').trim();
    var name = String(p.name || '').trim().toLowerCase();
    var price = Number(p.price || 0);
    var wholesale = Number(p.wholesale_price || 0);
    var bulk = Number(p.bulk_price || 0);
    var img = String(p.image_path || '').toLowerCase();
    var codeLooksTemp = /^-snlez2s_/i.test(code);
    var noPrice = price <= 0 && wholesale <= 0 && bulk <= 0;
    var nameLooksTemp = name === 'producto nuevo' || name === 'nuevo producto';
    var imgLooksTemp = img.indexOf('_no_hay_precio') !== -1;
    return (codeLooksTemp && noPrice && (nameLooksTemp || imgLooksTemp));
}

async function fetchProductsPageBySupplier(effectiveSupplier, offset, limit, loadTimeoutMs) {
    let url = '/products?limit=' + Number(limit || PAGE_SIZE) + '&offset=' + Number(offset || 0);
    if (effectiveSupplier) {
        url += '&supplier=' + encodeURIComponent(effectiveSupplier);
    }
    url += '&_=' + (Date.now ? Date.now() : 0);

    const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => reject(new Error('Tiempo de espera agotado. Compruebe la conexión o intente más tarde.')), loadTimeoutMs);
    });

    const result = await Promise.race([apiRequest(url), timeoutPromise]);
    const ok = result && (result.success === true || Array.isArray(result.data));
    if (!ok) return result;

    const rows = Array.isArray(result.data) ? result.data : [];
    var totalRaw = (result.total != null && result.total !== '') ? result.total : (result.pagination && result.pagination.total);
    const totalNum = Number(totalRaw);
    const total = (Number.isFinite(totalNum) && totalNum >= 0) ? totalNum : null;

    return {
        success: true,
        data: rows,
        total: total,
        nextOffset: Number(offset || 0) + rows.length,
        hasMore: total != null ? ((Number(offset || 0) + rows.length) < total) : (rows.length >= Number(limit || PAGE_SIZE))
    };
}

// Obtener lista de productos
// CHANGE: 支持 supplier 参数，用于区分自家产品和其他供应商产品；带超时避免一直 Cargando
// 无 supplier 时默认 'Cristy'（ULTIMO 页），避免后端无 supplier 时返回空列表
// CHANGE: 采用分页拉取并合并（offset 递增），确保「所见即所得」：只要云端有记录就能全部显示，不再被 limit=500 截断
async function fetchProducts(supplier = null, retryCount = 0) {
    AppState._catalogMode = false;
    AppState.catalogCategoryName = '';
    renderCatalogCategoryTags();
    const LOAD_TIMEOUT_MS = 45000;
    const effectiveSupplier = supplier != null && supplier !== '' ? supplier : 'Cristy';

    var cachedProducts = readProductsCache(effectiveSupplier);
    if (cachedProducts && cachedProducts.length) {
        AppState.products = dedupeProductsByCode(cachedProducts).filter(function(p) { return hasUsableImagePath(p); });
        AppState.productsVisibleCount = getUiPageSize();
        AppState.currentPage = 1;
        AppState._lastProductsSupplier = effectiveSupplier;
        AppState._productsLoading = false;
        AppState._productsNextOffset = AppState.products.length;
        // CHANGE: 缓存仅用于秒开，仍需允许继续分页拉取云端最新数据
        AppState._productsHasMore = true;
        AppState._productsPrefetchBuffer = null;
        AppState._productsPrefetchPromise = null;
        var statusWrap = document.getElementById('productsLoadStatusWrap');
        if (statusWrap) {
            statusWrap.innerHTML = '<div class="products-load-status" id="productsLoadStatus">Mostrando ' + AppState.productsVisibleCount + ' de ' + AppState.products.length + ' · Actualizando…</div>';
        }
        renderProducts();
    }

    // CHANGE: API 过慢时先展示本地缓存（避免空等），后台继续拉取
    var slowCacheTimer = null;
    if ((!cachedProducts || !cachedProducts.length) && typeof readProductsCache === 'function') {
        var slowCache = readProductsCache(effectiveSupplier);
        if (slowCache && slowCache.length) {
            slowCacheTimer = setTimeout(function() {
                if (!AppState._productsLoading) return;
                if (AppState.products && AppState.products.length) return;
                AppState.products = dedupeProductsByCode(slowCache).filter(function(p) { return hasUsableImagePath(p); });
                AppState.productsVisibleCount = PAGE_SIZE;
                AppState.currentPage = 1;
                AppState._lastProductsSupplier = effectiveSupplier;
                AppState._productsNextOffset = AppState.products.length;
                AppState._productsHasMore = true;
                AppState._productsPrefetchBuffer = null;
                AppState._productsPrefetchPromise = null;
                renderProducts();
                showToast('Mostrando caché mientras se actualiza…', 'info');
            }, 3500);
        }
    }

    var productsGrid = document.getElementById('productsGrid');
    if (productsGrid) {
        var hint = retryCount > 0 ? 'Reintentando...' : 'Cargando catálogo desde Cloudflare...';
        var seg = (function() { var h = (location && location.hash) ? location.hash.trim() : ''; if (h.indexOf('#/product/') !== 0) return ''; return h.replace('#/product/', '').replace(/^\/+|\/+$/g, '').trim(); })();
        if (seg && !supplier) {
            return;
        }
        if (!cachedProducts || !cachedProducts.length) {
            renderProductSkeletons(INITIAL_PAGE_SIZE);
            productsGrid.insertAdjacentHTML('beforeend', '<div class="empty-state" style="grid-column:1/-1;text-align:center;padding:1rem 2rem;color:var(--text-light);"><small>' + hint + '</small></div>');
        } else {
            showToast('Mostrando caché local · actualizando en segundo plano', 'info');
        }
    }

    AppState._productsLoading = true;
    AppState._productsLoadingMore = false;
    try {
        // CHANGE: 首屏只拉第一页并立即渲染，避免云端数据量大时页面长时间“卡住”在 Cargando。
        // 后续页由无限滚动 + 预取机制按需加载。
        const pageResult = await fetchProductsPageBySupplier(effectiveSupplier, 0, PAGE_SIZE, LOAD_TIMEOUT_MS);
        var ok = pageResult && (pageResult.success === true || Array.isArray(pageResult.data));
        if (!ok) {
            console.error('❌ [fetchProducts] API返回错误:', pageResult && pageResult.error ? pageResult.error : '未知错误');
            AppState.products = [];
            AppState._productsLoading = false;
            renderProducts();
            showToast('Error al cargar productos', 'error');
            return;
        }

        var firstRows = (Array.isArray(pageResult.data) ? pageResult.data.slice() : []).filter(function(p) { return !isPlaceholderProduct(p); });
        var newProducts = dedupeProductsByCode(firstRows);
        var statusWrapLoop = document.getElementById('productsLoadStatusWrap');
        if (statusWrapLoop) {
            statusWrapLoop.innerHTML = '<div class="products-load-status" id="productsLoadStatus">Cargando catálogo... ' + newProducts.length + ' productos</div>';
        }

        var viewMatch = (effectiveSupplier === 'Cristy' && AppState.currentView === 'ultimo') || (effectiveSupplier === 'others' && AppState.currentView === 'products');
        var isFirstLoadCristy = effectiveSupplier === 'Cristy' && newProducts.length > 0 && AppState.products.length === 0;

        if (viewMatch || isFirstLoadCristy) {
            if (AppState._pendingHashProduct) {
                var hp = AppState._pendingHashProduct;
                if (!newProducts.some(function(px) { return String(px.id) === String(hp.id) || String(px.product_code || '') === String(hp.product_code || ''); })) {
                    newProducts.unshift(hp);
                }
                AppState._pendingHashProduct = null;
            }

            AppState.products = newProducts;
            AppState.productsVisibleCount = PAGE_SIZE;
            AppState.currentPage = 1;
            AppState._lastProductsSupplier = effectiveSupplier;
            AppState._productsNextOffset = Number(pageResult.nextOffset || newProducts.length);
            AppState._productsHasMore = !!pageResult.hasMore;
            AppState._productsPrefetchBuffer = null;
            AppState._productsPrefetchPromise = null;
            writeProductsCache(effectiveSupplier, newProducts);
            AppState._productsLoading = false;
            renderProducts();

            var hashSeg = (function() { var h = (location && location.hash) ? location.hash.trim() : ''; if (h.indexOf('#/product/') !== 0) return ''; return h.replace('#/product/', '').replace(/^\/+|\/+$/g, '').trim(); })();
            if (hashSeg) {
                requestAnimationFrame(function() {
                    requestAnimationFrame(function() {
                        var r = applyProductHashAnchor();
                        if (r && !r.applied && r.segment && typeof fetchSingleProductForHash === 'function') fetchSingleProductForHash(r.segment);
                    });
                });
            }
        } else {
            AppState._productsLoading = false;
        }
    } catch (error) {
        if (slowCacheTimer) { clearTimeout(slowCacheTimer); slowCacheTimer = null; }
        var isTimeout = error && error.message && error.message.indexOf('Tiempo de espera') !== -1;
        if (isTimeout && retryCount < 1) {
            await new Promise(function(r) { setTimeout(r, 2000); });
            return await fetchProducts(supplier, retryCount + 1);
        }
        // CHANGE: 若已降级显示缓存，则保留缓存画面并提示，不再清空成空页面
        if (AppState.products && AppState.products.length > 0) {
            AppState._lastProductsError = error;
            AppState._productsLoading = false;
            showToast('Conexión lenta: mostrando caché local', 'warning');
            renderProducts();
            return;
        }
        AppState.products = [];
        AppState._lastProductsError = error;
        AppState._productsLoading = false;
        renderProducts();
    }
}

// CHANGE: 免登录 - 用 X-Session-Id 从服务端拉取购物车
async function fetchCart() {
    try {
        const result = await apiRequest(`/cart`, { silent: true });
        if (result && result.success) {
            AppState.cart = result.data || [];
            console.log(`🛒 Carrito actualizado: ${AppState.cart.length} artículos`);
            updateCartUI();
        } else {
            console.error('Error al obtener carrito:', result?.error || '未知错误');
            AppState.cart = [];
            updateCartUI();
        }
    } catch (error) {
        // CHANGE: 云端可无购物车接口（404），按空购物车处理，不打断页面、不弹红色报错
        var msg = String((error && error.message) || '');
        if (msg.indexOf('404') !== -1) {
            console.warn('⚠️ /api/cart 不可用（404），按空购物车继续');
        } else {
            console.error('Error al obtener carrito:', error);
        }
        AppState.cart = [];
        updateCartUI();
    }
}

// ===== Modal de selección de cantidad =====
let currentProductForModal = null;

// CHANGE: 支持 id 与 product_code 双查找；搜索/混合来源时产品可能不在 AppState，fallback 到 API 拉取
async function showQuantityModal(productId) {
    console.log('📱 showQuantityModal llamado con productId:', productId);

    // 1) 先按 id 查找
    let product = AppState.products.find(p => String(p.id) === String(productId));
    // 2) 未找到则按 product_code 查找（Cristy 产品 API 可能用 product_code 作 id，PRODUCTOS 用数字 id）
    if (!product) {
        product = AppState.products.find(p => String(p.product_code || '') === String(productId));
    }
    // 3) 仍无则从 API 拉取单产品（搜索/ hash 直达时产品可能不在当前 AppState）
    if (!product && productId) {
        try {
            var result = await apiRequest('/products/' + encodeURIComponent(productId));
            if (result && result.success && result.data) {
                product = result.data;
                if (product && !AppState.products.some(p => String(p.id) === String(product.id) || String(p.product_code || '') === String(product.product_code || ''))) {
                    AppState.products.push(product);
                }
            }
        } catch (e) {
            console.warn('⚠️ [showQuantityModal] API 拉取产品失败:', productId, e);
        }
    }
    if (!product) {
        console.error('❌ Producto no existe:', productId);
        console.error('📦 Productos disponibles:', AppState.products.map(p => p.id));
        showToast('Error: Producto no existe', 'error');
        return;
    }
    
    console.log('✅ Producto encontrado:', product);
    currentProductForModal = product;
    console.log('✅ currentProductForModal establecido:', currentProductForModal);
    
    // Actualizar contenido del modal
    const modal = document.getElementById('quantityModal');
    if (!modal) {
        console.error('❌ Modal no encontrado');
        return;
    }
    
    const productNameEl = document.getElementById('modalProductName');
    const productPriceEl = document.getElementById('modalProductPrice');
    const quantityInput = document.getElementById('quantityInput');
    const totalPriceEl = document.getElementById('modalTotalPrice');
    
    if (!productNameEl || !productPriceEl || !quantityInput || !totalPriceEl) {
        console.error('❌ Elementos del modal no encontrados');
        return;
    }
    
    productNameEl.textContent = product.name;
    // CHANGE: 初始显示根据数量1计算的价格
    const initialPrice = calculatePriceByQuantity(product, 1);
    productPriceEl.textContent = `$${initialPrice.toFixed(2)}`;
    quantityInput.value = 1;
    updateModalTotalPrice();
    
    // Mostrar modal
    modal.classList.remove('hidden');
    modal.focus(); // 使模态框可接收键盘事件
    
    // Enfocar en el campo de entrada de cantidad
    setTimeout(() => {
        quantityInput.focus();
        quantityInput.select();
    }, 100);
}

function hideQuantityModal() {
    const modal = document.getElementById('quantityModal');
    modal.classList.add('hidden');
    currentProductForModal = null;
}

function updateModalTotalPrice() {
    if (!currentProductForModal) return;
    
    const quantityInput = document.getElementById('quantityInput');
    const totalPriceEl = document.getElementById('modalTotalPrice');
    const productPriceEl = document.getElementById('modalProductPrice');
    
    // CHANGE: 允许输入框为空，如果为空则使用1作为默认值计算价格
    const inputValue = quantityInput.value.trim();
    let quantity = parseInt(inputValue);
    
    // 如果为空或无效，使用1作为默认值（但不更新输入框，允许用户继续输入）
    if (isNaN(quantity) || inputValue === '') {
        quantity = 1;
    }
    
    // CHANGE: 根据数量计算单价
    const unitPrice = calculatePriceByQuantity(currentProductForModal, quantity);
    const total = unitPrice * quantity;
    
    productPriceEl.textContent = `$${unitPrice.toFixed(2)}`;
    totalPriceEl.textContent = `Total: $${total.toFixed(2)}`;
}

function confirmAddToCart() {
    console.log('✅ confirmAddToCart llamado');
    console.log('📦 currentProductForModal:', currentProductForModal);
    
    if (!currentProductForModal) {
        console.error('❌ currentProductForModal es null');
        console.error('📦 AppState.products:', AppState.products);
        showToast('Error: Producto no disponible', 'error');
        hideQuantityModal();
        return;
    }
    
    const quantityInput = document.getElementById('quantityInput');
    if (!quantityInput) {
        console.error('❌ quantityInput no encontrado');
        showToast('Error: Campo de cantidad no encontrado', 'error');
        return;
    }
    
    // CHANGE: 验证数量，如果为空或无效，使用1
    const inputValue = quantityInput.value.trim();
    let quantity = parseInt(inputValue);
    
    if (isNaN(quantity) || inputValue === '') {
        quantity = 1;
        quantityInput.value = quantity;
    }
    
    console.log('📊 Cantidad seleccionada:', quantity);
    
    if (quantity < 1) {
        quantity = 1;
        quantityInput.value = quantity;
        showToast('La cantidad debe ser mayor que 0', 'error');
        return;
    }
    
    if (quantity > 999) {
        quantity = 999;
        quantityInput.value = quantity;
    }
    
    if (quantity > 999) {
        showToast('La cantidad no puede exceder 999', 'error');
        return;
    }
    
    // Guardar productoID antes de cerrar modal
    const productId = currentProductForModal.id;
    console.log('🆔 ID del producto guardado:', productId);
    
    if (!productId) {
        console.error('❌ ID del producto no disponible');
        console.error('📦 currentProductForModal completo:', currentProductForModal);
        showToast('Error: ID del producto no disponible', 'error');
        return;
    }
    
    // CHANGE: 与购物车页一致的单价（按数量层级），传给后端直接采用，保证其他位置“只读结果就一致”
    const unitPrice = calculatePriceByQuantity(currentProductForModal, quantity);
    console.log('🛒 按数量层级单价:', unitPrice, 'quantity=', quantity);
    
    // Cerrar modal (esto establecerá currentProductForModal = null)
    hideQuantityModal();
    
    // Añadir al carrito，传入单价以便后端照存、不重算
    console.log('🛒 Añadiendo al carrito: productId=', productId, 'quantity=', quantity, 'price=', unitPrice);
    addToCart(productId, quantity, unitPrice);
}

// Inicializar eventos del modal de selección de cantidad
function initQuantityModal() {
    const modal = document.getElementById('quantityModal');
    const closeBtn = document.getElementById('modalCloseBtn');
    const cancelBtn = document.getElementById('modalCancelBtn');
    const confirmBtn = document.getElementById('modalConfirmBtn');
    const decreaseBtn = document.getElementById('decreaseBtn');
    const increaseBtn = document.getElementById('increaseBtn');
    const quantityInput = document.getElementById('quantityInput');
    
    // Botón de cerrar
    closeBtn.addEventListener('click', hideQuantityModal);
    cancelBtn.addEventListener('click', hideQuantityModal);
    
    // Botón de confirmar
    confirmBtn.addEventListener('click', confirmAddToCart);
    
    // Cerrar al hacer clic en el fondo
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            hideQuantityModal();
        }
    });
    
    // Cerrar con tecla ESC
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideQuantityModal();
        }
    });
    
    // 数量增减按钮
    decreaseBtn.addEventListener('click', () => {
        const current = parseInt(quantityInput.value) || 1;
        if (current > 1) {
            quantityInput.value = current - 1;
            updateModalTotalPrice();
        }
    });
    
    increaseBtn.addEventListener('click', () => {
        const current = parseInt(quantityInput.value) || 1;
        if (current < 999) {
            quantityInput.value = current + 1;
            updateModalTotalPrice();
        }
    });
    
    // Cambio en el campo de entrada de cantidad
    // CHANGE: 允许用户删除数字并输入新数字，只在输入有效数字时更新价格
    quantityInput.addEventListener('input', () => {
        const inputValue = quantityInput.value.trim();
        
        // 如果输入框为空，允许保持为空（不强制设置为1）
        if (inputValue === '') {
            return; // 允许用户删除所有内容
        }
        
        // 尝试解析为数字
        let value = parseInt(inputValue);
        
        // 如果解析失败，不更新值（允许用户继续输入）
        if (isNaN(value)) {
            return;
        }
        
        // 限制范围
        if (value < 1) {
            value = 1;
            quantityInput.value = value;
        } else if (value > 999) {
            value = 999;
            quantityInput.value = value;
        }
        
        // 更新总价（只在有有效数字时）
        updateModalTotalPrice();
    });
    
    // CHANGE: 当输入框失去焦点时，验证并设置默认值
    quantityInput.addEventListener('blur', () => {
        const inputValue = quantityInput.value.trim();
        let value = parseInt(inputValue);
        
        // 如果为空或无效，设置为1
        if (isNaN(value) || inputValue === '') {
            value = 1;
            quantityInput.value = value;
        } else {
            // 限制范围
            if (value < 1) value = 1;
            if (value > 999) value = 999;
            quantityInput.value = value;
        }
        
        updateModalTotalPrice();
    });
    
    // CHANGE: 移动端键盘打开时，滚动弹窗使确认按钮可见，避免被键盘遮挡
    if ('ontouchstart' in window) {
        function scrollModalFooterIntoView() {
            const footer = modal.querySelector('.modal-footer');
            if (footer && !modal.classList.contains('hidden')) {
                footer.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        }
        quantityInput.addEventListener('focus', function() {
            setTimeout(scrollModalFooterIntoView, 400);
        });
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', function() {
                if (!modal.classList.contains('hidden') && document.activeElement === quantityInput) {
                    setTimeout(scrollModalFooterIntoView, 100);
                }
            });
        }
    }
}

// Añadir al carrito。unitPrice 可选：与购物车页一致的单价值，后端直接采用以保证各处“只读结果一致”
async function addToCart(productId, quantity = 1, unitPrice = null) {
    try {
        console.log('='.repeat(50));
        console.log(`🛒 [ADD_TO_CART] Iniciando adición de producto al carrito`);
        console.log(`   ID del Producto: ${productId}`);
        console.log(`   Cantidad: ${quantity}`);
        console.log(`   Precio (tier): ${unitPrice != null ? unitPrice : '(backend)'}`);
        console.log(`   API地址: ${CONFIG.API_BASE_URL}/cart/add`);
        if (!productId) {
            console.error('❌ ID del producto vacío!');
            showToast('Error: ID del producto inválido', 'error');
            return;
        }
        
        const requestBody = {
            product_id: productId,
            quantity: quantity
        };
        if (unitPrice != null && typeof unitPrice === 'number' && unitPrice > 0) {
            requestBody.price = unitPrice;
        }
        
        console.log('📤 发送请求:', JSON.stringify(requestBody, null, 2));
        
        const result = await apiRequest('/cart/add', {
            method: 'POST',
            body: JSON.stringify(requestBody)
        });
        
        console.log('📥 API响应:', JSON.stringify(result, null, 2));
        
        if (result.success) {
            console.log('✅ API respondió exitosamente, actualizando carrito...');
            // Actualizar carrito inmediatamente
            await fetchCart();
            showToast('Producto agregado al carrito', 'success');
            console.log('✅ Producto añadido exitosamente al carrito');
        } else {
            console.error('❌ API respondió con error:', result.error);
            showToast(result.error || 'Error al agregar producto', 'error');
        }
        console.log('='.repeat(50));
    } catch (error) {
        console.error('='.repeat(50));
        console.error('❌ [ADD_TO_CART] 异常:', error);
        console.error('错误堆栈:', error.stack);
        console.error('='.repeat(50));
        showToast('Error de conexión al agregar producto', 'error');
    }
}

// Actualizar cantidad de productos en el carrito。unitPrice 可选，与 add 一致保证后端照存
async function updateCartItem(productId, quantity, unitPrice = null) {
    try {
        const body = {
            product_id: productId,
            quantity: quantity
        };
        if (unitPrice != null && typeof unitPrice === 'number' && unitPrice > 0) {
            body.price = unitPrice;
        }
        const result = await apiRequest('/cart/update', {
            method: 'POST',
            body: JSON.stringify(body)
        });
        
        if (result.success) {
            await fetchCart();
        }
    } catch (error) {
        console.error('Error al actualizar carrito:', error);
    }
}

// Eliminar producto del carrito
async function removeFromCart(productId) {
    try {
        const result = await apiRequest('/cart/remove', {
            method: 'POST',
            body: JSON.stringify({
                product_id: productId
            })
        });
        
        if (result.success) {
            await fetchCart();
            showToast('Producto eliminado del carrito', 'success');
        }
    } catch (error) {
        console.error('Error al eliminar producto:', error);
    }
}

// Vaciar carrito
async function clearCart(silent = false) {
    try {
        const result = await apiRequest('/cart/clear', {
            method: 'POST',
            body: JSON.stringify({})
        });
        
        if (result.success) {
            await fetchCart();
            if (!silent) {
                showToast('Carrito vaciado', 'success');
            }
        }
    } catch (error) {
        console.error('Error al vaciar carrito:', error);
    }
}

// CHANGE: 保存客户信息到localStorage
function saveCustomerInfo(customerInfo) {
    try {
        localStorage.setItem('customer_info', JSON.stringify(customerInfo));
        console.log('✅ 客户信息已保存到localStorage');
    } catch (error) {
        console.error('❌ 保存客户信息失败:', error);
    }
}

// CHANGE: 从localStorage加载客户信息
function loadCustomerInfo() {
    try {
        const savedInfo = localStorage.getItem('customer_info');
        if (savedInfo) {
            return JSON.parse(savedInfo);
        }
    } catch (error) {
        console.error('❌ 加载客户信息失败:', error);
    }
    return null;
}

// CHANGE: 显示客户信息表单（用于修改后重新提交订单）
function showCustomerInfoModalForResubmit() {
    const modal = document.getElementById('customerInfoModal');
    if (!modal) {
        console.error('❌ 客户信息模态框未找到');
        return;
    }
    
    // CHANGE: 设置重新提交模式标志
    modal.dataset.editMode = 'resubmit';
    
    // CHANGE: 加载已保存的客户信息
    const savedInfo = loadCustomerInfo();
    const form = document.getElementById('customerInfoForm');
    const modalTitle = modal.querySelector('.modal-header h3');
    const modalDescription = modal.querySelector('.modal-body p');
    const submitBtn = document.getElementById('customerInfoSubmitBtn');
    
    if (form) {
        if (savedInfo) {
            // 填充已保存的信息
            document.getElementById('cedula').value = savedInfo.cedula || '';
            document.getElementById('nombres').value = savedInfo.nombres || '';
            document.getElementById('direccion').value = savedInfo.direccion || '';
            document.getElementById('provincia').value = savedInfo.provincia || '';
            document.getElementById('ciudad').value = savedInfo.ciudad || '';
            document.getElementById('whatsapp').value = savedInfo.whatsapp || '';
            document.getElementById('email').value = savedInfo.email || '';
            console.log('✅ 已加载保存的客户信息');
        } else {
            // 清空表单
            form.reset();
        }
    }
    
    // CHANGE: 更新标题和按钮文本为重新提交模式
    if (modalTitle) {
        modalTitle.textContent = '✏️ Editar Datos y Reenviar Pedido';
    }
    if (modalDescription) {
        modalDescription.textContent = 'Modifique sus datos personales. Después de guardar, el pedido se reenviará automáticamente con los nuevos datos.';
    }
    if (submitBtn) {
        submitBtn.textContent = 'Guardar y Reenviar Pedido';
    }
    
    // 显示模态框
    modal.classList.remove('hidden');
    
    // 聚焦第一个输入框
    setTimeout(() => {
        const firstInput = document.getElementById('cedula');
        if (firstInput) {
            firstInput.focus();
        }
    }, 100);
}

// CHANGE: 显示客户信息表单（支持编辑模式）
function showCustomerInfoModal(isEditMode = false) {
    const modal = document.getElementById('customerInfoModal');
    if (!modal) {
        console.error('❌ 客户信息模态框未找到');
        return;
    }
    
    // CHANGE: 设置编辑模式标志
    modal.dataset.editMode = isEditMode ? 'true' : 'false';
    
    // CHANGE: 加载已保存的客户信息
    const savedInfo = loadCustomerInfo();
    const form = document.getElementById('customerInfoForm');
    const modalTitle = modal.querySelector('.modal-header h3');
    const modalDescription = modal.querySelector('.modal-body p');
    const submitBtn = document.getElementById('customerInfoSubmitBtn');
    
    if (form) {
        if (savedInfo) {
            // 填充已保存的信息
            document.getElementById('cedula').value = savedInfo.cedula || '';
            document.getElementById('nombres').value = savedInfo.nombres || '';
            document.getElementById('direccion').value = savedInfo.direccion || '';
            document.getElementById('provincia').value = savedInfo.provincia || '';
            document.getElementById('ciudad').value = savedInfo.ciudad || '';
            document.getElementById('whatsapp').value = savedInfo.whatsapp || '';
            document.getElementById('email').value = savedInfo.email || '';
            console.log('✅ 已加载保存的客户信息');
        } else {
            // 清空表单
            form.reset();
        }
    }
    
    // CHANGE: 根据模式更新标题和按钮文本
    if (isEditMode) {
        if (modalTitle) {
            modalTitle.textContent = '✏️ Editar Datos del Cliente';
        }
        if (modalDescription) {
            modalDescription.textContent = 'Modifique sus datos personales. Los cambios se guardarán automáticamente.';
        }
        if (submitBtn) {
            submitBtn.textContent = 'Guardar Cambios';
        }
    } else {
        if (modalTitle) {
            modalTitle.textContent = '📋 Datos para el pedido';
        }
        if (modalDescription) {
            modalDescription.textContent = 'Por favor, complete los siguientes datos para realizar su pedido:';
        }
        if (submitBtn) {
            submitBtn.textContent = 'Confirmar Pedido';
        }
    }
    
    // 显示模态框
    modal.classList.remove('hidden');
    
    // 聚焦第一个输入框
    setTimeout(() => {
        const firstInput = document.getElementById('cedula');
        if (firstInput) {
            firstInput.focus();
        }
    }, 100);
}

// 隐藏客户信息表单
function hideCustomerInfoModal() {
    const modal = document.getElementById('customerInfoModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

// CHANGE: 提交订单（带客户信息）或仅保存信息（编辑模式）或重新提交订单
async function submitOrderWithCustomerInfo() {
    const modal = document.getElementById('customerInfoModal');
    const isEditMode = modal && modal.dataset.editMode === 'true';
    const isResubmitMode = modal && modal.dataset.editMode === 'resubmit';
    
    const form = document.getElementById('customerInfoForm');
    if (!form) {
        showToast('Error: formulario no encontrado', 'error');
        return;
    }
    
    // 验证表单
    if (!form.checkValidity()) {
        form.reportValidity();
        return;
    }
    
    // 收集客户信息
    const customerInfo = {
        cedula: document.getElementById('cedula').value.trim(),
        nombres: document.getElementById('nombres').value.trim(),
        direccion: document.getElementById('direccion').value.trim(),
        provincia: document.getElementById('provincia').value.trim(),
        ciudad: document.getElementById('ciudad').value.trim(),
        whatsapp: document.getElementById('whatsapp').value.trim(),
        email: document.getElementById('email').value.trim() || ''
    };
    
    // 验证必填字段
    if (!customerInfo.cedula || !customerInfo.nombres || !customerInfo.direccion || 
        !customerInfo.provincia || !customerInfo.ciudad || !customerInfo.whatsapp) {
        showToast('Por favor, complete todos los campos obligatorios', 'error');
        return;
    }
    
    // CHANGE: 保存客户信息到localStorage
    saveCustomerInfo(customerInfo);
    
    // CHANGE: 如果是编辑模式，只保存信息不提交订单
    if (isEditMode) {
        showToast('✅ Datos guardados correctamente', 'success');
        hideCustomerInfoModal();
        // CHANGE: 刷新转账信息页面以显示更新的客户信息
        if (document.getElementById('paymentSection') && !document.getElementById('paymentSection').classList.contains('hidden')) {
            fetchBankInfo();
        }
        return;
    }
    
    // CHANGE: 如果是重新提交模式，恢复购物车并重新提交订单
    if (isResubmitMode) {
        hideCustomerInfoModal();
        
        // 检查是否有保存的购物车状态
        if (!AppState.lastOrderCart || AppState.lastOrderCart.length === 0) {
            showToast('❌ No se puede reenviar el pedido: el carrito anterior no está disponible', 'error');
            return;
        }
        
        // 恢复购物车状态
        AppState.cart = JSON.parse(JSON.stringify(AppState.lastOrderCart));
        
        // 显示加载提示
        showToast('Reenviando pedido con los nuevos datos...', 'info');
        
        // 计算订单摘要
        const subtotal = AppState.cart.reduce((sum, item) => {
            const product = AppState.products.find(p => String(p.id) === String(item.product_id));
            const unitPrice = calculatePriceByQuantity(product || item, item.quantity);
            return sum + (unitPrice * item.quantity);
        }, 0);
        const shipping = CONFIG.SHIPPING_COST;
        const total = subtotal + shipping;
        
        try {
            // CHANGE: 发送 CARRITO 计算的小计/总计，保证 PEDIDOS 与 CARRITO 一致
            const result = await apiRequest('/checkout', {
                method: 'POST',
                body: JSON.stringify({
                    customer_info: customerInfo,
                    subtotal: subtotal,
                    total: total
                })
            });
            
            if (result.success) {
                showToast(`¡Pedido reenviado! Nuevo número de pedido: ${result.data.order_id}`, 'success');
                await fetchCart();
                // 更新订单信息
                AppState.lastOrderId = result.data.order_id;
                AppState.lastOrderSummary = {
                    subtotal: subtotal,
                    shipping: shipping,
                    total: total
                };
                // 更新购物车状态
                AppState.lastOrderCart = JSON.parse(JSON.stringify(AppState.cart));
                // 刷新转账信息页面
                fetchBankInfo();
            } else {
                showToast('Error al reenviar el pedido', 'error');
            }
        } catch (error) {
            console.error('重新提交订单失败:', error);
            showToast('Error al reenviar el pedido', 'error');
        }
        return;
    }
    
    // 隐藏模态框
    hideCustomerInfoModal();
    
    // CHANGE: 在提交订单前计算订单摘要（避免购物车被清空后无法计算）
    const subtotal = AppState.cart.reduce((sum, item) => {
        const product = AppState.products.find(p => String(p.id) === String(item.product_id));
        const unitPrice = calculatePriceByQuantity(product || item, item.quantity);
        return sum + (unitPrice * item.quantity);
    }, 0);
    const shipping = CONFIG.SHIPPING_COST;
    const total = subtotal + shipping;
    
    // 显示加载提示
    showToast('Procesando pedido...', 'info');
    
    try {
        // CHANGE: 发送 CARRITO 计算的小计/总计，保证 PEDIDOS 与 CARRITO 一致
        const result = await apiRequest('/checkout', {
            method: 'POST',
            body: JSON.stringify({
                customer_info: customerInfo,
                subtotal: subtotal,
                total: total
            })
        });
        
        if (result.success) {
            showToast(`¡Pedido realizado! Número de pedido: ${result.data.order_id}`, 'success');
            await fetchCart();
            // CHANGE: 保存订单ID、订单摘要和购物车状态，用于显示转账信息和重新提交
            AppState.lastOrderId = result.data.order_id;
            AppState.lastOrderSummary = {
                subtotal: subtotal,
                shipping: shipping,
                total: total
            };
            // CHANGE: 保存购物车状态，用于重新提交订单
            AppState.lastOrderCart = JSON.parse(JSON.stringify(AppState.cart));
            // 显示转账信息视图
            switchView('payment');
        }
    } catch (error) {
        console.error('提交订单失败:', error);
        showToast('Error al realizar el pedido', 'error');
    }
}

// 提交订单（原函数，现在显示客户信息表单）
async function checkout() {
    if (AppState.cart.length === 0) {
        showToast('El carrito está vacío', 'error');
        return;
    }
    showCustomerInfoModal();
}

// CHANGE: 免登录 - 用 session_id 获取订单列表
async function fetchOrders() {
    try {
        const result = await apiRequest('/orders', { silent: true });
        if (result.success) {
            AppState.orders = result.data || [];
            renderOrders(AppState.orders);
        } else {
            showToast('Error al cargar pedidos', 'error');
        }
    } catch (error) {
        var msg = String((error && error.message) || '');
        if (msg.indexOf('404') !== -1) {
            console.warn('⚠️ /api/orders 不可用（404），按空订单继续');
            AppState.orders = [];
            renderOrders([]);
            return;
        }
        console.error('获取订单列表失败:', error);
        showToast('Error al cargar pedidos', 'error');
    }
}

// 渲染订单列表
function renderOrders(orders) {
    const ordersList = document.getElementById('ordersList');
    
    if (orders.length === 0) {
        ordersList.innerHTML = `
            <div style="text-align: center; padding: 3rem 1rem; color: var(--text-light);">
                <div style="font-size: 4rem; margin-bottom: 1rem;">📋</div>
                <h3 style="font-size: 1.2rem; margin-bottom: 0.5rem;">No hay pedidos</h3>
                <p>Comience a comprar para ver sus pedidos aquí</p>
            </div>
        `;
        return;
    }
    
    ordersList.innerHTML = orders.map(order => {
        const date = new Date(order.created_at);
        const formattedDate = date.toLocaleDateString('es-ES', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
        
        const statusText = {
            'pending': '⏳ Pendiente',
            'confirmed': '✅ Confirmado',
            'processing': '🔄 Procesando',
            'shipped': '🚚 Enviado',
            'completed': '✅ Completado',
            'cancelled': '❌ Cancelado'
        }[order.status] || order.status;
        
        // 转义 order.id 以防止 XSS
        const safeOrderId = String(order.id).replace(/'/g, "\\'").replace(/"/g, '&quot;');
        
        // CHANGE: API已经返回包含运费的正确总价，直接使用
        const displayTotal = order.total_amount;
        
        return `
            <div class="order-card">
                <div class="order-card-content" onclick="viewOrderDetail('${safeOrderId}')">
                    <div class="order-header">
                        <div class="order-id">Pedido: ${order.id}</div>
                        <div class="order-status">${statusText}</div>
                    </div>
                    <div class="order-info">
                        <div class="order-total">Total: $${displayTotal.toFixed(2)}</div>
                        <div class="order-date">${formattedDate}</div>
                    </div>
                </div>
                ${order.status === 'pending' ? `
                    <div class="order-actions-bar">
                        <button class="btn btn-secondary btn-edit-order" onclick="event.stopPropagation(); editOrder('${safeOrderId}')" title="Editar pedido">
                            ✏️ Editar Pedido
                        </button>
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

// 编辑订单 - 将订单商品添加到购物车
async function editOrder(orderId) {
    try {
        const result = await apiRequest(`/orders/${orderId}`);
        if (!result.success) {
            showToast('Error al cargar el pedido', 'error');
            return;
        }
        const order = result.data;
        if (order.status !== 'pending') {
            showToast('Solo se pueden editar pedidos pendientes', 'error');
            return;
        }
        
        // CHANGE: 用户点击 EDITAR PEDIDO 已表示确认，不再弹出 confirm 避免卡顿（浏览器安全机制）
        // 清空当前购物车（静默模式，不显示确认对话框）
        await clearCart(true);
        
        // 将订单中的商品添加到购物车
        let addedCount = 0;
        let failedCount = 0;
        
        for (const item of order.items) {
            try {
                // 直接调用 API，不显示 toast
                const body = { product_id: item.product_id, quantity: item.quantity };
                if (item.price != null && item.price > 0) body.price = item.price;
                const result = await apiRequest('/cart/add', {
                    method: 'POST',
                    body: JSON.stringify(body)
                });
                
                if (result.success) {
                    addedCount++;
                } else {
                    failedCount++;
                    console.error(`Error al agregar producto ${item.product_id}:`, result.error);
                }
            } catch (error) {
                failedCount++;
                console.error(`Error al agregar producto ${item.product_id}:`, error);
            }
        }
        
        // 更新购物车UI
        await fetchCart();
        
        if (addedCount > 0) {
            if (failedCount > 0) {
                showToast(`Pedido cargado: ${addedCount} producto(s) agregado(s), ${failedCount} fallido(s).`, 'success');
            } else {
                showToast(`Pedido cargado al carrito. ${addedCount} producto(s) agregado(s).`, 'success');
            }
            // 切换到购物车视图
            switchView('cart');
        } else {
            showToast('Error al cargar productos del pedido', 'error');
        }
    } catch (error) {
        console.error('编辑订单失败:', error);
        showToast('Error al editar el pedido', 'error');
    }
}

// 查看订单详情
async function viewOrderDetail(orderId) {
    try {
        const result = await apiRequest(`/orders/${orderId}`, { silent: true });
        if (result.success) {
            renderOrderDetail(result.data);
            switchView('order-detail');
        } else {
            showToast('Error al cargar el pedido', 'error');
        }
    } catch (error) {
        var msg = String((error && error.message) || '');
        if (msg.indexOf('404') !== -1) {
            showToast('Pedido no encontrado', 'warning');
            return;
        }
        console.error('获取订单详情失败:', error);
        showToast('Error al cargar el pedido', 'error');
    }
}

// 渲染订单详情
function renderOrderDetail(order) {
    const orderDetailContent = document.getElementById('orderDetailContent');
    
    const date = new Date(order.created_at);
    const formattedDate = date.toLocaleDateString('es-ES', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
    
    const statusText = {
        'pending': '⏳ Pendiente',
        'confirmed': '✅ Confirmado',
        'processing': '🔄 Procesando',
        'shipped': '🚚 Enviado',
        'completed': '✅ Completado',
        'cancelled': '❌ Cancelado'
    }[order.status] || order.status;
    
    const itemsHtml = order.items.map(item => `
        <div class="order-item">
            <div class="order-item-info">
                <div class="order-item-name">${item.name}</div>
                <div class="order-item-details">
                    <span>Cantidad: ${item.quantity}</span>
                    <span>Precio: $${item.price.toFixed(2)}</span>
                </div>
            </div>
            <div class="order-item-subtotal">$${(item.subtotal).toFixed(2)}</div>
        </div>
    `).join('');
    
    // CHANGE: 计算商品小计和运费
    const subtotal = order.items.reduce((sum, item) => sum + (item.subtotal || 0), 0);
    const shipping = CONFIG.SHIPPING_COST;
    // CHANGE: 总是使用 subtotal + shipping 作为总价，修复旧订单计算错误
    // 旧订单的 total_amount 可能不包含运费，所以直接计算正确的总价
    const total = subtotal + shipping;
    
    orderDetailContent.innerHTML = `
        <div class="order-detail-card">
            <div class="order-detail-header">
                <h3>Pedido: ${order.order_id}</h3>
                <div class="order-status-badge">${statusText}</div>
            </div>
            <div class="order-detail-date">Fecha: ${formattedDate}</div>
            <div class="order-items-list">
                <h4>Productos:</h4>
                ${itemsHtml}
            </div>
            <div class="order-detail-summary">
                <div class="summary-row">
                    <span>Subtotal:</span>
                    <span>$${subtotal.toFixed(2)}</span>
                </div>
                <div class="summary-row">
                    <span>Envío:</span>
                    <span style="font-weight: 500;">$${shipping.toFixed(2)}</span>
                </div>
                <div class="summary-row total">
                    <span>Total:</span>
                    <span>$${total.toFixed(2)}</span>
                </div>
            </div>
            ${order.status === 'pending' ? `
                <div class="order-actions">
                    <button class="btn btn-primary" onclick="viewPaymentInfo('${order.order_id}')">
                        Ver Información de Transferencia
                    </button>
                </div>
            ` : ''}
        </div>
    `;
}

// 获取转账信息
async function fetchBankInfo() {
    try {
        const result = await apiRequest('/payment/bank-info', { silent: true });
        if (result.success) {
            // CHANGE: 调试日志 - 确认Telegram链接
            console.log('📱 接收到的Telegram链接:', result.data.customer_service?.telegram);
            renderBankInfo(result.data);
        } else {
            showToast('Error al cargar información de transferencia', 'error');
        }
    } catch (error) {
        var msg = String((error && error.message) || '');
        if (msg.indexOf('404') !== -1) {
            console.warn('⚠️ /api/payment/bank-info 不可用（404），跳过显示');
            const paymentContent = document.getElementById('paymentContent');
            if (paymentContent) {
                paymentContent.innerHTML = '<div class="empty-state" style="text-align:center;padding:2.5rem;color:var(--text-light);">No hay información de transferencia disponible.</div>';
            }
            return;
        }
        console.error('获取转账信息失败:', error);
        showToast('Error al cargar información de transferencia', 'error');
    }
}

// 获取银行logo路径
function getBankLogoPath(bankName) {
    // 银行名称到logo文件的映射
    const bankLogoMap = {
        'Banco Pichincha': 'banco-pichincha.png',
        'Banco del Pacífico': 'banco-del-pacifico.png',
        'Banco Guayaquil': 'banco-guayaquil.png',
        'Produbanco (Grupo Promerica)': 'produbanco.png'
    };
    
    const logoFileName = bankLogoMap[bankName] || 'default-bank.png';
    return `assets/bank-logos/${logoFileName}`;
}

// 渲染转账信息
function renderBankInfo(bankInfo) {
    const paymentContent = document.getElementById('paymentContent');
    
    const banksHtml = bankInfo.banks.map(bank => {
        const logoPath = getBankLogoPath(bank.name);
        return `
        <div class="bank-card">
            <div class="bank-name">
                <img src="${logoPath}" alt="${bank.name}" class="bank-logo" onerror="this.style.display='none';">
            </div>
            <div class="bank-details">
                <div class="bank-detail-row">
                    <span class="bank-label">Tipo:</span>
                    <span class="bank-value">${bank.type}</span>
                </div>
                <div class="bank-detail-row">
                    <span class="bank-label">Número:</span>
                    <span class="bank-value">${bank.number}</span>
                </div>
                <div class="bank-detail-row">
                    <span class="bank-label">Nombre:</span>
                    <span class="bank-value">${bank.account_name}</span>
                </div>
                <div class="bank-detail-row">
                    <span class="bank-label">I.C.:</span>
                    <span class="bank-value">${bank.id_number}</span>
                </div>
            </div>
        </div>
    `;
    }).join('');
    
    // CHANGE: 加载客户信息用于显示
    const customerInfo = loadCustomerInfo();
    
    // CHANGE: 在转账信息页面顶部显示客户资料
    const customerInfoHtml = customerInfo ? `
        <div class="customer-info-card" style="background: white; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h3 style="margin-top: 0; margin-bottom: 1rem; color: var(--text-color, #333); font-size: 1.2rem; font-weight: 600;">👤 Datos del Cliente</h3>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Cédula/RUC:</span>
                <span style="font-weight: 500;">${customerInfo.cedula || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Nombres:</span>
                <span style="font-weight: 500;">${customerInfo.nombres || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Dirección:</span>
                <span style="font-weight: 500; text-align: right; max-width: 60%;">${customerInfo.direccion || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Provincia:</span>
                <span style="font-weight: 500;">${customerInfo.provincia || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">Ciudad:</span>
                <span style="font-weight: 500;">${customerInfo.ciudad || 'N/A'}</span>
            </div>
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666); font-weight: 500;">WhatsApp:</span>
                <span style="font-weight: 500;">${customerInfo.whatsapp || 'N/A'}</span>
            </div>
            ${customerInfo.email ? `
            <div class="customer-info-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0;">
                <span style="color: var(--text-color, #666); font-weight: 500;">E-Mail:</span>
                <span style="font-weight: 500;">${customerInfo.email}</span>
            </div>
            ` : ''}
        </div>
    ` : '';
    
    // CHANGE: 在转账信息页面顶部显示订单摘要（账单）
    const orderSummaryHtml = AppState.lastOrderSummary ? `
        <div class="order-summary-card" style="background: white; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h3 style="margin-top: 0; margin-bottom: 1rem; color: var(--text-color, #333); font-size: 1.2rem; font-weight: 600;">📋 Resumen del Pedido</h3>
            <div class="summary-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666);">Subtotal:</span>
                <span style="font-weight: 500;">$${AppState.lastOrderSummary.subtotal.toFixed(2)}</span>
            </div>
            <div class="summary-row" style="display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid #eee;">
                <span style="color: var(--text-color, #666);">Envío:</span>
                <span style="font-weight: 500;">$${AppState.lastOrderSummary.shipping.toFixed(2)}</span>
            </div>
            <div class="summary-row total" style="display: flex; justify-content: space-between; padding: 0.75rem 0; margin-top: 0.5rem; border-top: 2px solid var(--primary-color, #4CAF50);">
                <span style="font-size: 1.1rem; font-weight: 600; color: var(--text-color, #333);">Total:</span>
                <span style="font-size: 1.2rem; font-weight: 700; color: var(--primary-color, #4CAF50);">$${AppState.lastOrderSummary.total.toFixed(2)}</span>
            </div>
        </div>
    ` : '';
    
    paymentContent.innerHTML = `
        ${customerInfoHtml}
        ${orderSummaryHtml}
        <div class="payment-info-card">
            <div class="payment-message">
                <p>${bankInfo.message}</p>
            </div>
            <div class="banks-list">
                ${banksHtml}
            </div>
            <div class="payment-footer" style="display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-top: 1.5rem; flex-wrap: wrap;">
                ${AppState.lastOrderId ? `
                    <div class="order-reference" style="flex: 1; min-width: 200px;">
                        <p style="margin-bottom: 0.5rem;"><strong>Número de Pedido:</strong> ${AppState.lastOrderId}</p>
                        <p style="margin: 0 0 0.75rem 0; color: var(--text-light, #666); font-size: 0.9rem;">Por favor, incluya este número al realizar la transferencia.</p>
                        <button onclick="copyOrderNumber('${AppState.lastOrderId}')" class="btn btn-primary" style="width: 100%; max-width: 300px; padding: 0.6rem 1rem; font-size: 0.9rem; display: flex; align-items: center; justify-content: center; gap: 0.5rem;">
                            <span>📋</span>
                            <span>COPIAR NUMERO DE PEDIDO</span>
                        </button>
                    </div>
                ` : '<div style="flex: 1;"></div>'}
                <div class="customer-service" style="flex-shrink: 0;">
                    <h4 style="margin-bottom: 0.5rem; margin-top: 0;">Servicio al Cliente:</h4>
                    <div class="service-links" style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                        <a href="${bankInfo.customer_service.whatsapp}" target="_blank" class="btn btn-secondary" style="white-space: nowrap;">
                            <img src="assets/social-logos/whatsapp.png" alt="WhatsApp" class="social-logo" onerror="this.style.display='none'; this.nextSibling.style.display='inline';">
                            <span class="social-fallback" style="display: none;">📱</span> WhatsApp
                        </a>
                        <a href="${bankInfo.customer_service.telegram}" target="_blank" class="btn btn-secondary" style="white-space: nowrap;" onclick="console.log('🔗 Telegram链接:', '${bankInfo.customer_service.telegram}');">
                            <img src="assets/social-logos/telegram.png" alt="Telegram" class="social-logo" onerror="this.style.display='none'; this.nextSibling.style.display='inline';">
                            <span class="social-fallback" style="display: none;">💬</span> Telegram
                        </a>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// 查看转账信息（从订单详情）
function viewPaymentInfo(orderId) {
    AppState.lastOrderId = orderId;
    switchView('payment');
}

// 复制订单号到剪贴板
function copyOrderNumber(orderId) {
    if (!orderId) {
        console.error('❌ 订单号为空');
        return;
    }
    
    // 使用 Clipboard API 复制订单号
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(orderId).then(() => {
            // 显示成功提示
            showNotification('✅ N.º de pedido copiado: ' + orderId, 'success');
        }).catch(err => {
            console.error('❌ 复制失败:', err);
            // 回退方案：使用传统方法
            fallbackCopyToClipboard(orderId);
        });
    } else {
        // 回退方案：使用传统方法
        fallbackCopyToClipboard(orderId);
    }
}

// 回退复制方法（兼容旧浏览器）
function fallbackCopyToClipboard(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
        const successful = document.execCommand('copy');
        if (successful) {
            showNotification('✅ N.º de pedido copiado: ' + text, 'success');
        } else {
            showNotification('❌ No se pudo copiar. Copie el número manualmente.', 'error');
        }
    } catch (err) {
        console.error('❌ 复制失败:', err);
        showNotification('❌ No se pudo copiar. Copie el número manualmente.', 'error');
    } finally {
        document.body.removeChild(textArea);
    }
}

// 显示通知（如果不存在则创建简单的通知函数）
function showNotification(message, type = 'info') {
    // 检查是否已有通知系统
    if (typeof window.showToast === 'function') {
        window.showToast(message, type);
        return;
    }
    
    // 简单的通知实现
    const notification = document.createElement('div');
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 1rem 1.5rem;
        background: ${type === 'success' ? '#4CAF50' : type === 'error' ? '#f44336' : '#2196F3'};
        color: white;
        border-radius: 4px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        z-index: 10000;
        font-size: 0.9rem;
        max-width: 300px;
        animation: slideIn 0.3s ease-out;
    `;
    
    // 添加动画样式（如果不存在）
    if (!document.getElementById('notification-styles')) {
        const style = document.createElement('style');
        style.id = 'notification-styles';
        style.textContent = `
            @keyframes slideIn {
                from {
                    transform: translateX(100%);
                    opacity: 0;
                }
                to {
                    transform: translateX(0);
                    opacity: 1;
                }
            }
            @keyframes slideOut {
                from {
                    transform: translateX(0);
                    opacity: 1;
                }
                to {
                    transform: translateX(100%);
                    opacity: 0;
                }
            }
        `;
        document.head.appendChild(style);
    }
    
    document.body.appendChild(notification);
    
    // 3秒后自动移除
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 300);
    }, 3000);
}

// 导出函数到全局作用域
window.viewOrderDetail = viewOrderDetail;
window.viewPaymentInfo = viewPaymentInfo;
window.editOrder = editOrder;
window.copyOrderNumber = copyOrderNumber;

// ===== UI渲染函数 =====

// Renderizar lista de productos
// CHANGE: 网页展示用产品代码，去掉 ._AI / ._Al 等后缀，便于阅读
function displayProductCode(code) {
    if (code == null || code === '') return '';
    var s = String(code).trim();
    return s.replace(/\._A[Ii]\s*$/i, '').trim() || s;
}

function prefetchNextProductsPage() {
    // CHANGE: 分类筛选模式下禁止预取下一页，避免混入非当前分类数据
    if (AppState._catalogMode) return;
    if (AppState._productsPrefetchPromise || AppState._productsPrefetchBuffer || !AppState._productsHasMore) return;
    var supplier = AppState._lastProductsSupplier || (AppState.currentView === 'products' ? 'others' : 'Cristy');
    var offset = Number(AppState._productsNextOffset || AppState.products.length || 0);
    AppState._productsPrefetchPromise = fetchProductsPageBySupplier(supplier, offset, PAGE_SIZE, 45000)
        .then(function(result) {
            if (!(result && (result.success === true || Array.isArray(result.data)))) return;
            var incoming = Array.isArray(result.data) ? result.data.slice() : [];
            incoming = incoming.filter(function(p) { return !isPlaceholderProduct(p); });
            AppState._productsPrefetchBuffer = {
                supplier: supplier,
                offset: offset,
                incoming: incoming,
                nextOffset: Number(result.nextOffset || (offset + incoming.length)),
                hasMore: !!result.hasMore
            };
        })
        .catch(function(e) {
            console.error('❌ prefetchNextProductsPage failed', e);
        })
        .finally(function() {
            AppState._productsPrefetchPromise = null;
        });
}

async function loadMoreProductsPage(trigger) {
    if (AppState.currentView !== 'products' && AppState.currentView !== 'ultimo') return false;
    // CHANGE: 分类筛选结果为独立集合，不做无限加载
    if (AppState._catalogMode) return false;
    if (AppState._productsLoadingMore || AppState._productsLoading) return false;

    var totalLoaded = (AppState._lastRenderProductsToRender || []).length;
    if (!totalLoaded) return false;

    var uiPage = getUiPageSize();
    var visibleCount = Number(AppState.productsVisibleCount || uiPage);
    if (!Number.isFinite(visibleCount) || visibleCount < uiPage) visibleCount = uiPage;

    if (visibleCount < totalLoaded) {
        AppState._productsLoadingMore = true;
        AppState.productsVisibleCount = Math.min(totalLoaded, visibleCount + uiPage);
        renderProducts();
        setTimeout(function() { AppState._productsLoadingMore = false; }, 120);
        prefetchNextProductsPage();
        return true;
    }

    if (!AppState._productsHasMore) return false;
    AppState._productsLoadingMore = true;
    try {
        var supplier = AppState._lastProductsSupplier || (AppState.currentView === 'products' ? 'others' : 'Cristy');
        var incoming = [];

        if (AppState._productsPrefetchBuffer) {
            var buf = AppState._productsPrefetchBuffer;
            AppState._productsPrefetchBuffer = null;
            incoming = Array.isArray(buf.incoming) ? buf.incoming : [];
            AppState._productsNextOffset = Number(buf.nextOffset || (Number(buf.offset || 0) + incoming.length));
            AppState._productsHasMore = !!buf.hasMore;
        } else {
            var offset = Number(AppState._productsNextOffset || AppState.products.length || 0);
            var result = await fetchProductsPageBySupplier(supplier, offset, PAGE_SIZE, 45000);
            if (!(result && (result.success === true || Array.isArray(result.data)))) return false;
            incoming = Array.isArray(result.data) ? result.data.slice() : [];
            incoming = incoming.filter(function(p) { return !isPlaceholderProduct(p); });
            AppState._productsNextOffset = Number(result.nextOffset || (offset + incoming.length));
            AppState._productsHasMore = !!result.hasMore;
        }

        var merged = dedupeProductsByCode((AppState.products || []).concat(incoming));
        AppState.products = merged;
        AppState.productsVisibleCount = Math.min(merged.length, visibleCount + uiPage);
        writeProductsCache(supplier, merged);
        renderProducts();
        prefetchNextProductsPage();
        return true;
    } catch (e) {
        console.error('❌ loadMoreProductsPage fetch failed', e);
        return false;
    } finally {
        AppState._productsLoadingMore = false;
    }
}

function handleInfiniteScrollLoadMore() {
    // CHANGE: 搜索模式下禁止无限滚动触发 renderProducts，避免覆盖搜索结果
    var searchEl = document.getElementById('searchInput');
    var searching = !!(searchEl && String(searchEl.value || '').trim());
    if (searching || AppState._searchActive) return;

    var scrollTop = window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0;
    var viewport = window.innerHeight || document.documentElement.clientHeight || 0;
    var full = Math.max(document.documentElement.scrollHeight || 0, document.body.scrollHeight || 0);

    // 预取阈值：还没到底部时就提前请求下一页
    var prefetchNearBottom = (scrollTop + viewport) >= (full - 900);
    if (prefetchNearBottom) prefetchNextProductsPage();

    // 真正触发展示下一批
    var nearBottom = (scrollTop + viewport) >= (full - 260);
    if (!nearBottom) return;
    loadMoreProductsPage('scroll');
}

function bindInfiniteScroll() {
    if (AppState._infiniteScrollBound) return;
    AppState._infiniteScrollBound = true;
    var onScroll = function() { handleInfiniteScrollLoadMore(); };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('wheel', onScroll, { passive: true });
    window.addEventListener('touchmove', onScroll, { passive: true });
    document.addEventListener('scroll', onScroll, { passive: true });
}

function renderProductSkeletons(count) {
    var grid = document.getElementById('productsGrid');
    if (!grid) return;
    var n = Number(count || INITIAL_PAGE_SIZE);
    if (!Number.isFinite(n) || n < 6) n = 12;
    var html = '';
    for (var i = 0; i < n; i++) {
        html += '<div class="product-card product-skeleton-card">' +
            '<div class="product-image-wrapper"><div class="product-skeleton product-skeleton-image"></div></div>' +
            '<div class="product-info">' +
              '<div class="product-skeleton product-skeleton-line" style="width:45%;height:14px;margin-bottom:8px;"></div>' +
              '<div class="product-skeleton product-skeleton-line" style="width:85%;height:18px;margin-bottom:12px;"></div>' +
              '<div class="product-skeleton product-skeleton-line" style="width:55%;height:14px;margin-bottom:6px;"></div>' +
              '<div class="product-skeleton product-skeleton-line" style="width:38%;height:20px;margin-bottom:12px;"></div>' +
              '<div class="product-skeleton product-skeleton-line" style="width:100%;height:40px;border-radius:10px;"></div>' +
            '</div>' +
          '</div>';
    }
    grid.innerHTML = html;

    if (!document.getElementById('productSkeletonStyle')) {
        var st = document.createElement('style');
        st.id = 'productSkeletonStyle';
        st.textContent = '.product-skeleton{position:relative;overflow:hidden;background:#eef1f4;} .product-skeleton::after{content:"";position:absolute;inset:0;transform:translateX(-100%);background:linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,.75), rgba(255,255,255,0));animation:pwaSkelShimmer 1.2s infinite;} .product-skeleton-image{width:100%;height:180px;border-radius:10px;} .product-skeleton-line{border-radius:8px;} @keyframes pwaSkelShimmer{100%{transform:translateX(100%);}}';
        document.head.appendChild(st);
    }
}

// CHANGE: 商品卡片 HTML 生成（供分片渲染复用）
function buildProductCardHtml(product, hashSegment, placeholderSvg) {
    const p = product && typeof product === 'object' ? product : {};
    const safeProductId = String(p.id != null ? p.id : '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    const productCode = (p.product_code != null && p.product_code !== '') ? String(p.product_code) : safeProductId;
    const safeProductCode = productCode.replace(/'/g, "\\'").replace(/"/g, '&quot;');

    function normForMatch(s) {
        if (!s) return '';
        return String(s).trim().toLowerCase().replace(/\._al$/i, '._ai');
    }

    var needHighlight = hashSegment && (String(p.id) === hashSegment || String(productCode) === hashSegment || normForMatch(p.id) === normForMatch(hashSegment) || normForMatch(productCode) === normForMatch(hashSegment));
    var highlightClass = needHighlight ? ' product-card-highlight' : '';

    const _bulk = Number(p.bulk_price || p.precio_bulto || 0);
    const _wholesale = Number(p.wholesale_price || p.precio_mayor || 0);
    const _price = Number(p.price || p.precio_unidad || 0);
    const displayPrice = (_bulk > 0) ? _bulk : ((_wholesale > 0) ? _wholesale : _price);
    const priceLabel = (_bulk > 0) ? 'Precio Bulto' : ((_wholesale > 0) ? 'Precio Mayoreo' : '');

    const rawPath = p.image_path || '';
    const hasImage = rawPath && String(rawPath).trim() && !rawPath.includes('data:image');
    const imageSrc = hasImage ? _resolveImageSrc(rawPath, p) : (placeholderSvg || '');
    const safeImagePath = (rawPath || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    const safeImageSrc = (imageSrc || '').replace(/"/g, '&quot;').replace(/'/g, "\\'");

    if (window && window.localStorage && localStorage.getItem('pwaImageDebug') === '1') {
        console.log('[PWA-IMAGE-DBG]', {
            id: p.id,
            code: p.product_code || p.id || '',
            name: p.name || '',
            rawPath: rawPath,
            resolvedSrc: imageSrc,
            supplier: p.codigo_proveedor || p.supplier || ''
        });
    }

    return '<div class="product-card' + highlightClass + '" data-product-id="' + safeProductId + '" data-product-code="' + safeProductCode + '" data-image-path="' + (safeImagePath || '') + '" data-resolved-image-src="' + (safeImageSrc || '') + '">' +
        '<div class="product-image-wrapper">' +
            '<img src="' + safeImageSrc + '" alt="' + (p.name || '').replace(/"/g, '&quot;') + '" class="product-image" data-image-src="' + safeImageSrc + '" data-image-raw="' + (safeImagePath || '') + '" loading="lazy" referrerpolicy="no-referrer" onclick="showImageModal(\'' + safeImageSrc + '\')" onerror="handleImageError(this);">' +
        '</div>' +
        '<div class="product-info">' +
            '<div class="product-code">' + ((displayProductCode(p.product_code || p.id || '') || '').replace(/"/g, '&quot;')) + '</div>' +
            '<div class="product-name">' + ((p.name || p.product_code || p.id || '').replace(/"/g, '&quot;')) + '</div>' +
            '<div class="product-price">' +
                (priceLabel ? ('<div class="price-label">' + priceLabel + '</div>') : '<div class="price-label">Precio</div>') +
                '<div class="price-amount' + (displayPrice <= 0 ? ' price-consultar' : '') + '">' + (displayPrice > 0 ? ('$' + displayPrice.toFixed(2)) : 'Consultar precio') + '</div>' +
            '</div>' +
            '<div class="product-actions">' +
                '<button class="btn btn-primary add-to-cart-btn" data-product-id="' + safeProductId + '">Agregar al Carrito</button>' +
            '</div>' +
        '</div>' +
    '</div>';
}

// CHANGE: 分片 append，避免一次性 innerHTML 导致低配 Android 主线程卡顿
function renderProductCardsChunked(grid, visible, hashSegment, placeholderSvg, onDone) {
    if (!grid) {
        if (typeof onDone === 'function') onDone();
        return;
    }
    var jobId = ++AppState._renderChunkJobId;
    grid.innerHTML = '';

    var total = Array.isArray(visible) ? visible.length : 0;
    if (!total) {
        if (typeof onDone === 'function') onDone();
        return;
    }

    var chunkSize = getUiPageSize() <= 60 ? 10 : 16;
    var index = 0;

    function pump() {
        if (jobId !== AppState._renderChunkJobId) return;
        var frag = document.createDocumentFragment();
        var until = Math.min(index + chunkSize, total);
        for (; index < until; index++) {
            var wrapper = document.createElement('div');
            wrapper.innerHTML = buildProductCardHtml(visible[index], hashSegment, placeholderSvg);
            if (wrapper.firstElementChild) frag.appendChild(wrapper.firstElementChild);
        }
        grid.appendChild(frag);

        if (index < total) {
            requestAnimationFrame(pump);
        } else if (typeof onDone === 'function') {
            onDone();
        }
    }

    requestAnimationFrame(pump);
}

function pruneBrokenProductCards(grid) {
    if (!grid) return 0;
    var cards = Array.from(grid.querySelectorAll('.product-card'));
    var removed = 0;
    cards.forEach(function(card) {
        var img = card.querySelector('img.product-image');
        if (!img) {
            card.remove();
            removed++;
            return;
        }
        var src = String(img.currentSrc || img.src || '').trim();
        var isPlaceholder = src.indexOf('data:image/svg+xml') === 0 || /sin imagen|imagen no disponible/i.test(img.alt || '');
        var isBroken = !!img.complete && (!img.naturalWidth || !img.naturalHeight);
        if (isPlaceholder || isBroken) {
            card.remove();
            removed++;
        }
    });
    return removed;
}

function renderProducts() {
    const grid = document.getElementById('productsGrid');
    
    if (!grid) {
        console.error('❌ [renderProducts] 找不到 productsGrid 元素');
        return;
    }
    
    console.log(`🎨 [renderProducts] 开始渲染，产品数量: ${AppState.products.length}`);
    // CHANGE: 按 product_code（或 id）去重，同一产品只显示一张卡片，避免重复数据被渲染成多张卡片
    // 同时提前过滤已经确认坏掉的图片，避免坏文件商品继续渲染
    const productsToRender = dedupeProductsByCode(AppState.products, false).filter(function(p) {
        if (!(p && (p.id != null || p.name || (p.product_code && String(p.product_code).trim())))) return false;
        return hasUsableImagePath(p);
    });
    AppState._lastRenderProductsToRender = productsToRender;
    var uiPage = getUiPageSize();
    if (productsToRender.length === 0) {
        var statusWrapEmpty = document.getElementById('productsLoadStatusWrap');
        if (statusWrapEmpty) statusWrapEmpty.innerHTML = '';
        renderPagination(0, 1);
        // CHANGE: switchView(products) 会先清空列表再调 renderProducts，此时 fetchProducts 尚未写入数据，属正常加载中，勿报「无产品」
        if (AppState.products.length === 0 && AppState._productsLoading) {
            grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;text-align:center;padding:4rem 2rem;color:var(--text-light);">Cargando productos...<br><small>Espere un momento</small></div>';
            console.log('🎨 [renderProducts] 加载中，暂不显示空状态');
            return;
        }
        console.warn('⚠️ [renderProducts] 无产品，显示空状态');
        var err = AppState._lastProductsError;
        var is404 = err && err.message && String(err.message).indexOf('404') !== -1;
        var is502OrFetch = err && err.message && (String(err.message).indexOf('Failed to fetch') !== -1 || String(err.message).indexOf('espera') !== -1 || String(err.message).indexOf('CORS') !== -1);
        var hintHtml;
        if (is404) {
            hintHtml = '<p style="color: var(--text-light); font-size: 1rem; margin-top: 0.5rem;">La ruta /api no está disponible. Revise Cloudflare Functions y _routes/_redirects.</p>';
        } else if (is502OrFetch) {
            hintHtml = '<p style="color: var(--text-light); font-size: 1.1rem;">La API no responde por ahora. Revise el estado del Worker/Functions y vuelva a intentar.</p>';
        } else if (err && err.message) {
            hintHtml = '<p style="color: var(--text-light); font-size: 1.1rem;">' + (err.message || '').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</p>';
        } else {
            hintHtml = '<p style="color: var(--text-light); font-size: 1.1rem;">Pronto agregaremos nuevos productos</p>';
        }
        var retryBtn = '<button class="btn btn-primary" onclick="fetchProducts(\'Cristy\').then(function(){console.log(\'OK\');}).catch(function(e){console.error(e);})" style="margin-top: 1rem;">🔄 Reintentar</button>';
        grid.innerHTML = `
            <div class="empty-state" style="grid-column: 1 / -1; text-align: center; padding: 4rem 2rem;">
                <div style="font-size: 5rem; margin-bottom: 1.5rem; opacity: 0.6; animation: bounce 2s ease-in-out infinite;">📦</div>
                <h3 style="font-size: 1.5rem; color: var(--text-color); margin-bottom: 0.5rem; font-weight: 600;">No hay productos disponibles</h3>
                ${hintHtml}
                ${retryBtn}
            </div>
        `;
        return;
    }

    AppState._productsLoading = false;

    var placeholderSvg = typeof PRODUCT_PLACEHOLDER_SVG !== 'undefined' ? PRODUCT_PLACEHOLDER_SVG : '';
    var hashSegment = (function() {
        var h = (location && location.hash) ? location.hash.trim() : '';
        if (h.indexOf('#/product/') !== 0) return '';
        return h.replace('#/product/', '').replace(/^\/+|\/+$/g, '').trim();
    })();
    function normForMatch(s) {
        if (!s) return '';
        return String(s).trim().toLowerCase().replace(/\._al$/i, '._ai');
    }
    // CHANGE: #/product/xxx 直达时确保目标产品在首屏可见范围内，避免 fetchProducts 完成后重渲染把该产品挤出 slice 导致高亮“一闪而过”
    if (hashSegment) {
        var hashIndex = -1;
        for (var i = 0; i < productsToRender.length; i++) {
            var px = productsToRender[i];
            var pid = px && (px.id != null || px.product_code != null) ? px : null;
            if (!pid) continue;
            if (String(pid.id) === hashSegment || String(pid.product_code || '') === hashSegment || normForMatch(String(pid.id)) === normForMatch(hashSegment) || normForMatch(String(pid.product_code || '')) === normForMatch(hashSegment)) {
                hashIndex = i;
                break;
            }
        }
        if (hashIndex >= 0 && hashIndex >= AppState.productsVisibleCount) {
            AppState.productsVisibleCount = hashIndex + 1;
            AppState.currentPage = Math.max(1, Math.ceil(AppState.productsVisibleCount / uiPage));
        }
    }
    var totalPages = Math.max(1, Math.ceil(productsToRender.length / uiPage));
    if (!Number.isFinite(AppState.currentPage) || AppState.currentPage < 1) AppState.currentPage = 1;
    if (AppState.currentPage > totalPages) AppState.currentPage = totalPages;

    var startIndex = (AppState.currentPage - 1) * uiPage;
    var endIndex = Math.min(startIndex + uiPage, productsToRender.length);
    var visible = productsToRender.slice(startIndex, endIndex);

    var statusWrap = document.getElementById('productsLoadStatusWrap');
    if (statusWrap) {
        var loadingMoreText = AppState._productsLoadingMore ? ' · Cargando más…' : '';
        var modeText = (uiPage < PAGE_SIZE) ? ' · modo ligero' : '';
        statusWrap.innerHTML = '<div class="products-load-status" id="productsLoadStatus">Mostrando ' + endIndex + ' de ' + productsToRender.length + loadingMoreText + modeText + '</div>';
    }

    // CHANGE: 事件委托 - 在 productsGrid 上绑定一次，避免每张卡片单独 addEventListener（grid 已在函数开头声明）
    if (!grid._cartDelegateBound) {
        grid._cartDelegateBound = true;
        grid.addEventListener('click', function(e) {
            var btn = e.target.closest('.add-to-cart-btn');
            if (!btn || !grid.contains(btn)) return;
            e.preventDefault();
            e.stopPropagation();
            var productId = btn.getAttribute('data-product-id');
            if (productId) showQuantityModal(productId);
        });
    }

    // CHANGE: 卡片分片 append，降低一次性 innerHTML 大量节点造成的卡顿
    renderProductCardsChunked(grid, visible, hashSegment, placeholderSvg, function() {
        var cleanupAttempts = 0;
        function runCleanupUntilStable() {
            cleanupAttempts += 1;
            var removedBroken = pruneBrokenProductCards(grid);
            if (removedBroken > 0) {
                console.log('[IMG-CLEANUP] 已移除无图商品卡:', removedBroken, 'attempt:', cleanupAttempts);
            }
            if (cleanupAttempts < 5) {
                setTimeout(runCleanupUntilStable, cleanupAttempts === 1 ? 800 : 1200);
            }
        }
        setTimeout(runCleanupUntilStable, 500);

        renderPagination(totalPages, AppState.currentPage);

        // CHANGE: Telegram/WhatsApp 链接 #/product/2202._AI 或 #/product/18bf4405 直达：渲染后尝试滚动到该产品
        var anchorResult = applyProductHashAnchor();
        if (anchorResult && !anchorResult.applied && anchorResult.segment) {
            fetchSingleProductForHash(anchorResult.segment);
        }
    });
}

// CHANGE: 解析 location.hash 中的 #/product/<id|code>，滚动到对应产品卡片并高亮；未找到时返回 { applied: false, segment } 以便请求单产品（Telegram 展示码直达）
function _scheduleProductHighlightFade(card, segment) {
    if (!card) return;
    try {
        if (card.__highlightFadeTimer) {
            clearTimeout(card.__highlightFadeTimer);
            card.__highlightFadeTimer = null;
        }
        card.classList.remove('product-card-highlight-fadeout');
        card.classList.add('product-card-highlight');

        // CHANGE: 自动高亮约 6 秒后淡出，客户滚动时视觉更自然
        card.__highlightFadeTimer = setTimeout(function() {
            card.classList.add('product-card-highlight-fadeout');
            setTimeout(function() {
                card.classList.remove('product-card-highlight', 'product-card-highlight-fadeout');
                card.__highlightFadeTimer = null;
                // 仅在仍是当前 segment 时释放锁，避免旧定时器清掉新高亮
                if (AppState._hashAutoScrolledSegment === segment) {
                    AppState._hashAutoScrolledSegment = '';
                }
            }, 650);
        }, 6000);
    } catch (e) {
        // ignore highlight fade errors
    }
}

function applyProductHashAnchor() {
    var hash = (typeof location !== 'undefined' && location.hash) ? location.hash.trim() : '';
    if (!hash) return null;
    var segment = '';
    if (hash.indexOf('#/product/') === 0) {
        segment = hash.replace('#/product/', '').replace(/^\/+|\/+$/g, '').trim();
    } else if (hash.indexOf('#/pwa_cart/products/') === 0) {
        segment = hash.replace('#/pwa_cart/products/', '').replace(/^\/+|\/+$/g, '').trim();
    } else if (hash.indexOf('#/products/') === 0) {
        segment = hash.replace('#/products/', '').replace(/^\/+|\/+$/g, '').trim();
    }
    if (!segment) {
        // CHANGE: 离开产品 hash 时清除一次性自动滚动锁
        AppState._hashAutoScrolledSegment = '';
        return null;
    }
    function norm(s) {
        if (!s) return '';
        var t = s.trim().toLowerCase();
        return t.replace(/\._al$/i, '._ai');
    }

    var allProducts = Array.isArray(AppState._lastRenderProductsToRender) ? AppState._lastRenderProductsToRender : [];
    if (allProducts.length) {
        var fullIndex = -1;
        for (var ai = 0; ai < allProducts.length; ai++) {
            var ap = allProducts[ai] || {};
            var aid = String(ap.id || '').trim();
            var acode = String(ap.product_code || '').trim();
            if (aid === segment || acode === segment || norm(aid) === norm(segment) || norm(acode) === norm(segment)) {
                fullIndex = ai;
                break;
            }
        }
        if (fullIndex >= 0) {
            var targetPage = Math.floor(fullIndex / PAGE_SIZE) + 1;
            if (AppState.currentPage !== targetPage) {
                AppState.currentPage = targetPage;
                renderProducts();
                return { applied: false, segment: segment };
            }
        }
    }

    var grid = document.getElementById('productsGrid');
    if (!grid) return { applied: false, segment: segment };
    var cards = grid.querySelectorAll('.product-card[data-product-id], .product-card[data-product-code]');
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var id = (card.getAttribute('data-product-id') || '').trim();
        var code = (card.getAttribute('data-product-code') || '').trim();
        if (id === segment || code === segment || norm(id) === norm(segment) || norm(code) === norm(segment)) {
            _scheduleProductHighlightFade(card, segment);
            // CHANGE: 同一个 hash 产品只自动滚动一次，避免用户下滑时再次被拉回去
            if (AppState._hashAutoScrolledSegment !== segment) {
                AppState._hashAutoScrolledSegment = segment;
                var scrollCard = card;
                requestAnimationFrame(function() {
                    requestAnimationFrame(function() {
                        scrollCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    });
                });
            }
            return { applied: true, segment: segment };
        }
    }
    return { applied: false, segment: segment };
}

// CHANGE: hash 直达时请求单产品；若为供应商产品则切到 PRODUCTOS 页并合并列表；404 时尝试从当前列表按 id/product_code 匹配并滚动
async function fetchSingleProductForHash(segment) {
    if (!segment) return;
    try {
        var result = await apiRequest('/products/' + encodeURIComponent(segment));
        if (result && result.success && result.data) {
            var p = result.data;
            var prov = (p.codigo_proveedor || '').trim().toLowerCase();
            var isSupplier = prov && prov !== 'cristy';
            if (isSupplier) {
                AppState._hashProductForView = { product: p, segment: segment };
                switchView('products');
            } else {
                AppState._pendingHashProduct = p;
                var exists = AppState.products.some(function (px) { return String(px.id) === String(p.id); });
                if (!exists) {
                    AppState.products.unshift(p);
                    AppState.currentPage = 1;
                    renderProducts();
                }
                requestAnimationFrame(function() {
                    requestAnimationFrame(function() {
                        var r = applyProductHashAnchor();
                        if (!r || !r.applied) {
                            var grid = document.getElementById('productsGrid');
                            if (grid) {
                                var code = (p.product_code || p.id || segment).toString().trim();
                                var card = grid.querySelector('.product-card[data-product-id="' + String(p.id).replace(/"/g, '\\"') + '"]') || grid.querySelector('.product-card[data-product-code="' + code.replace(/"/g, '\\"') + '"]');
                                if (card) {
                                    _scheduleProductHighlightFade(card, String(segment || code || p.id || ''));
                                    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                }
                            }
                        }
                    });
                });
            }
            return;
        }
    } catch (e) {
        console.warn('[fetchSingleProductForHash] 无法加载产品:', segment, e);
    }
    // 404 或异常：若当前列表中有 id/product_code 与 segment 一致的产品，直接滚动到该卡片（ULTIMO/列表已有但云端单条未同步时可用）
    var found = (AppState.products || []).find(function (px) {
        return String(px.id) === String(segment) || String(px.product_code || '') === String(segment);
    });
    if (found) {
        var code = (found.product_code || found.id || segment).toString().trim();
        try {
            location.hash = '#/product/' + code;
        } catch (e) {}
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                var r = applyProductHashAnchor();
                if (r && r.applied) return;
                var grid = document.getElementById('productsGrid');
                if (!grid) return;
                var card = grid.querySelector('.product-card[data-product-id="' + (found.id || '').toString().replace(/"/g, '\\"') + '"]') || grid.querySelector('.product-card[data-product-code="' + code.replace(/"/g, '\\"') + '"]');
                if (card) {
                    _scheduleProductHighlightFade(card, String(segment || code || found.id || ''));
                    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        });
    } else if (typeof showToast === 'function') {
        showToast('El producto no existe o no está sincronizado en la nube. Compruebe el enlace o ejecute la sincronización.', 'warning');
    }
}

// 渲染分页按钮（上一页 | 页码 ... 最后一页 | 下一页）
function renderPagination(totalPages, currentPage) {
    var container = document.getElementById('productsPagination');
    if (!container) return;

    // CHANGE: 即使只有 1 页也显示「Anterior / Siguiente」按钮（禁用态），让用户始终看到翻页能力
    var safeTotalPages = Math.max(1, Number(totalPages) || 1);
    var safeCurrentPage = Number(currentPage) || 1;
    if (safeCurrentPage < 1) safeCurrentPage = 1;
    if (safeCurrentPage > safeTotalPages) safeCurrentPage = safeTotalPages;

    totalPages = safeTotalPages;
    currentPage = safeCurrentPage;

    container.style.display = 'flex';

    // CHANGE: 始终显示最后一页页码，避免客户误以为只有前几页
    // 规则：
    // - totalPages <= 10: 全部显示
    // - 靠前页：1..8 ... last
    // - 靠后页：1 ... (last-7)..last
    // - 中间页：1 ... (current-2..current+2) ... last
    var items = [];

    function pushPage(p) {
        if (p >= 1 && p <= totalPages) items.push({ type: 'page', value: p });
    }
    function pushDots() {
        if (!items.length || items[items.length - 1].type === 'dots') return;
        items.push({ type: 'dots' });
    }

    if (totalPages <= 10) {
        for (var p = 1; p <= totalPages; p++) pushPage(p);
    } else if (currentPage <= 5) {
        for (var p1 = 1; p1 <= 8; p1++) pushPage(p1);
        pushDots();
        pushPage(totalPages);
    } else if (currentPage >= totalPages - 4) {
        pushPage(1);
        pushDots();
        for (var p2 = totalPages - 7; p2 <= totalPages; p2++) pushPage(p2);
    } else {
        pushPage(1);
        pushDots();
        for (var p3 = currentPage - 2; p3 <= currentPage + 2; p3++) pushPage(p3);
        pushDots();
        pushPage(totalPages);
    }

    var prevDisabled = currentPage <= 1 ? ' disabled' : '';
    var nextDisabled = currentPage >= totalPages ? ' disabled' : '';
    var html = '';

    html += '<div class="pagination-shell">';
    html += '<button class="pagination-btn pagination-nav prev-btn' + prevDisabled + '" data-page="' + (currentPage - 1) + '" ' + (prevDisabled ? 'disabled' : '') + ' aria-label="Página anterior">‹ Anterior</button>';
    html += '<div class="pagination-pages">';

    items.forEach(function(item) {
        if (item.type === 'dots') {
            html += '<span class="pagination-ellipsis">...</span>';
            return;
        }
        var p = item.value;
        var active = p === currentPage ? ' active' : '';
        html += '<button class="pagination-btn page-btn' + active + '" data-page="' + p + '" aria-label="Ir a la página ' + p + '">' + p + '</button>';
    });

    html += '</div>';
    html += '<button class="pagination-btn pagination-nav next-btn' + nextDisabled + '" data-page="' + (currentPage + 1) + '" ' + (nextDisabled ? 'disabled' : '') + ' aria-label="Página siguiente">Siguiente ›</button>';
    html += '</div>';
    html += '<div class="pagination-meta">Página ' + currentPage + ' de ' + totalPages + '</div>';

    container.innerHTML = html;

    if (!container._paginationBound) {
        container._paginationBound = true;
        container.addEventListener('click', function(e) {
            var btn = e.target.closest('.pagination-btn');
            if (!btn || btn.disabled) return;
            var page = parseInt(btn.getAttribute('data-page'));
            if (!page || page < 1 || page === AppState.currentPage) return;
            AppState.currentPage = page;
            renderProducts();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }
}

// CHANGE: 进入购物车视图前，按 cart 中的 product_id 补全 AppState.products，避免列表因 products 未包含购物车商品而空白
async function ensureCartProductsInState() {
    if (!AppState.cart || AppState.cart.length === 0) return;
    const idsToLoad = [];
    for (const item of AppState.cart) {
        const pid = item.product_id;
        const p = AppState.products.find(px => String(px.id) === String(pid));
        const hasValidImage = p && p.image_path && String(p.image_path).trim() && !(p.image_path.includes && p.image_path.includes('data:image'));
        if (!p || !hasValidImage) {
            if (idsToLoad.indexOf(String(pid)) === -1) idsToLoad.push(String(pid));
        }
    }
    for (const productId of idsToLoad) {
        try {
            const result = await apiRequest('/products/' + encodeURIComponent(productId));
            if (result && result.success && result.data) {
                const existing = AppState.products.find(px => String(px.id) === String(result.data.id));
                if (!existing) {
                    AppState.products.push(result.data);
                }
            }
        } catch (e) {
            console.warn('⚠️ [ensureCartProductsInState] 拉取商品失败:', productId, e);
        }
    }
}

// Renderizar carrito
function renderCart() {
    const cartItems = document.getElementById('cartItems');
    
    if (AppState.cart.length === 0) {
        cartItems.innerHTML = `
            <div class="empty-cart">
                <div class="empty-cart-icon">🛒</div>
                <h3 style="font-size: 1.5rem; margin-bottom: 0.5rem; color: var(--text-color); font-weight: 600;">Tu carrito está vacío</h3>
                <p style="margin-bottom: 2rem; color: var(--text-light);">Agrega productos para comenzar a comprar</p>
                <button class="btn btn-primary" onclick="switchView('products')" style="position: relative; z-index: 1;">Ir a Comprar</button>
            </div>
        `;
        // CHANGE: 购物车为空时隐藏「继续购物」按钮（已有 Ir a Comprar）
        var wrap = document.querySelector('.cart-continue-shopping-wrap');
        if (wrap) wrap.style.display = 'none';
        // CHANGE: 购物车为空时也要更新 Subtotal/Total 归零，避免清空或删除所有商品后仍显示旧金额
        updateCartTotal();
        return;
    }
    
    // NOTE: 冇图不显示、不能缺图空着；仅渲染有 image_path 的项，图失败时 handleImageError 隐藏该行
    // CHANGE: 使用 String 比较，避免 API 返回的 product_id 为字符串而 products[].id 为数字导致匹配失败
    const cartWithImage = AppState.cart.filter(item => {
        const p = AppState.products.find(px => String(px.id) === String(item.product_id));
        return p && p.image_path && String(p.image_path).trim() && !p.image_path.includes('data:image');
    });
    // CHANGE: 若补全后仍无带图项，降级显示全部 cart 项（用占位图），避免“有数量、有小计但列表空白”
    const itemsToRender = cartWithImage.length > 0 ? cartWithImage : AppState.cart;
    const placeholderSvg = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect fill="#f0f0f0" width="100" height="100"/><text x="50" y="55" text-anchor="middle" fill="#999" font-size="12">Sin imagen</text></svg>');
    cartItems.innerHTML = itemsToRender.map(item => {
        const product = AppState.products.find(p => String(p.id) === String(item.product_id)) || {
            name: item.name || 'Producto desconocido',
            price: item.price || 0,
            image_path: ''
        };
        
        const safeProductId = String(item.product_id).replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const hasValidImg = product.image_path && String(product.image_path).trim() && !product.image_path.includes('data:image');
        const cartImgSrc = hasValidImg ? _resolveImageSrc(product.image_path, product) : placeholderSvg;
        const safeCartImgSrc = (cartImgSrc || '').replace(/"/g, '&quot;').replace(/'/g, "\\'");
        return `
            <div class="cart-item">
                <div class="cart-item-image-wrapper">
                    <img src="${safeCartImgSrc}" 
                         alt="${product.name}" 
                         class="cart-item-image"
                         referrerpolicy="no-referrer"
                         onclick="showImageModal('${safeCartImgSrc}')"
                         onerror="handleImageError(this);">
                </div>
                <div class="cart-item-info">
                    <div class="cart-item-name">${product.name}</div>
                    <div class="cart-item-price">$${calculatePriceByQuantity(product || item, item.quantity).toFixed(2)} × ${item.quantity}</div>
                </div>
                <div class="cart-item-controls">
                    <div class="quantity-control">
                        <button class="quantity-btn" onclick="updateQuantity('${safeProductId}', ${item.quantity - 1})">-</button>
                        <input class="quantity-input" type="text" inputmode="numeric" pattern="[0-9]*" value="${item.quantity}" data-product-id="${safeProductId}" data-prev="${item.quantity}" aria-label="Cantidad">
                        <button class="quantity-btn" onclick="updateQuantity('${safeProductId}', ${item.quantity + 1})">+</button>
                    </div>
                    <button class="remove-btn" onclick="removeFromCart('${safeProductId}')" style="position: relative; z-index: 20;" title="Eliminar producto del carrito">🗑️ ELIMINAR</button>
                </div>
            </div>
        `;
    }).join('');

    // CHANGE: 购物车数量支持键盘直接输入（输入时本地更新小计，300ms 后再发后端请求）
    cartItems.querySelectorAll('.quantity-input').forEach(function(input) {
        input.addEventListener('focus', function() {
            input.select();
        });

        var timer = null;

        function sanitizeAndClamp() {
            var raw = String(input.value || '');
            var digits = raw.replace(/\D+/g, '');
            if (digits !== raw) input.value = digits;
            if (!digits) return null;
            var next = parseInt(digits, 10);
            if (!Number.isFinite(next) || isNaN(next)) return null;
            if (next < 1) next = 1;
            if (next > 999) next = 999;
            input.value = String(next);
            return next;
        }

        function updateLocalCartQuantity(productId, next) {
            var item = (AppState.cart || []).find(function(ci) { return String(ci.product_id) === String(productId); });
            if (item) item.quantity = next;
            updateCartTotal();
            var row = input.closest('.cart-item');
            if (row) {
                var priceEl = row.querySelector('.cart-item-price');
                if (priceEl) {
                    var prod = AppState.products.find(function(p) { return String(p.id) === String(productId); });
                    var unitPrice = calculatePriceByQuantity(prod || item, next);
                    priceEl.textContent = '$' + unitPrice.toFixed(2) + ' × ' + next;
                }
            }
        }

        function scheduleCommit(next) {
            var productId = input.getAttribute('data-product-id');
            var prev = parseInt(input.getAttribute('data-prev') || '1', 10) || 1;

            if (next === null) return;
            if (next !== prev) {
                input.setAttribute('data-prev', String(next));
                updateLocalCartQuantity(productId, next);
            }

            if (timer) clearTimeout(timer);
            timer = setTimeout(function() {
                if (next !== prev) updateQuantity(productId, next);
            }, 300);
        }

        input.addEventListener('input', function() {
            var next = sanitizeAndClamp();
            scheduleCommit(next);
        });

        input.addEventListener('blur', function() {
            var productId = input.getAttribute('data-product-id');
            var prev = parseInt(input.getAttribute('data-prev') || '1', 10) || 1;
            var next = sanitizeAndClamp();
            if (next === null) {
                input.value = String(prev);
                return;
            }
            if (timer) { clearTimeout(timer); timer = null; }
            if (next !== prev) {
                input.setAttribute('data-prev', String(next));
                updateLocalCartQuantity(productId, next);
                updateQuantity(productId, next);
            }
        });

        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                input.blur();
            }
        });
    });

    // CHANGE: 购物车有商品时显示「继续购物」按钮
    var wrap = document.querySelector('.cart-continue-shopping-wrap');
    if (wrap) wrap.style.display = 'flex';
    
    // 更新总价
    updateCartTotal();
}

// 按数量取价：1-2 单价，3-11 批发价，12+ 批量价（无批量价则用批发价）
// 情况1 三价: 1-2 unidad, 3-11 mayor, 12+ bulto（无 bulto 用 mayor）
// 情况2 两价(unidad+bulto): 1-11 unidad, 12+ bulto
// 情况3 一价: 所有数量用该价
function calculatePriceByQuantity(product, quantity) {
    if (!product) return 0;
    const price = Number(product.price) || 0;
    const wholesalePrice = Number(product.wholesale_price) || 0;
    const bulkPrice = Number(product.bulk_price) || 0;
    const hasUnidad = price > 0;
    const hasMayor = wholesalePrice > 0;
    const hasBulto = bulkPrice > 0;
    const tierCount = (hasUnidad ? 1 : 0) + (hasMayor ? 1 : 0) + (hasBulto ? 1 : 0);
    if (tierCount === 0) return 0;
    if (tierCount === 1) return price || wholesalePrice || bulkPrice;
    const scenarioSkipMayor = tierCount === 2 && hasUnidad && hasBulto && !hasMayor;
    // 1-2 件必须用单价；price 为 API 约定的 precio_unidad，避免旧逻辑默认批发价
    if (quantity <= 2) return (price > 0 ? price : (wholesalePrice || bulkPrice));
    if (scenarioSkipMayor && quantity <= 11) return price;
    if (quantity <= 11) return wholesalePrice || bulkPrice || price;
    if (scenarioSkipMayor) return bulkPrice || price;
    return bulkPrice || wholesalePrice || price;
}

// Actualizar precio total del carrito
function updateCartTotal() {
    const subtotal = AppState.cart.reduce((sum, item) => {
        const product = AppState.products.find(p => String(p.id) === String(item.product_id));
        const unitPrice = calculatePriceByQuantity(product || item, item.quantity);
        return sum + (unitPrice * item.quantity);
    }, 0);
    
    const total = subtotal + CONFIG.SHIPPING_COST;
    
    document.getElementById('subtotal').textContent = `$${subtotal.toFixed(2)}`;
    document.getElementById('shipping').textContent = `$${CONFIG.SHIPPING_COST.toFixed(2)}`;
    document.getElementById('total').textContent = `$${total.toFixed(2)}`;
}

// Actualizar UI del carrito
function updateCartUI() {
    const cartCount = AppState.cart.reduce((sum, item) => sum + (item.quantity || 0), 0);
    
    console.log(`🛒 Actualizando UI del carrito: ${AppState.cart.length} artículos, cantidad total: ${cartCount}`);
    console.log('Contenido del carrito:', AppState.cart);
    
    document.getElementById('cartCount').textContent = cartCount;
    const bottomNavCount = document.getElementById('bottomNavCartCount');
    if (bottomNavCount) {
        bottomNavCount.textContent = cartCount;
        // Si la cantidad es 0, ocultar insignia
        if (cartCount === 0) {
            bottomNavCount.style.display = 'none';
        } else {
            bottomNavCount.style.display = 'flex';
        }
    }
    
    // Actualizar insignia del botón del carrito superior
    const topCartCount = document.getElementById('cartCount');
    if (topCartCount) {
        if (cartCount === 0) {
            topCartCount.style.display = 'none';
        } else {
            topCartCount.style.display = 'flex';
        }
    }
    
    if (AppState.currentView === 'cart') {
        renderCart();
    }
}

// Cambiar vista
// CHANGE: 支持 ultimo 视图（显示自家产品）和 products 视图（显示其他供应商产品）
function switchView(view) {
    if (view === 'categories') view = 'products';
    AppState.currentView = view;
    
    const productsSection = document.getElementById('productsSection');
    const cartSection = document.getElementById('cartSection');
    const ordersSection = document.getElementById('ordersSection');
    const orderDetailSection = document.getElementById('orderDetailSection');
    const paymentSection = document.getElementById('paymentSection');
    const navItems = document.querySelectorAll('.nav-item');
    
    // 隐藏所有视图
    productsSection.classList.add('hidden');
    cartSection.classList.add('hidden');
    ordersSection.classList.add('hidden');
    orderDetailSection.classList.add('hidden');
    paymentSection.classList.add('hidden');
    
    // 显示对应视图
    if (view === 'ultimo') {
        productsSection.classList.remove('hidden');
        // CHANGE: 缓存优先 - 若已有 Cristy 数据则先渲染，同时后台刷新以展示新上传产品
        if (AppState._lastProductsSupplier === 'Cristy' && AppState.products.length > 0) {
            renderProducts();
            fetchProducts('Cristy');
        } else {
            fetchProducts('Cristy');
        }
    } else if (view === 'products') {
        productsSection.classList.remove('hidden');
        if (AppState._hashProductForView && AppState._hashProductForView.product) {
            AppState.products = [AppState._hashProductForView.product];
            AppState.currentPage = 1;
            renderProducts();
            fetchProducts('others');
        } else if (AppState._lastProductsSupplier === 'others' && AppState.products.length > 0) {
            renderProducts();
            fetchProducts('others');
        } else {
            AppState._productsLoading = true;
            AppState.products = [];
            AppState.currentPage = 1;
            renderProducts();
            fetchProducts('others');
        }
    } else if (view === 'cart') {
        cartSection.classList.remove('hidden');
        // CHANGE: 进入 carrito 时再请求购物车，减少首页并发请求导致 429
        (async function () {
            try { await fetchCart(); } catch (e) { /* ignore */ }
            await ensureCartProductsInState();
            renderCart();
        })();
    } else if (view === 'orders') {
        ordersSection.classList.remove('hidden');
        // CHANGE: 缓存优先 - 若有订单数据则先渲染，再后台刷新
        if (AppState.orders && AppState.orders.length > 0) {
            renderOrders(AppState.orders);
        } else if (AppState.orders && AppState.orders.length === 0) {
            renderOrders([]);
        }
        fetchOrders();
    } else if (view === 'order-detail') {
        orderDetailSection.classList.remove('hidden');
    } else if (view === 'payment') {
        paymentSection.classList.remove('hidden');
        fetchBankInfo();
    }
    
    // 更新导航状态
    navItems.forEach(item => {
        if (item.dataset.view === view) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });
}

// Actualizar cantidad de producto（传单价以便后端照存，与页面一致）
function updateQuantity(productId, newQuantity) {
    if (newQuantity <= 0) {
        removeFromCart(productId);
    } else {
        const product = AppState.products.find(p => String(p.id) === String(productId));
        const unitPrice = product ? calculatePriceByQuantity(product, newQuantity) : null;
        updateCartItem(productId, newQuantity, unitPrice);
    }
}

// 显示提示消息。position: 'top' 时提示显示在顶部；duration 可选，默认 3000ms
function showToast(message, type = 'info', position, duration) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    var cls = 'toast show ' + type;
    if (position === 'top') cls += ' toast-top';
    toast.className = cls;

    var ms = (typeof duration === 'number' && duration > 0) ? duration : 3000;
    setTimeout(() => {
        toast.classList.remove('show');
    }, ms);
}

function buildTechDiagText(meta) {
    var m = meta || {};
    var endpoint = String(m.endpoint || 'unknown');
    var status = String(m.status != null ? m.status : 'n/a');
    var source = String(m.source || m.target || 'unknown');
    var attempts = String(m.attempts || '1');
    return 'endpoint=' + endpoint + ' | status=' + status + ' | source=' + source + ' | attempts=' + attempts;
}

function hideTechStatusBar() {
    var bar = document.getElementById('techStatusBar');
    if (!bar) return;
    bar.classList.add('hidden');
    bar.innerHTML = '';
}

function copyTechDiagnostic(meta) {
    var text = buildTechDiagText(meta);
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast('Diagnóstico copiado', 'success');
        }).catch(function() {
            showNotification('❌ No se pudo copiar diagnóstico', 'error');
        });
    } else {
        fallbackCopyToClipboard(text);
    }
}

function showTechStatusBar(meta) {
    var bar = document.getElementById('techStatusBar');
    if (!bar) return;
    var text = buildTechDiagText(meta);
    bar.innerHTML = '' +
        '<div class="tech-status-inner">' +
            '<span class="tech-status-title">Estado técnico:</span>' +
            '<code class="tech-status-code">' + text.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</code>' +
            '<button type="button" class="tech-status-copy-btn" id="copyTechStatusBtn">Copiar diagnóstico</button>' +
            '<button type="button" class="tech-status-close-btn" id="closeTechStatusBtn" aria-label="Cerrar">×</button>' +
        '</div>';
    bar.classList.remove('hidden');

    var copyBtn = document.getElementById('copyTechStatusBtn');
    if (copyBtn) {
        copyBtn.onclick = function() { copyTechDiagnostic(meta); };
    }
    var closeBtn = document.getElementById('closeTechStatusBtn');
    if (closeBtn) {
        closeBtn.onclick = function() { hideTechStatusBar(); };
    }
}

// CHANGE: 404/5xx 技术状态条 + 一键复制诊断信息（endpoint + status + source + attempts）
function setTechStatusError(meta) {
    try {
        var bar = document.getElementById('techStatusBar');
        var txt = document.getElementById('techStatusText');
        if (!bar || !txt || !meta) return;

        var status = Number(meta.status || 0);
        var endpoint = String(meta.endpoint || '');
        var source = String(meta.source || '-');
        var attempts = String(meta.attempts || '-');
        var target = String(meta.target || '-');
        var when = new Date().toLocaleString();

        txt.textContent = 'HTTP ' + status + ' · ' + endpoint + ' · source=' + source + ' · attempts=' + attempts;
        bar.classList.remove('hidden');

        if (typeof window !== 'undefined') {
            window.__techDiag = {
                endpoint: endpoint,
                status: status,
                source: source,
                attempts: attempts,
                target: target,
                method: String(meta.method || 'GET'),
                when: when
            };
        }
    } catch (_) {}
}

function clearTechStatus() {
    try {
        var bar = document.getElementById('techStatusBar');
        var txt = document.getElementById('techStatusText');
        if (bar) bar.classList.add('hidden');
        if (txt) txt.textContent = 'Sin errores recientes.';
    } catch (_) {}
}

async function copyTechDiagnosis() {
    try {
        var d = (typeof window !== 'undefined' && window.__techDiag) ? window.__techDiag : null;
        if (!d) {
            showToast('No hay diagnóstico disponible todavía', 'info');
            return;
        }
        var payload = [
            'endpoint=' + String(d.endpoint || ''),
            'status=' + String(d.status || ''),
            'source=' + String(d.source || ''),
            'attempts=' + String(d.attempts || ''),
            'target=' + String(d.target || ''),
            'method=' + String(d.method || ''),
            'when=' + String(d.when || '')
        ].join(' | ');

        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(payload);
        } else {
            var ta = document.createElement('textarea');
            ta.value = payload;
            ta.style.position = 'fixed';
            ta.style.left = '-99999px';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }
        showToast('Diagnóstico copiado', 'success');
    } catch (e) {
        showToast('No se pudo copiar el diagnóstico', 'error');
    }
}

// Buscar productos：先本地快速返回，再异步刷新云端结果（降低“7-10秒才出结果”的体感）
var _searchRequestSeq = 0;
var _searchResultCache = {};

function _buildSearchLocalPool() {
    var merged = [];
    if (Array.isArray(AppState.products) && AppState.products.length) {
        merged = merged.concat(AppState.products);
    }
    var c1 = readProductsCache('Cristy') || [];
    var c2 = readProductsCache('others') || [];
    if (c1.length) merged = merged.concat(c1);
    if (c2.length) merged = merged.concat(c2);
    return dedupeProductsByCode(merged).filter(function(p) { return hasUsableImagePath(p); });
}

function _filterSearchLocal(pool, q, maxCount) {
    var needle = String(q || '').toLowerCase();
    var out = [];
    for (var i = 0; i < pool.length; i++) {
        var p = pool[i] || {};
        var name = String(p.name || p.nombre_producto || '').toLowerCase();
        var code = String(p.product_code || p.codigo_producto || '').toLowerCase();
        var id = String(p.id || '').toLowerCase();
        var desc = String(p.description || '').toLowerCase();
        if (name.indexOf(needle) !== -1 || code.indexOf(needle) !== -1 || id.indexOf(needle) !== -1 || desc.indexOf(needle) !== -1) {
            out.push(p);
            if (out.length >= maxCount) break;
        }
    }
    return out;
}

function renderSearchResults(products, options) {
    var opts = options || {};
    var grid = document.getElementById('productsGrid');
    if (!grid) return;
    var list = Array.isArray(products) ? products : [];

    if (!list.length) {
        grid.innerHTML = '<div class="loading">' + (opts.emptyText || 'No se encontraron productos coincidentes') + '</div>';
        var statusWrapEmptySearch = document.getElementById('productsLoadStatusWrap');
        if (statusWrapEmptySearch) statusWrapEmptySearch.innerHTML = '';
        return;
    }

    var tipHtml = opts.tipText ?
        ('<div class="products-load-status" style="grid-column:1/-1;text-align:center;padding:.35rem 0 .7rem;color:var(--text-light);font-size:.85rem;">' + opts.tipText + '</div>') :
        '';

    grid.innerHTML = list.map(function(product) {
        var safeProductId = String(product.id).replace(/'/g, "\\'").replace(/"/g, '&quot;');
        var productCode = (product.product_code != null && product.product_code !== '') ? String(product.product_code).replace(/'/g, "\\'").replace(/"/g, '&quot;') : safeProductId;
        var safeImagePath = product.image_path ? product.image_path.replace(/'/g, "\\'").replace(/"/g, '&quot;') : '';
        var searchImgSrc = _resolveImageSrc(product.image_path, product);
        var safeSearchImgSrc = (searchImgSrc || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        var displayPrice = (product.bulk_price && product.bulk_price > 0) ? product.bulk_price : (product.wholesale_price && product.wholesale_price > 0 ? product.wholesale_price : (product.price || 0));
        var priceLabel = (product.bulk_price && product.bulk_price > 0) ? 'Precio Bulto' : (product.wholesale_price && product.wholesale_price > 0 ? 'Precio Mayoreo' : '');
        var priceText = displayPrice > 0 ? '$' + displayPrice.toFixed(2) : 'Consultar precio';
        var labelHtml = priceLabel ? '<div class="price-label">' + priceLabel + ':</div>' : '';
        return '<div class="product-card" data-product-id="' + safeProductId + '" data-product-code="' + productCode + '" data-image-path="' + (safeImagePath || '') + '">' +
            '<div class="product-image-wrapper">' +
            '<img src="' + searchImgSrc.replace(/"/g, '&quot;') + '" alt="' + (product.name || '').replace(/"/g, '&quot;') + '" class="product-image" data-image-src="' + safeSearchImgSrc + '" loading="lazy" referrerpolicy="no-referrer" onclick="showImageModal(\'' + safeSearchImgSrc + '\')" onerror="handleImageError(this);">' +
            '</div><div class="product-info">' +
            '<div class="product-code">' + (displayProductCode(product.product_code || product.id || '') || '').replace(/"/g, '&quot;') + '</div>' +
            '<div class="product-name">' + (product.name || '') + '</div>' +
            '<div class="product-price">' + labelHtml + '<div class="price-amount">' + priceText + '</div></div>' +
            '<div class="product-actions"><button class="btn btn-primary add-to-cart-btn" data-product-id="' + safeProductId + '">Agregar al Carrito</button></div>' +
            '</div></div>';
    }).join('') + tipHtml;

    var statusWrap = document.getElementById('productsLoadStatusWrap');
    if (statusWrap) {
        statusWrap.innerHTML = '<div class="products-load-status" id="productsLoadStatus">Resultados: ' + list.length + '</div>';
    }
    applyProductHashAnchor();
}

async function searchProducts(query) {
    renderCatalogCategoryTags();
    var q = (query || '').trim();
    AppState._searchActive = q.length > 0;
    var grid = document.getElementById('productsGrid');
    if (!grid) return;
    if (!q) {
        AppState._searchActive = false;
        renderProducts();
        renderCatalogCategoryTags();
        return;
    }

    var reqSeq = ++_searchRequestSeq;

    // 1) 先从本地（当前列表 + localStorage 缓存）快速返回，保证即时反馈
    var localPool = _buildSearchLocalPool();
    var quick = _filterSearchLocal(localPool, q, 120);
    if (quick.length) {
        renderSearchResults(quick, { tipText: 'Resultados rápidos locales · sincronizando resultados completos…' });
    } else {
        grid.innerHTML = '<div class="loading">Buscando...</div>';
    }

    // 2) 再请求云端完整结果，并覆盖快速结果
    var cacheKey = q.toLowerCase();
    if (_searchResultCache[cacheKey] && Array.isArray(_searchResultCache[cacheKey])) {
        renderSearchResults(_searchResultCache[cacheKey], { emptyText: 'No se encontraron productos coincidentes' });
        return;
    }

    var url = '/products?limit=500&search=' + encodeURIComponent(q);
    try {
        var result = await apiRequest(url);
        // 输入变化或被后续请求覆盖，直接丢弃旧响应
        if (reqSeq !== _searchRequestSeq) return;
        var currentInput = (document.getElementById('searchInput') && document.getElementById('searchInput').value) ? document.getElementById('searchInput').value.trim() : '';
        if (currentInput !== q) return;

        if (result && result.success && result.data && result.data.length > 0) {
            var beforeDedupe = result.data.length;
            var filtered = dedupeProductsByCode(result.data).filter(function(product) { return hasUsableImagePath(product); });
            if (beforeDedupe !== filtered.length) {
                console.log('🔍 [searchProducts] 去重/过滤无图: ' + beforeDedupe + ' → ' + filtered.length + ' 条');
            }
            _searchResultCache[cacheKey] = filtered;
            renderSearchResults(filtered, { emptyText: 'No se encontraron productos coincidentes' });
        } else {
            renderSearchResults([], { emptyText: 'No se encontraron productos coincidentes' });
        }
    } catch (err) {
        console.error('搜索请求失败:', err);
        // 若已有快速结果则保留，不用错误覆盖；没有时才显示错误
        grid = document.getElementById('productsGrid');
        if (grid && !quick.length) {
            grid.innerHTML = '<div class="loading">Error de búsqueda. Intente de nuevo.</div>';
        }
    }
}

// ===== 事件监听 =====

// CHANGE: 老旧设备兼容 - Lucide CDN 加载失败时用 emoji 回退
var _ICON_FALLBACK = { 'shopping-bag':'🛍','shopping-cart':'🛒','search':'🔍','log-out':'🚪','smartphone':'📱','pencil':'✏️','sparkles':'✨','package':'📦','clipboard-list':'📋','tags':'🏷️' };
function _fallbackIconsIfNeeded() {
    var els = document.querySelectorAll('[data-lucide]');
    if (els.length === 0) return;
    var hasSvg = els[0] && els[0].querySelector && els[0].querySelector('svg');
    if (hasSvg) return;
    if (typeof lucide !== 'undefined' && lucide.createIcons) { lucide.createIcons(); return; }
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var name = (el.getAttribute('data-lucide') || '').toLowerCase();
        var emoji = _ICON_FALLBACK[name] || '•';
        el.textContent = emoji;
        el.setAttribute('aria-hidden', 'true');
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', async () => {
    console.log('🚀 [INIT] 页面加载完成，开始初始化...');
    renderVersionBadge();
    // CHANGE: 初始化 Lucide 图标；老旧设备/慢网速时 CDN 可能失败，3 秒后回退 emoji
    if (typeof lucide !== 'undefined' && lucide.createIcons) {
        lucide.createIcons();
    }
    setTimeout(_fallbackIconsIfNeeded, 3000);
    console.log('   session_id:', getOrCreateSessionId().substring(0, 8) + '...');
    console.log('   API地址:', CONFIG.API_BASE_URL);
    
    // Inicializar modal de selección de cantidad
    initQuantityModal();
    // CHANGE: 绑定无限下拉加载（到页面底部自动翻下一页）
    bindInfiniteScroll();
    
    // CHANGE: 初始化图片大图模态框
    initImageModal();
    
    // CHANGE: 初始化客户信息表单模态框
    initCustomerInfoModal();
    
    // CHANGE: 添加到主屏幕按钮与模态框（登录旁，方便用户安装到桌面）
    initAddToHomeModal();
    var addToHomeBtn = document.getElementById('addToHomeBtn');
    if (addToHomeBtn) {
        addToHomeBtn.addEventListener('click', function() {
            if (deferredInstallPrompt) {
                deferredInstallPrompt.prompt();
                deferredInstallPrompt.userChoice.then(function(choice) {
                    if (choice.outcome === 'accepted') showToast('App añadida a la pantalla de inicio', 'success');
                    deferredInstallPrompt = null;
                });
            } else {
                showAddToHomeModal();
            }
        });
    }
    window.addEventListener('beforeinstallprompt', function(e) {
        e.preventDefault();
        deferredInstallPrompt = e;
    });
    // 已以「独立应用」方式打开时隐藏「添加到主屏幕」按钮
    var isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    if (addToHomeBtn && isStandalone) addToHomeBtn.style.display = 'none';
    
    // 检查关键函数是否存在
    console.log('🔍 检查关键函数:');
    console.log('   addToCart:', typeof addToCart);
    console.log('   fetchCart:', typeof fetchCart);
    console.log('   renderProducts:', typeof renderProducts);
    
    // CHANGE: 若当前为重置密码页 (#/reset?token=xxx)，不发起产品/购物车请求，避免出现 "Error de red" 掩盖重置表单
    var hash = (typeof location !== 'undefined' && location.hash) ? location.hash.trim() : '';
    var isResetPage = hash.indexOf('#/reset') === 0 && hash.indexOf('token=') !== -1;
    // CHANGE: 若 URL 有 ?q=xxx，跳过 fetchProducts，仅由下方 URL Search 执行 searchProducts，避免 renderProducts 覆盖搜索结果
    var urlParams = typeof window !== 'undefined' && window.location ? new URLSearchParams(window.location.search || '') : null;
    var hasUrlSearch = urlParams && (urlParams.get('q') || urlParams.get('search') || '').trim().length > 0;

    // CHANGE: 先拉产品（默认 ULTIMO = Cristy 目录），再注册 Service Worker（重置页跳过）
    console.log('📦 [INIT] Iniciando carga de productos...');
    console.log('📦 [INIT] session_id:', getOrCreateSessionId().substring(0, 8) + '...');
    console.log('📦 [INIT] CONFIG.API_BASE_URL:', CONFIG.API_BASE_URL);
    if (!isResetPage && !hasUrlSearch) {
        fetchProducts('Cristy').finally(function() {
            fetchCategories().catch(function() {});
        }).catch(error => {
            console.error('❌ [INIT] 加载产品失败:', error);
            console.error('❌ [INIT] 错误详情:', {
                message: error.message,
                stack: error.stack
            });
            const productsGrid = document.getElementById('productsGrid');
            if (productsGrid) {
                productsGrid.innerHTML = `
                    <div class="empty-state" style="grid-column: 1 / -1; text-align: center; padding: 4rem 2rem;">
                        <div style="font-size: 5rem; margin-bottom: 1.5rem; opacity: 0.6;">⚠️</div>
                        <h3 style="font-size: 1.5rem; color: var(--text-color); margin-bottom: 0.5rem; font-weight: 600;">No se pueden cargar los productos</h3>
                        <p style="color: var(--text-light); font-size: 1.1rem;">${error.message || 'Compruebe la conexión o intente más tarde'}</p>
                        <button class="btn btn-primary" onclick="fetchProducts().then(() => console.log('OK')).catch(e => console.error(e))" style="margin-top: 1rem;">🔄 Recargar</button>
                    </div>
                `;
            }
        });
    } else if (isResetPage) {
        console.log('📦 [INIT] Página de restablecer contraseña: no se cargan productos para evitar Error de red');
    } else if (hasUrlSearch) {
        console.log('📦 [INIT] URL con ?q= detectado: se ejecutará searchProducts en lugar de fetchProducts');
        fetchCategories().catch(function() {});
    }
    
    // CHANGE: 监听 hash 变化；从 Telegram 打开 #/product/xxx 时立即拉取单产品（不依赖列表先加载），确保能跳转并高亮
    function onHashChange() {
        var h = (location && location.hash) ? location.hash.trim() : '';
        if (h.indexOf('#/product/') !== 0 && h.indexOf('#/products/') !== 0 && h.indexOf('#/pwa_cart/products/') !== 0) return;
        var seg = h.replace(/#\/product\/|\#\/products\/|\#\/pwa_cart\/products\//, '').replace(/^\/+|\/+$/g, '').trim();
        if (!seg) return;
        requestAnimationFrame(function() {
            var r = applyProductHashAnchor();
            if (r && !r.applied && r.segment) fetchSingleProductForHash(r.segment);
        });
    }
    if (typeof window !== 'undefined') {
        window.addEventListener('hashchange', onHashChange);
        var initialHash = (location && location.hash) ? location.hash.trim() : '';
        if (initialHash.indexOf('#/product/') === 0 || initialHash.indexOf('#/products/') === 0 || initialHash.indexOf('#/pwa_cart/products/') === 0) {
            var seg = initialHash.replace(/#\/product\/|\#\/products\/|\#\/pwa_cart\/products\//, '').replace(/^\/+|\/+$/g, '').trim();
            if (seg) {
                // CHANGE: 直达时强制显示产品区（避免从其他视图返回时被隐藏）
                var ps = document.getElementById('productsSection');
                if (ps && ps.classList.contains('hidden')) {
                    ps.classList.remove('hidden');
                    if (typeof switchView === 'function') switchView('ultimo');
                }
                fetchSingleProductForHash(seg);
            }
            // CHANGE: 多次延迟重试 applyProductHashAnchor，应对 fetchProducts 与 fetchSingleProductForHash 竞态导致卡片晚渲染
            var hashRetryCount = 0;
            [200, 500, 1000, 2000, 3500].forEach(function(ms) {
                setTimeout(function() {
                    var r = applyProductHashAnchor();
                    if (r && !r.applied && r.segment && hashRetryCount === 0 && typeof fetchSingleProductForHash === 'function') {
                        hashRetryCount = 1;
                        fetchSingleProductForHash(r.segment);
                    }
                }, ms);
            });
        }
    }
    // CHANGE: 用户点击任意处时移除荧光高亮；排除图片放大（.product-image、#imageModal），避免点击查看大图时高亮消失
    document.addEventListener('click', function removeHighlightOnClick(e) {
        if (e.target.closest('.product-image') || e.target.closest('.product-image-wrapper') || e.target.closest('#imageModal')) return;
        var grid = document.getElementById('productsGrid');
        if (grid) {
            var highlighted = grid.querySelectorAll('.product-card.product-card-highlight');
            highlighted.forEach(function(c) { c.classList.remove('product-card-highlight'); });
        }
    }, { passive: true });

    // 注册 Service Worker（不阻塞产品加载）；路径与当前页同目录，部署时需确保 service-worker.js 与 index.html 同目录（如 /pwa_cart/ 下）
    // CHANGE: 添加自动更新逻辑 - 定期检查 + 页面可见时检查 + 新版本激活后自动刷新
    if ('serviceWorker' in navigator) {
        var swPath = (location.pathname || '').indexOf('/pwa_cart') !== -1
            ? (location.pathname.replace(/\/[^/]*$/, '') || '/pwa_cart') + '/service-worker.js'
            : './service-worker.js';
        navigator.serviceWorker.register(swPath, swPath.indexOf('/pwa_cart') !== -1 ? { scope: (location.pathname.replace(/\/[^/]*$/, '') || '/pwa_cart') + '/' } : undefined)
            .then(function(reg) {
                console.log('✅ Service Worker注册成功:', reg.scope);
                // 定期检查更新（每 60 秒），便于 debug 后用户无需手动刷新
                var checkUpdate = function() { try { reg.update(); } catch (e) {} };
                setInterval(checkUpdate, 60000);
                // 页面从后台切回前台时立即检查
                document.addEventListener('visibilitychange', function() {
                    if (document.visibilityState === 'visible') checkUpdate();
                });
                // 新 SW 激活后自动刷新以加载最新代码
                navigator.serviceWorker.addEventListener('controllerchange', function onControllerChange() {
                    navigator.serviceWorker.removeEventListener('controllerchange', onControllerChange);
                    location.reload();
                });
                // 若有等待中的新 SW（未自动 skipWaiting 时），主动触发激活
                if (reg.waiting) {
                    reg.waiting.postMessage({ type: 'SKIP_WAITING' });
                }
                reg.addEventListener('updatefound', function() {
                    var newWorker = reg.installing;
                    if (newWorker) {
                        newWorker.addEventListener('statechange', function() {
                            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                                newWorker.postMessage({ type: 'SKIP_WAITING' });
                            }
                        });
                    }
                });
            })
            .catch(function(err) {
                if (err && (err.message || '').indexOf('404') !== -1) {
                    console.warn('⚠️ Service Worker 未找到（请确认部署包含 service-worker.js 与 index 同目录）:', swPath);
                } else {
                    console.error('❌ Service Worker注册失败:', err);
                }
            });
    }
    
    // CHANGE: 首屏不自动拉购物车，避免与 products/hash 并发触发 429；进入 carrito 视图时再加载
    if (!isResetPage) {
        console.log('🛒 跳过首屏购物车请求（进入 carrito 时再加载）');
        AppState.cart = [];
        updateCartUI();
    }
    
    // 绑定事件
    const cartButton = document.getElementById('cartButton');
    if (cartButton) {
        cartButton.addEventListener('click', () => {
            console.log('🖱️ Botón del carrito clickeado');
            switchView('cart');
        });
    } else {
        console.error('❌ 找不到cartButton元素');
    }
    
    const backButton = document.getElementById('backButton');
    if (backButton) {
        backButton.addEventListener('click', () => {
            console.log('🖱️ Botón de regreso clickeado');
            switchView('products');
        });
    }
    
    const clearCartButton = document.getElementById('clearCartButton');
    if (clearCartButton) {
        clearCartButton.addEventListener('click', clearCart);
    }
    
    const checkoutButton = document.getElementById('checkoutButton');
    if (checkoutButton) {
        checkoutButton.addEventListener('click', checkout);
    }
    
    // CHANGE: 购物车页面编辑客户信息按钮
    const editCustomerInfoButton = document.getElementById('editCustomerInfoButton');
    if (editCustomerInfoButton) {
        editCustomerInfoButton.addEventListener('click', () => {
            console.log('✏️ 编辑客户信息按钮点击（购物车页面）');
            showCustomerInfoModal(true); // 传入true表示编辑模式
        });
    }
    
    // CHANGE: 订单页面编辑客户信息按钮
    const editCustomerInfoButtonOrders = document.getElementById('editCustomerInfoButtonOrders');
    if (editCustomerInfoButtonOrders) {
        editCustomerInfoButtonOrders.addEventListener('click', () => {
            console.log('✏️ 编辑客户信息按钮点击（订单页面）');
            showCustomerInfoModal(true); // 传入true表示编辑模式
        });
    }
    
    // CHANGE: 转账信息页面编辑客户信息按钮（修改后重新提交订单）
    const editCustomerInfoButtonPayment = document.getElementById('editCustomerInfoButtonPayment');
    if (editCustomerInfoButtonPayment) {
        editCustomerInfoButtonPayment.addEventListener('click', () => {
            console.log('✏️ 编辑客户信息按钮点击（转账信息页面）');
            showCustomerInfoModalForResubmit(); // 特殊模式：修改后重新提交订单
        });
    }
    
    // 订单列表返回按钮
    const ordersBackButton = document.getElementById('ordersBackButton');
    if (ordersBackButton) {
        ordersBackButton.addEventListener('click', () => {
            switchView('products');
        });
    }
    
    // 订单详情返回按钮
    const orderDetailBackButton = document.getElementById('orderDetailBackButton');
    if (orderDetailBackButton) {
        orderDetailBackButton.addEventListener('click', () => {
            switchView('orders');
        });
    }
    
    // 转账信息返回按钮
    const paymentBackButton = document.getElementById('paymentBackButton');
    if (paymentBackButton) {
        paymentBackButton.addEventListener('click', () => {
            // 如果是从订单详情来的，返回订单详情；否则返回订单列表
            if (AppState.lastOrderId) {
                viewOrderDetail(AppState.lastOrderId);
            } else {
                switchView('orders');
            }
        });
    }
    
    // CHANGE: 底部导航 - 事件委托 + 防抖。BUSCAR 独立处理，避免与其他分支干扰
    var bottomNav = document.querySelector('.bottom-nav');
    if (bottomNav) {
        var _lastNavAction = { view: null, ts: 0 };
        var DEBOUNCE_MS = 400;
        // BUSCAR 专用：直接跳转搜索并聚焦，独立分支避免与其他导航干扰
        function goToSearch(e) {
            var item = e.target.closest('.nav-item[data-view="search"]');
            if (!item || !bottomNav.contains(item)) return false;
            e.preventDefault();
            e.stopPropagation();
            var productsSection = document.getElementById('productsSection');
            var searchInp = document.getElementById('searchInput');
            if (!productsSection || !searchInp) return true;
            // 显示产品区（若被隐藏，如从 Carrito/Pedidos 切换）
            if (productsSection.classList.contains('hidden')) {
                switchView('products');
            }
            // 清空搜索框并恢复默认产品列表，方便用户直接输入关键词
            searchInp.value = '';
            if (typeof renderProducts === 'function') {
                _searchRequestSeq++;
                renderProducts();
            }
            // 双帧延迟聚焦，确保布局/渲染完成
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    searchInp.focus();
                    searchInp.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            });
            console.log('🔍 BUSCAR: 跳转搜索并聚焦');
            return true;
        }
        function handleNavAction(e) {
            // 优先处理 BUSCAR，独立分支
            if (goToSearch(e)) return;
            var item = e.target.closest('.nav-item');
            if (!item || !bottomNav.contains(item)) return;
            if (item.classList.contains('nav-link')) return;
            e.preventDefault();
            e.stopPropagation();
            var view = item.dataset.view;
            if (!view) return;
            var now = Date.now();
            if (_lastNavAction.view === view && now - _lastNavAction.ts < DEBOUNCE_MS) return;
            _lastNavAction = { view: view, ts: now };
            console.log('🖱️ Botón de navegación clickeado:', view);
            switchView(view);
        }
        bottomNav.addEventListener('click', handleNavAction, true);
        bottomNav.addEventListener('pointerup', handleNavAction, true);
        bottomNav.addEventListener('touchend', handleNavAction, { passive: false, capture: true });
        // CHANGE: 为所有底部导航按钮添加透明覆盖层，确保整块可点击且显示手指，避免 toast/其他元素遮挡
        bottomNav.querySelectorAll('.nav-item').forEach(function (btn) {
            if (!btn.querySelector('.nav-hit-overlay')) {
                var overlay = document.createElement('span');
                overlay.className = 'nav-hit-overlay';
                overlay.setAttribute('aria-hidden', 'true');
                overlay.style.cssText = 'position:absolute;inset:0;cursor:pointer;pointer-events:auto;z-index:1;';
                btn.style.position = 'relative';
                btn.insertBefore(overlay, btn.firstChild);
            }
        });
    }
    
    // 搜索功能：防抖 + 支持 Enter 触发，确保有反应
    var searchDebounceTimer = null;
    var searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', function(e) {
            var val = (e.target.value || '').trim();
            if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
            if (!val) {
                _searchRequestSeq++;
                renderProducts();
                return;
            }
            searchDebounceTimer = setTimeout(function() {
                searchDebounceTimer = null;
                console.log('🔍 搜索:', val);
                searchProducts(val);
            }, 180);
        });
        searchInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
                searchDebounceTimer = null;
                var val = (e.target.value || '').trim();
                if (val) { searchProducts(val); } else { renderProducts(); }
            }
        });
    }

    var catalogCategoryTags = document.getElementById('catalogCategoryTags');
    if (catalogCategoryTags) {
        catalogCategoryTags.addEventListener('click', async function(e) {
            var btn = e.target.closest('.catalog-category-tag');
            if (!btn || !catalogCategoryTags.contains(btn) || btn.classList.contains('catalog-category-tag--muted')) return;
            var cat = btn.getAttribute('data-category');
            if (cat === null) return;
            cat = cat || '';

            // CHANGE: 点击分类标签时退出搜索态并清空搜索关键词，避免行为不一致
            AppState._searchActive = false;
            var si = document.getElementById('searchInput');
            if (si) si.value = '';

            if (!cat) {
                await loadProductsByCatalogCategory('');
            } else {
                await loadProductsByCatalogCategory(cat);
            }
        });
    }

    // CHANGE: 支持从链接参数自动搜索（如 /pwa_cart/?q=carro），客户点击链接后可直达关键词结果
    // 有 ?q= 时已跳过 fetchProducts，此处立即执行搜索，避免显示 ULTIMO 再被覆盖
    if (!isResetPage && typeof window !== 'undefined' && window.location) {
        try {
            var params = new URLSearchParams(window.location.search || '');
            var initialQuery = (params.get('q') || params.get('search') || '').trim();
            if (initialQuery) {
                var productsSection = document.getElementById('productsSection');
                if (productsSection && productsSection.classList.contains('hidden') && typeof switchView === 'function') {
                    switchView('products');
                }
                if (searchInput) searchInput.value = initialQuery;
                var delay = hasUrlSearch ? 50 : 250;
                setTimeout(function() {
                    if (typeof searchProducts === 'function') searchProducts(initialQuery);
                }, delay);
                console.log('🔗 [URL Search] 自动应用关键词搜索:', initialQuery);
            }
        } catch (e) {
            console.warn('⚠️ [URL Search] 解析链接关键词失败:', e);
        }
    }
    
    // Usar delegación de eventos para manejar todos los botones de añadir al carrito (más confiable)
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('add-to-cart-btn') || e.target.closest('.add-to-cart-btn')) {
            const btn = e.target.classList.contains('add-to-cart-btn') ? e.target : e.target.closest('.add-to-cart-btn');
            const productId = btn.getAttribute('data-product-id');
            console.log('🖱️ [Delegación de eventos] Botón de añadir al carrito clickeado, ID del Producto:', productId);
            if (productId) {
                e.preventDefault();
                e.stopPropagation();
                // 显示数量选择模态框
                showQuantityModal(productId);
            }
        }
    });
    
    // CHANGE: 初始化认证相关功能
    initAuth();
    
    // CHANGE: hash 变化时（如点击链接）重新执行直达逻辑
    window.addEventListener('hashchange', function() {
        if (document.getElementById('productsSection') && !document.getElementById('productsSection').classList.contains('hidden')) {
            var r = applyProductHashAnchor();
            if (r && !r.applied && r.segment) fetchSingleProductForHash(r.segment);
        }
    });
    
    // CHANGE: 免登录模式 - 隐藏登录相关 UI
    setTimeout(() => {
        const loginBtn = document.getElementById('loginBtn');
        const userInfo = document.getElementById('userInfo');
        if (loginBtn) loginBtn.classList.add('hidden');
        if (userInfo) userInfo.classList.add('hidden');
    }, 500);
    
    console.log('✅ [INIT] Inicialización completada');
});

// CHANGE: 免登录模式 - 仅隐藏登录相关 UI
function initAuth() {
    updateUserUI();
}

function initAuthModal() {
    const modal = document.getElementById('authModal');
    const closeBtn = document.getElementById('authModalCloseBtn');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const forgotForm = document.getElementById('forgotForm');
    const resetForm = document.getElementById('resetForm');
    const switchToRegister = document.getElementById('switchToRegister');
    const switchToLogin = document.getElementById('switchToLogin');
    const switchToForgot = document.getElementById('switchToForgot');
    const switchToLoginFromForgot = document.getElementById('switchToLoginFromForgot');
    const switchToLoginFromReset = document.getElementById('switchToLoginFromReset');
    const loginFormElement = document.getElementById('loginFormElement');
    const registerFormElement = document.getElementById('registerFormElement');
    const forgotFormElement = document.getElementById('forgotFormElement');
    const resetFormElement = document.getElementById('resetFormElement');
    
    if (!modal) return;
    
    // 关闭按钮
    if (closeBtn) {
        closeBtn.addEventListener('click', hideAuthModal);
    }
    
    // 点击背景关闭
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            hideAuthModal();
        }
    });
    
    // ESC键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideAuthModal();
        }
    });
    
    // 切换表单
    if (switchToRegister) {
        switchToRegister.addEventListener('click', () => {
            showAuthModal('register');
        });
    }
    if (switchToLogin) {
        switchToLogin.addEventListener('click', () => {
            showAuthModal('login');
        });
    }
    if (switchToForgot) {
        switchToForgot.addEventListener('click', () => {
            showAuthModal('forgot');
        });
    }
    if (switchToLoginFromForgot) {
        switchToLoginFromForgot.addEventListener('click', () => {
            showAuthModal('login');
        });
    }
    if (switchToLoginFromReset) {
        switchToLoginFromReset.addEventListener('click', () => {
            showAuthModal('login');
        });
    }
    
    // 登录表单提交
    if (loginFormElement) {
        loginFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = (document.getElementById('loginEmail').value || '').trim();
            const password = (document.getElementById('loginPassword').value || '').trim();
            await handleLogin(email, password);
        });
    }
    
    // 注册表单提交
    if (registerFormElement) {
        registerFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const name = document.getElementById('registerName').value;
            const email = document.getElementById('registerEmail').value;
            const password = document.getElementById('registerPassword').value;
            const passwordConfirm = document.getElementById('registerPasswordConfirm').value;
            
            if (password !== passwordConfirm) {
                showToast('Las contraseñas no coinciden', 'error');
                return;
            }
            
            await handleRegister(name, email, password);
        });
    }
    
    // CHANGE: 忘记密码表单提交
    if (forgotFormElement) {
        forgotFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('forgotEmail').value;
            await handleForgotPassword(email);
        });
    }
    
    // CHANGE: 重置密码表单提交
    if (resetFormElement) {
        resetFormElement.addEventListener('submit', async (e) => {
            e.preventDefault();
            const password = document.getElementById('resetPassword').value;
            const passwordConfirm = document.getElementById('resetPasswordConfirm').value;
            if (password !== passwordConfirm) {
                showToast('Las contraseñas no coinciden', 'error');
                return;
            }
            // CHANGE: token 可从 URL (#/reset?token=) 或隐藏域取（忘记密码直接弹重置框时无 URL token）
            var tokenEl = document.getElementById('resetTokenHidden');
            const token = (_getResetTokenFromUrl() || (tokenEl && tokenEl.value ? tokenEl.value.trim() : null)) || null;
            if (!token) {
                showToast('Enlace inválido o expirado', 'error');
                return;
            }
            await handleResetPassword(token, password);
        });
    }
    
}

function showAuthModal(mode = 'login', token) {
    const modal = document.getElementById('authModal');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const forgotForm = document.getElementById('forgotForm');
    const resetForm = document.getElementById('resetForm');
    const authModalTitle = document.getElementById('authModalTitle');
    const forgotSuccess = document.getElementById('forgotSuccess');
    
    if (!modal) return;
    
    var allForms = [loginForm, registerForm, forgotForm, resetForm];
    allForms.forEach(function(f) { if (f) f.classList.add('hidden'); });
    
    if (mode === 'login') {
        if (loginForm) loginForm.classList.remove('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Iniciar Sesión';
        // CHANGE: 打开登录弹窗时清除之前的错误提示
        var loginErrorEl = document.getElementById('loginError');
        if (loginErrorEl) { loginErrorEl.textContent = ''; loginErrorEl.classList.add('hidden'); }
    } else if (mode === 'register') {
        if (registerForm) registerForm.classList.remove('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Registrarse';
    } else if (mode === 'forgot') {
        if (forgotForm) forgotForm.classList.remove('hidden');
        if (forgotSuccess) forgotSuccess.classList.add('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Recuperar contraseña';
    } else if (mode === 'reset' && token) {
        if (resetForm) resetForm.classList.remove('hidden');
        if (authModalTitle) authModalTitle.textContent = 'Restablecer contraseña';
        var resetTokenInput = document.getElementById('resetTokenHidden');
        if (!resetTokenInput) {
            var inp = document.createElement('input');
            inp.type = 'hidden';
            inp.id = 'resetTokenHidden';
            inp.value = token;
            var form = document.getElementById('resetFormElement');
            if (form) form.appendChild(inp);
        } else {
            resetTokenInput.value = token;
        }
    }
    
    modal.classList.remove('hidden');
}

function hideAuthModal() {
    const modal = document.getElementById('authModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

// CHANGE: 忘记密码 API 返回的 message 统一为西班牙语（避免中文提示）
function getForgotMessage(apiMessage) {
    if (!apiMessage || typeof apiMessage !== 'string') return 'Si el correo está registrado, recibirás el enlace en esta página.';
    var s = apiMessage;
    if (/[\u4e00-\u9fff]/.test(s)) {
        if (s.indexOf('邮箱') !== -1 && (s.indexOf('注册') !== -1 || s.indexOf('收到') !== -1 || s.indexOf('链接') !== -1)) return 'Si el correo está registrado, recibirás el enlace en esta página.';
        return 'Si el correo está registrado, recibirás el enlace en esta página.';
    }
    return s;
}

// CHANGE: 将 API 返回的中文等错误映射为西班牙语，供登录界面显示
function getLoginErrorMessage(apiErrorText) {
    if (!apiErrorText || typeof apiErrorText !== 'string') return 'Error al iniciar sesión';
    var s = apiErrorText;
    if (s.indexOf('邮箱或密码错误') !== -1 || s.indexOf('401') !== -1) return 'Correo o contraseña incorrectos';
    if (s.indexOf('邮箱') !== -1 && s.indexOf('验证') !== -1) return 'Correo no verificado';
    if (s.indexOf('网络') !== -1 || s.indexOf('red') !== -1) return 'Error de red, intente más tarde';
    return s; // 未知则原样显示，避免丢信息
}

async function handleLogin(email, password) {
    var loginErrorEl = document.getElementById('loginError');
    if (loginErrorEl) { loginErrorEl.textContent = ''; loginErrorEl.classList.add('hidden'); }
    try {
        const result = await apiRequest('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ email, password }),
            // CHANGE: 登录失败由本函数统一提示，避免 apiRequest 再弹一次通用错误
            silent: true
        });
        
        if (result.success && result.data) {
            saveAuth(result.data.token, {
                user_id: result.data.user_id,
                email: result.data.email,
                name: result.data.name,
                avatar_url: result.data.avatar_url
            });
            updateUserUI();
            hideAuthModal();
            showToast('Inicio de sesión exitoso', 'success');
            // 重新加载购物车
            if (typeof loadCart === 'function') {
                loadCart();
            } else {
                renderCart();
            }
        } else {
            var msg = getLoginErrorMessage(result.error);
            if (loginErrorEl) { loginErrorEl.textContent = msg; loginErrorEl.classList.remove('hidden'); }
            showToast(msg, 'error');
        }
    } catch (error) {
        console.error('登录失败:', error);
        var msg = getLoginErrorMessage(error && error.message);
        if (loginErrorEl) { loginErrorEl.textContent = msg; loginErrorEl.classList.remove('hidden'); }
        showToast(msg, 'error');
    }
}

async function handleRegister(name, email, password) {
    try {
        const result = await apiRequest('/auth/register', {
            method: 'POST',
            body: JSON.stringify({ name, email, password })
        });
        
        if (result.success && result.data) {
            saveAuth(result.data.token, {
                user_id: result.data.user_id,
                email: result.data.email,
                name: result.data.name,
                avatar_url: result.data.avatar_url
            });
            updateUserUI();
            hideAuthModal();
            showToast('Registro exitoso', 'success');
            // 重新加载购物车
            if (typeof loadCart === 'function') {
                loadCart();
            } else {
                renderCart();
            }
        } else {
            showToast(result.error || 'Error al registrarse', 'error');
        }
    } catch (error) {
        console.error('注册失败:', error);
        showToast('Error al registrarse', 'error');
    }
}

// CHANGE: 忘记密码
// CHANGE: 从 reset_url 中解析 token（兼容未返回 reset_token 的旧后端）
function _parseTokenFromResetUrl(resetUrl) {
    if (!resetUrl || typeof resetUrl !== 'string') return null;
    var idx = resetUrl.indexOf('token=');
    if (idx === -1) return null;
    var start = idx + 6;
    var end = resetUrl.indexOf('&', start);
    var token = end === -1 ? resetUrl.substring(start) : resetUrl.substring(start, end);
    return token ? decodeURIComponent(token) : null;
}

function _getResetTokenFromUrl() {
    var hash = (typeof location !== 'undefined' && location.hash) ? location.hash.trim() : '';
    if (hash.indexOf('#/reset') === 0) {
        var q = hash.indexOf('?');
        if (q !== -1) {
            var params = hash.substring(q + 1).split('&');
            for (var i = 0; i < params.length; i++) {
                var p = params[i].split('=');
                if (p[0] === 'token' && p[1]) return decodeURIComponent(p[1]);
            }
        }
    }
    return null;
}

async function handleForgotPassword(email) {
    try {
        const result = await apiRequest('/auth/forgot-password', {
            method: 'POST',
            body: JSON.stringify({ email: email.trim().toLowerCase() })
        });
        
        // CHANGE: 邮箱已注册时直接弹出重置密码表单，不显示链接（避免客户抗拒链接、担心诈骗）
        var token = result.reset_token || (result.reset_url ? _parseTokenFromResetUrl(result.reset_url) : null);
        if (result.success && token) {
            showToast(getForgotMessage(result.message) || 'Introduce tu nueva contraseña a continuación.', 'success');
            showAuthModal('reset', token);
        } else if (result.success && result.reset_url) {
            var forgotSuccess = document.getElementById('forgotSuccess');
            var forgotResetLink = document.getElementById('forgotResetLink');
            var forgotForm = document.getElementById('forgotFormElement');
            if (forgotSuccess && forgotResetLink) {
                forgotResetLink.href = result.reset_url;
                forgotResetLink.textContent = result.reset_url;
                forgotSuccess.classList.remove('hidden');
                if (forgotForm) forgotForm.classList.add('hidden');
            }
            showToast(getForgotMessage(result.message) || 'Revisa el enlace para restablecer', 'success');
        } else if (result.success) {
            showToast(getForgotMessage(result.message) || 'Si el correo está registrado, recibirás el enlace en esta página.', 'success');
        } else {
            showToast(result.error || 'Error al solicitar recuperación', 'error');
        }
    } catch (error) {
        console.error('忘记密码失败:', error);
        showToast('Error al solicitar recuperación', 'error');
    }
}

async function handleResetPassword(token, password) {
    try {
        const result = await apiRequest('/auth/reset-password', {
            method: 'POST',
            body: JSON.stringify({ token: token, password: password })
        });
        
        if (result.success) {
            showToast('Contraseña restablecida correctamente', 'success');
            hideAuthModal();
            if (typeof history !== 'undefined' && history.replaceState) {
                history.replaceState(null, '', location.pathname + location.search);
            }
        } else {
            showToast(result.error || 'Error al restablecer contraseña', 'error');
        }
    } catch (error) {
        console.error('重置密码失败:', error);
        showToast('Error al restablecer contraseña', 'error');
    }
}


async function verifyToken(token) {
    try {
        const result = await apiRequest('/auth/verify', {
            method: 'POST',
            body: JSON.stringify({ token })
        });
        
        if (result.success && result.data) {
            AppState.user = result.data;
            AppState.userId = result.data.user_id;
            updateUserUI();
        } else {
            // Token无效，清除认证信息
            clearAuth();
            updateUserUI();
        }
    } catch (error) {
        console.error('验证token失败:', error);
        clearAuth();
        updateUserUI();
    }
}

function logout() {
    clearAuth();
    updateUserUI();
    // 清空购物车
    AppState.cart = [];
    renderCart();
    showToast('Sesión cerrada', 'info');
}

// CHANGE: 只要数据库有资料就显示产品；图加载失败用占位图，不隐藏卡片（避免整页空白）
var PRODUCT_PLACEHOLDER_SVG = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"><rect fill="#f0f0f0" width="200" height="200"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#999" font-size="14" font-family="sans-serif">Sin imagen</text></svg>');
function handleImageError(imgElement) {
    imgElement.onerror = null;
    if (imgElement.src && imgElement.src.includes('data:image/svg+xml')) {
        logImageDebug('error-placeholder-skip', imgElement.src, imgElement.src, {});
        return;
    }
    var failedUrl = (imgElement.src || '').trim();
    if (failedUrl && AppState && AppState._badImagePaths) {
        AppState._badImagePaths[String(failedUrl).toLowerCase()] = true;
        var rawFailed = String(imgElement.getAttribute('data-image-raw') || '').trim();
        if (rawFailed) AppState._badImagePaths[rawFailed.toLowerCase()] = true;
        var resolvedFailed = String(imgElement.getAttribute('data-image-src') || '').trim();
        if (resolvedFailed) AppState._badImagePaths[resolvedFailed.toLowerCase()] = true;
    }
    logImageDebug('error-start', failedUrl, '', { alt: imgElement.alt || '' });

    // CHANGE: 关闭坏图多轮重试；首次失败即隐藏卡片，避免大量 404 拖慢页面

    // CHANGE: 若最终仍然无法加载，直接隐藏该商品卡，避免坏图继续显示
    var failedUrlShort = failedUrl.substring(0, 150) + (failedUrl.length > 150 ? '...' : '');
    const productCard = imgElement.closest('.product-card');
    const cartItem = imgElement.closest('.cart-item');
    if (productCard) {
        productCard.style.display = 'none';
        logImageDebug('error-final-hide-product', failedUrl, '', { failedShort: failedUrlShort });
        return;
    }
    if (cartItem) {
        cartItem.style.display = 'none';
        logImageDebug('error-final-hide-cart', failedUrl, '', { failedShort: failedUrlShort });
        return;
    }
}

// CHANGE: 图片大图显示功能，初始尺寸自动匹配屏幕，支持PC指针放大、移动端双指缩放
var _imageModalZoomState = { baseScale: 1, userScale: 1, lastPinchDist: 0 };

function showImageModal(imageSrc) {
    if (!imageSrc || imageSrc.includes('data:image/svg+xml')) {
        return; // 不显示占位图
    }
    
    var modal = document.getElementById('imageModal');
    var img = document.getElementById('imageModalImg');
    var wrap = document.getElementById('imageModalZoomWrap');
    
    if (!modal || !img || !wrap) return;
    
    img.src = imageSrc;
    _imageModalZoomState.baseScale = 1;
    _imageModalZoomState.userScale = 1;
    _imageModalZoomState.lastPinchDist = 0;
    wrap.scrollTop = 0;
    wrap.scrollLeft = 0;
    img.style.transform = '';
    img.style.width = '';
    img.style.height = '';
    img.style.maxWidth = '';
    img.style.maxHeight = '';
    modal.classList.remove('hidden');
    
    // 图片加载完成后：尺寸自动匹配屏幕（整图可见），再支持缩放
    img.onload = function() {
        var nw = img.naturalWidth || img.width;
        var nh = img.naturalHeight || img.height;
        if (!nw || !nh) return;
        var rect = wrap.getBoundingClientRect();
        var wrapW = rect.width || wrap.clientWidth || 300;
        var wrapH = rect.height || wrap.clientHeight || 300;
        var fitScale = Math.min(wrapW / nw, wrapH / nh, 1); /* 不放大小图，仅缩小大图以匹配屏幕 */
        _imageModalZoomState.baseScale = fitScale;
        _imageModalZoomState.userScale = 1;
        var w = Math.round(nw * fitScale);
        var h = Math.round(nh * fitScale);
        img.style.width = w + 'px';
        img.style.height = h + 'px';
    };
    if (img.complete && img.naturalWidth) {
        img.onload();
    }
}

function hideImageModal() {
    var modal = document.getElementById('imageModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

// CHANGE: PC 滚轮缩放，指针指到哪里放大到哪里
function _imageModalWheelZoom(e) {
    var modal = document.getElementById('imageModal');
    var img = document.getElementById('imageModalImg');
    var wrap = document.getElementById('imageModalZoomWrap');
    if (!modal || modal.classList.contains('hidden') || !img || !wrap || !img.complete) return;
    
    e.preventDefault();
    var nw = img.naturalWidth || img.offsetWidth;
    var nh = img.naturalHeight || img.offsetHeight;
    if (!nw || !nh) return;
    var totalScale = _imageModalZoomState.baseScale * _imageModalZoomState.userScale;
    var rect = wrap.getBoundingClientRect();
    var vx = e.clientX - rect.left;
    var vy = e.clientY - rect.top;
    var px = vx + wrap.scrollLeft;
    var py = vy + wrap.scrollTop;
    var sx = px / totalScale;
    var sy = py / totalScale;
    var factor = e.deltaY > 0 ? 0.9 : 1.1;
    var newUserScale = Math.max(0.5, Math.min(5, _imageModalZoomState.userScale * factor));
    var newTotalScale = _imageModalZoomState.baseScale * newUserScale;
    _imageModalZoomState.userScale = newUserScale;
    img.style.width = (nw * newTotalScale) + 'px';
    img.style.height = (nh * newTotalScale) + 'px';
    wrap.scrollLeft = sx * newTotalScale - vx;
    wrap.scrollTop = sy * newTotalScale - vy;
}

// CHANGE: 移动端双指捏合缩放
function _imageModalTouchZoom(e) {
    var modal = document.getElementById('imageModal');
    var img = document.getElementById('imageModalImg');
    var wrap = document.getElementById('imageModalZoomWrap');
    if (!modal || modal.classList.contains('hidden') || !img || !wrap || e.touches.length !== 2) return;
    
    e.preventDefault();
    var nw = img.naturalWidth || img.offsetWidth;
    var nh = img.naturalHeight || img.offsetHeight;
    if (!nw || !nh) return;
    var t0 = e.touches[0];
    var t1 = e.touches[1];
    var dist = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
    var cx = (t0.clientX + t1.clientX) / 2;
    var cy = (t0.clientY + t1.clientY) / 2;
    var rect = wrap.getBoundingClientRect();
    var vx = cx - rect.left;
    var vy = cy - rect.top;
    var px = vx + wrap.scrollLeft;
    var py = vy + wrap.scrollTop;
    var totalScale = _imageModalZoomState.baseScale * _imageModalZoomState.userScale;
    var sx = px / totalScale;
    var sy = py / totalScale;
    var newUserScale;
    if (_imageModalZoomState.lastPinchDist > 0) {
        var factor = dist / _imageModalZoomState.lastPinchDist;
        newUserScale = Math.max(0.5, Math.min(5, _imageModalZoomState.userScale * factor));
    } else {
        newUserScale = _imageModalZoomState.userScale;
    }
    _imageModalZoomState.lastPinchDist = dist;
    _imageModalZoomState.userScale = newUserScale;
    var newTotalScale = _imageModalZoomState.baseScale * newUserScale;
    img.style.width = (nw * newTotalScale) + 'px';
    img.style.height = (nh * newTotalScale) + 'px';
    wrap.scrollLeft = sx * newTotalScale - vx;
    wrap.scrollTop = sy * newTotalScale - vy;
}

function _imageModalTouchEnd() {
    _imageModalZoomState.lastPinchDist = 0;
}

// CHANGE: PC 点击图片放大（指针放大镜对应功能）
function _imageModalClickZoom(e) {
    var modal = document.getElementById('imageModal');
    var img = document.getElementById('imageModalImg');
    var wrap = document.getElementById('imageModalZoomWrap');
    if (!modal || modal.classList.contains('hidden') || !img || !wrap || !img.complete) return;
    e.preventDefault();
    var nw = img.naturalWidth || img.offsetWidth;
    var nh = img.naturalHeight || img.offsetHeight;
    if (!nw || !nh) return;
    var totalScale = _imageModalZoomState.baseScale * _imageModalZoomState.userScale;
    var rect = wrap.getBoundingClientRect();
    var vx = e.clientX - rect.left;
    var vy = e.clientY - rect.top;
    var px = vx + wrap.scrollLeft;
    var py = vy + wrap.scrollTop;
    var sx = px / totalScale;
    var sy = py / totalScale;
    var factor = 1.25;
    var newUserScale = Math.max(0.5, Math.min(5, _imageModalZoomState.userScale * factor));
    var newTotalScale = _imageModalZoomState.baseScale * newUserScale;
    _imageModalZoomState.userScale = newUserScale;
    img.style.width = (nw * newTotalScale) + 'px';
    img.style.height = (nh * newTotalScale) + 'px';
    wrap.scrollLeft = sx * newTotalScale - vx;
    wrap.scrollTop = sy * newTotalScale - vy;
}

// 初始化图片模态框
function initImageModal() {
    var modal = document.getElementById('imageModal');
    var closeBtn = document.getElementById('imageModalCloseBtn');
    var wrap = document.getElementById('imageModalZoomWrap');
    
    if (!modal || !closeBtn) return;
    
    // 关闭按钮
    closeBtn.addEventListener('click', hideImageModal);
    
    // CHANGE: 点击图片以外区域（遮罩、空白区、内容区）关闭；点击图片则放大
    var img = document.getElementById('imageModalImg');
    modal.addEventListener('click', function(e) {
        if (e.target === img) {
            _imageModalClickZoom(e);
            return;
        }
        hideImageModal();
    });
    
    // ESC键关闭
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideImageModal();
        }
    });
    
    // CHANGE: PC 滚轮缩放（指针跟随）
    if (wrap) {
        wrap.addEventListener('wheel', _imageModalWheelZoom, { passive: false });
    }
    
    // CHANGE: 移动端双指捏合缩放
    if (wrap) {
        wrap.addEventListener('touchstart', function(e) {
            if (e.touches.length === 2) {
                e.preventDefault();
                _imageModalTouchZoom(e);
            }
        }, { passive: false });
        wrap.addEventListener('touchmove', function(e) {
            if (e.touches.length === 2) {
                e.preventDefault();
                _imageModalTouchZoom(e);
            }
        }, { passive: false });
        wrap.addEventListener('touchend', _imageModalTouchEnd);
        wrap.addEventListener('touchcancel', _imageModalTouchEnd);
    }
}

// 初始化客户信息表单模态框
// ===== 添加到主屏幕模态框（无原生安装提示时显示步骤说明，支持 ESC 关闭） =====
function showAddToHomeModal() {
    const modal = document.getElementById('addToHomeModal');
    const stepsIOS = document.getElementById('addToHomeStepsIOS');
    const stepsAndroid = document.getElementById('addToHomeStepsAndroid');
    const stepsGeneric = document.getElementById('addToHomeStepsGeneric');
    if (!modal) return;
    var ua = navigator.userAgent || '';
    var isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    var isAndroid = /Android/.test(ua);
    if (stepsIOS) stepsIOS.classList.toggle('hidden', !isIOS);
    if (stepsAndroid) stepsAndroid.classList.toggle('hidden', !isAndroid);
    if (stepsGeneric) stepsGeneric.classList.toggle('hidden', isIOS || isAndroid);
    modal.classList.remove('hidden');
    if (typeof lucide !== 'undefined' && lucide.createIcons) lucide.createIcons();
}

function hideAddToHomeModal() {
    const modal = document.getElementById('addToHomeModal');
    if (modal) modal.classList.add('hidden');
}

function initAddToHomeModal() {
    const modal = document.getElementById('addToHomeModal');
    const closeBtn = document.getElementById('addToHomeModalCloseBtn');
    if (!modal || !closeBtn) return;
    closeBtn.addEventListener('click', hideAddToHomeModal);
    modal.addEventListener('click', function(e) {
        if (e.target === modal) hideAddToHomeModal();
    });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) hideAddToHomeModal();
    });
}

function initCustomerInfoModal() {
    const modal = document.getElementById('customerInfoModal');
    const closeBtn = document.getElementById('customerInfoModalCloseBtn');
    const cancelBtn = document.getElementById('customerInfoCancelBtn');
    const submitBtn = document.getElementById('customerInfoSubmitBtn');
    
    if (!modal || !closeBtn || !cancelBtn || !submitBtn) {
        console.error('❌ 客户信息模态框元素未找到');
        return;
    }
    
    // 关闭按钮
    closeBtn.addEventListener('click', hideCustomerInfoModal);
    cancelBtn.addEventListener('click', hideCustomerInfoModal);
    
    // 提交按钮
    submitBtn.addEventListener('click', submitOrderWithCustomerInfo);
    
    // 点击背景关闭
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            hideCustomerInfoModal();
        }
    });
    
    // ESC键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            hideCustomerInfoModal();
        }
    });
    
    // 表单回车提交
    const form = document.getElementById('customerInfoForm');
    if (form) {
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            submitOrderWithCustomerInfo();
        });
    }
}

// Hacer funciones disponibles en el ámbito global
window.addToCart = addToCart;
window.removeFromCart = removeFromCart;
window.updateQuantity = updateQuantity;
window.switchView = switchView;
window.fetchProducts = fetchProducts; // 添加这个，方便调试
window.AppState = AppState; // 添加这个，方便调试
window.showImageModal = showImageModal; // CHANGE: 导出图片大图函数

// Depuración: verificar que las funciones estén correctamente expuestas
console.log('🔍 [GLOBAL] 检查全局函数:');
console.log('   window.addToCart:', typeof window.addToCart);
console.log('   window.removeFromCart:', typeof window.removeFromCart);
console.log('   window.updateQuantity:', typeof window.updateQuantity);
console.log('   window.switchView:', typeof window.switchView);

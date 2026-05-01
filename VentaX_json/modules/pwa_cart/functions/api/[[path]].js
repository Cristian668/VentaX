import productsSnapshot from './products_snapshot.json';

/**
 * Cloudflare Pages Function: 只走 Edge Worker（Neon）+ 本地 snapshot/stale。
 * CHANGE: 移除 Render 回退，彻底去除 Render 依赖，避免 suspend 导致慢加载/503。
 */

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Session-Id, X-Admin-Token',
  };
}

function isCacheableGet(request, path) {
  if (request.method !== 'GET') return false;
  if (!path) return false;
  return path === 'products' || path === 'categories' || path === 'suppliers' || /^products\//.test(path);
}

function cacheTtlByPath(path) {
  if (path === 'products') return 45;
  if (path === 'suppliers') return 60;
  if (/^products\//.test(path)) return 120;
  return 30;
}

function buildCacheKey(request, path, url) {
  const u = new URL(request.url);
  u.pathname = `/__pwa_cache__/api/${path}`;
  u.search = url.search;
  return new Request(u.toString(), { method: 'GET' });
}

function responseIsCacheable(res) {
  if (!res) return false;
  if (!res.ok) return false;
  const ct = String(res.headers.get('content-type') || '').toLowerCase();
  return ct.includes('application/json');
}

function decorateResponse(baseResponse, origin, source, extras) {
  const h = new Headers(baseResponse.headers);
  h.set('x-api-proxy-source', source);
  Object.entries(corsHeaders(origin)).forEach(([k, v]) => h.set(k, v));
  if (extras) {
    Object.entries(extras).forEach(([k, v]) => {
      if (v !== undefined && v !== null) h.set(k, String(v));
    });
  }
  return new Response(baseResponse.body, { status: baseResponse.status, headers: h });
}

function isAdminProductPatch(path, request) {
  const method = String(request && request.method || '').toUpperCase();
  // CHANGE: 修复部署构建失败：正则必须闭合后再调用 test。
  return method === 'PATCH' && /^admin\/products\//.test(String(path || ''));
}

async function buildAdminProductWrite502Response(origin, path, upstreamResponse, source, details) {
  const retryAfter = String((upstreamResponse && upstreamResponse.headers && upstreamResponse.headers.get('Retry-After')) || '8').trim() || '8';
  const h = new Headers(corsHeaders(origin));
  h.set('Content-Type', 'application/json');
  h.set('x-api-proxy-source', source || 'edge-worker');
  h.set('x-api-proxy-code', 'ADMIN_PRODUCT_WRITE_UPSTREAM_502');
  h.set('Retry-After', retryAfter);

  let upstreamMsg = '';
  try {
    if (upstreamResponse) {
      const ct = String(upstreamResponse.headers.get('content-type') || '').toLowerCase();
      if (ct.includes('application/json')) {
        const j = await upstreamResponse.clone().json();
        upstreamMsg = String((j && (j.error?.message || j.error || j.message)) || '').trim();
      } else {
        upstreamMsg = String((await upstreamResponse.clone().text()) || '').slice(0, 280);
      }
    }
  } catch (_) {}

  return new Response(JSON.stringify({
    success: false,
    error: {
      code: 'ADMIN_PRODUCT_WRITE_UPSTREAM_502',
      message: '云端写入接口临时故障（非前端问题），请稍后重试。',
      details: details || upstreamMsg || 'upstream 5xx on admin product patch',
      path,
      retry_after_seconds: Number(retryAfter) || 8
    }
  }), {
    status: 502,
    headers: h,
  });
}

function inferSupplierAndImagePath(rawPath, currentSupplier) {
  const imagePathRaw = String(rawPath || '').trim();
  const supplierRaw = String(currentSupplier || '').trim();
  const supplierLower = supplierRaw.toLowerCase();
  const normalized = imagePathRaw.replace(/\\/g, '/');
  const lower = normalized.toLowerCase();

  // 1) 显式 Cristy 供应商
  if (supplierLower === 'cristy') {
    const filename = normalized.split('/').pop() || '';
    return {
      supplier: 'Cristy',
      image_path: filename ? `Ya Subio/Cristy/${filename}` : imagePathRaw,
    };
  }

  // 2) 通过路径推断 Cristy（旧产品常见：codigo_proveedor 为空）
  const isCristyPath =
    lower.includes('/cristy/procesado/') ||
    lower.includes('/ya subio/cristy/') ||
    lower.includes(':/cristy/procesado/') ||
    /^ya\s*subio\/cristy\//i.test(normalized);
  if (isCristyPath) {
    const filename = normalized.split('/').pop() || '';
    return {
      supplier: 'Cristy',
      image_path: filename ? `Ya Subio/Cristy/${filename}` : imagePathRaw,
    };
  }

  // 3) D:/.../output_images/<subfolder>/<file> -> Ya Subio/<subfolder>/<file>
  const marker = '/output_images/';
  const markerIdx = lower.indexOf(marker);
  if (markerIdx !== -1) {
    const rel = normalized.slice(markerIdx + marker.length).replace(/^\/+/, '');
    const parts = rel.split('/').filter(Boolean);
    const inferredSupplier = parts.length > 1 ? parts[0] : supplierRaw;
    return {
      supplier: inferredSupplier || supplierRaw,
      image_path: `Ya Subio/${rel}`,
    };
  }

  // 4) 裸文件名（如 Importadora_Chinito_xxx.jpg）统一映射到 Ya Subio 根目录
  const isBareFile = /^[^/\\]+\.(jpg|jpeg|png|webp|gif)$/i.test(imagePathRaw);
  if (isBareFile) {
    return {
      supplier: supplierRaw,
      image_path: `Ya Subio/${imagePathRaw}`,
    };
  }

  // 5) 已是 Ya Subio 相对路径，规范斜杠
  if (/^\/?ya\s*subio\//i.test(normalized)) {
    return {
      supplier: supplierRaw,
      image_path: normalized.replace(/^\/+/, ''),
    };
  }

  // 默认保留原路径（前端会再兜底）
  return {
    supplier: supplierRaw,
    image_path: imagePathRaw,
  };
}

function normalizeProduct(item) {
  const inferred = inferSupplierAndImagePath(item.image_path, item.codigo_proveedor);
  return {
    id: item.id,
    id_producto: item.id,
    codigo_producto: item.product_code || '',
    name: item.name || '',
    nombre_producto: item.name || '',
    price: Number(item.price || 0),
    precio_unidad: Number(item.price || 0),
    wholesale_price: Number(item.wholesale_price || 0),
    precio_mayor: Number(item.wholesale_price || 0),
    bulk_price: Number(item.bulk_price || 0),
    precio_bulto: Number(item.bulk_price || 0),
    description: item.description || '',
    image_path: inferred.image_path || '',
    ruta_imagen: inferred.image_path || '',
    category: item.category || '',
    codigo_proveedor: inferred.supplier || '',
    channel_username: item.channel_username || '',
    created_at: item.created_at || null,
  };
}

function buildSuppliersFromRows(rows) {
  const map = new Map();
  for (const p of (rows || [])) {
    const raw = String((p && p.channel_username) || '').trim();
    if (!raw) continue;
    const key = raw.toLowerCase();
    const rec = map.get(key) || { supplier: raw, count: 0 };
    rec.count += 1;
    map.set(key, rec);
  }
  return Array.from(map.values()).sort((a, b) => {
    const dc = Number(b.count || 0) - Number(a.count || 0);
    if (dc !== 0) return dc;
    return String(a.supplier || '').localeCompare(String(b.supplier || ''));
  });
}

function fromSnapshotByPath(path, url, origin) {
  // 兼容某些旧前端/插件探测接口：避免 /api/device* 在 Pages 上 404 噪音
  if (/^devices?(\/|$)/.test(path)) {
    const h = new Headers(corsHeaders(origin));
    h.set('Content-Type', 'application/json; charset=utf-8');
    h.set('x-api-proxy-source', 'snapshot-fallback');
    h.set('x-api-cache', 'BYPASS');
    return new Response(JSON.stringify({ success: true, data: { status: 'ok', source: 'edge-fallback' } }), { status: 200, headers: h });
  }

  if (path === 'products') {
    const supplier = (url.searchParams.get('supplier') || '').toLowerCase();
    // 兼容前端 searchProducts 使用 ?search=，以及旧链接 ?q=
    const q = (url.searchParams.get('search') || url.searchParams.get('q') || '').toLowerCase();
    const category = (url.searchParams.get('category') || '').toLowerCase();
    const limit = Math.max(1, Math.min(5000, Number(url.searchParams.get('limit') || 100)));
    const offset = Math.max(0, Number(url.searchParams.get('offset') || 0));

    let rows = productsSnapshot;
    if (supplier) {
      const supplierNeedle = String(supplier || '').toLowerCase();
      rows = rows.filter((p) => {
        const inferred = inferSupplierAndImagePath(p.image_path, p.codigo_proveedor);
        const code = String(inferred.supplier || p.codigo_proveedor || '').toLowerCase();
        const channel = String(p.channel_username || '').toLowerCase();

        if (supplierNeedle === 'others') {
          // 只要不是 Cristy 且不是空供应商，就视为 PRODUCTOS
          //（空供应商多为历史 Cristy 数据，避免被误分到 others）
          return code !== 'cristy' && code !== '';
        }

        if (supplierNeedle === 'cristy') {
          // 兼容历史数据：很多旧记录 codigo_proveedor 为空，但实际属于 Cristy
          return code === 'cristy' || code === '' || channel.includes('cristy');
        }

        return code.includes(supplierNeedle) || channel.includes(supplierNeedle);
      });
    }
    if (q) {
      rows = rows.filter((p) => {
        const a = String(p.name || '').toLowerCase();
        const b = String(p.product_code || '').toLowerCase();
        const c = String(p.id || '').toLowerCase();
        return a.includes(q) || b.includes(q) || c.includes(q);
      });
    }

    if (category) {
      rows = rows.filter((p) => {
        const cat = String((p.category || p.codigo_proveedor || '')).toLowerCase();
        return cat === category;
      });
    }

    const total = rows.length;
    const page = rows.slice(offset, offset + limit).map(normalizeProduct);
    const h = new Headers(corsHeaders(origin));
    h.set('Content-Type', 'application/json; charset=utf-8');
    h.set('x-api-proxy-source', 'snapshot-fallback');
    h.set('x-api-cache', 'BYPASS');
    return new Response(JSON.stringify({ success: true, data: page, count: page.length, total }), { status: 200, headers: h });
  }

  if (path === 'categories') {
    const map = new Map();
    for (const p of productsSnapshot) {
      const name = String(p.category || p.codigo_proveedor || 'Otros').trim();
      if (!name) continue;
      map.set(name, (map.get(name) || 0) + 1);
    }
    const data = Array.from(map.entries()).map(([name, count]) => ({ name, count }));
    const h = new Headers(corsHeaders(origin));
    h.set('Content-Type', 'application/json; charset=utf-8');
    h.set('x-api-proxy-source', 'snapshot-fallback');
    h.set('x-api-cache', 'BYPASS');
    return new Response(JSON.stringify({ success: true, data }), { status: 200, headers: h });
  }

  if (path === 'suppliers') {
    const data = buildSuppliersFromRows(productsSnapshot);
    const h = new Headers(corsHeaders(origin));
    h.set('Content-Type', 'application/json; charset=utf-8');
    h.set('x-api-proxy-source', 'snapshot-fallback');
    h.set('x-api-cache', 'BYPASS');
    return new Response(JSON.stringify({ success: true, data }), { status: 200, headers: h });
  }

  if (/^products\//.test(path)) {
    const key = decodeURIComponent(path.replace(/^products\//, '')).toLowerCase();
    const found = productsSnapshot.find((p) =>
      String(p.product_code || '').toLowerCase() === key || String(p.id) === key
    );
    const h = new Headers(corsHeaders(origin));
    h.set('Content-Type', 'application/json; charset=utf-8');
    h.set('x-api-proxy-source', 'snapshot-fallback');
    h.set('x-api-cache', 'BYPASS');
    if (!found) {
      return new Response(JSON.stringify({ success: false, error: 'Product not found' }), { status: 404, headers: h });
    }
    return new Response(JSON.stringify({ success: true, data: normalizeProduct(found) }), { status: 200, headers: h });
  }

  return null;
}

export async function onRequestOptions(context) {
  return new Response(null, {
    status: 204,
    headers: corsHeaders(context.request.headers.get('Origin')),
  });
}

export async function onRequest(context) {
  const { request, params } = context;
  const path = Array.isArray(params.path) ? params.path.join('/') : (params.path || '');
  const url = new URL(request.url);

  const edgeBase = String(context.env?.EDGE_API_BASE || '').replace(/\/+$/, '');
  const edgeUrl = edgeBase ? `${edgeBase}/api/${path}${url.search}` : null;

  const headers = new Headers();
  request.headers.forEach((v, k) => {
    const lower = k.toLowerCase();
    if (lower !== 'host' && lower !== 'origin' && lower !== 'referer') {
      headers.set(k, v);
    }
  });

  // CHANGE: 管理端请求确保双头透传（X-Admin-Token + Authorization）
  // 兼容上游仅校验其中一种头，避免出现 "Unauthorized admin token (expected from ADMIN_API_TOKEN)"。
  if (/^admin\//.test(path)) {
    const adminToken = String(request.headers.get('X-Admin-Token') || '').trim();
    const authHeader = String(request.headers.get('Authorization') || '').trim();
    if (adminToken) {
      headers.set('X-Admin-Token', adminToken);
      if (!authHeader) headers.set('Authorization', `Bearer ${adminToken}`);
    } else if (authHeader.toLowerCase().startsWith('bearer ')) {
      const bearerToken = authHeader.slice(7).trim();
      if (bearerToken) headers.set('X-Admin-Token', bearerToken);
    }
  }

  const origin = request.headers.get('Origin') || 'https://ventax.pages.dev';
  const cacheable = isCacheableGet(request, path);
  const cache = cacheable ? caches.default : null;
  const cacheKey = cacheable ? buildCacheKey(request, path, url) : null;
  const ttl = cacheable ? cacheTtlByPath(path) : 0;

  const doFetch = async (targetUrl) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 25000);
    try {
      return await fetch(targetUrl, {
        method: request.method,
        headers,
        body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const doFetchWithRetry = async (targetUrl, label) => {
    const method = String(request.method || 'GET').toUpperCase();
    const isGet = method === 'GET';
    // CHANGE: 对 PATCH/PUT/DELETE 启用有限重试，缓解上游短暂 502 导致的分类切换失败
    const canRetryWrite = method === 'PATCH' || method === 'PUT' || method === 'DELETE';
    const maxAttempts = isGet ? 4 : (canRetryWrite ? 3 : 1);
    let lastRes = null;
    let lastErr = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        const res = await doFetch(targetUrl);
        lastRes = res;
        const shouldRetryStatus =
          (isGet || canRetryWrite) && (res.status === 429 || res.status === 502 || res.status === 503 || res.status === 504);
        if (!shouldRetryStatus || attempt === maxAttempts) {
          const h = new Headers(res.headers);
          h.set('x-api-proxy-attempts', String(attempt));
          h.set('x-api-proxy-target', label);
          return new Response(res.body, { status: res.status, headers: h });
        }
      } catch (err) {
        lastErr = err;
        if (!(isGet || canRetryWrite) || attempt === maxAttempts) throw err;
      }

      const backoff = attempt === 1 ? 900 : (attempt === 2 ? 1800 : 3200);
      await sleep(backoff);
    }

    if (lastRes) {
      const h = new Headers(lastRes.headers);
      h.set('x-api-proxy-attempts', String(maxAttempts));
      h.set('x-api-proxy-target', label);
      return new Response(lastRes.body, { status: lastRes.status, headers: h });
    }
    throw lastErr || new Error('Upstream request failed');
  };

  const readStaleCache = async () => {
    if (!cache || !cacheKey) return null;
    const hit = await cache.match(cacheKey);
    if (!hit) return null;
    return decorateResponse(hit, origin, 'cache-stale', { 'x-api-cache': 'STALE' });
  };

  const storeCacheIfNeeded = async (res, source) => {
    if (!cache || !cacheKey || !responseIsCacheable(res)) return;
    const cacheHeaders = new Headers(res.headers);
    cacheHeaders.set('Cache-Control', `public, max-age=${ttl}, s-maxage=${ttl}`);
    cacheHeaders.set('x-api-cache-stored-by', source);
    const body = await res.clone().arrayBuffer();
    await cache.put(cacheKey, new Response(body, { status: res.status, headers: cacheHeaders }));
  };

  if (cache && cacheKey) {
    const hit = await cache.match(cacheKey);
    if (hit) {
      return decorateResponse(hit, origin, 'cache-hit', { 'x-api-cache': 'HIT' });
    }
  }

  try {
    if (edgeUrl) {
      let edgeResponse = await doFetchWithRetry(edgeUrl, 'edge-worker');

      // CHANGE: 统一把上游产品接口的 image_path/codigo_proveedor 规范成 Ya Subio 路径，
      // 避免前端因历史绝对路径（如 D:\\Cristy\\Procesado\\*.jpg）解析差异导致云端显示不一致。
      if (edgeResponse.ok && request.method === 'GET' && (path === 'products' || /^products\//.test(path))) {
        try {
          const ct = String(edgeResponse.headers.get('content-type') || '').toLowerCase();
          if (ct.includes('application/json')) {
            const parsed = await edgeResponse.clone().json();
            if (parsed && Array.isArray(parsed.data)) {
              const normalizedData = parsed.data.map(normalizeProduct);
              const normalizedPayload = { ...parsed, data: normalizedData };
              const nh = new Headers(edgeResponse.headers);
              nh.set('Content-Type', 'application/json; charset=utf-8');
              edgeResponse = new Response(JSON.stringify(normalizedPayload), {
                status: edgeResponse.status,
                headers: nh,
              });
            } else if (parsed && parsed.data && typeof parsed.data === 'object') {
              const normalizedPayload = { ...parsed, data: normalizeProduct(parsed.data) };
              const nh = new Headers(edgeResponse.headers);
              nh.set('Content-Type', 'application/json; charset=utf-8');
              edgeResponse = new Response(JSON.stringify(normalizedPayload), {
                status: edgeResponse.status,
                headers: nh,
              });
            }
          }
        } catch (_) {
          // 若上游响应不是标准 JSON，保持原响应，继续后续兜底流程
        }
      }

      if (edgeResponse.ok) {
        if (isAdminProductPatch(path, request)) {
          return decorateResponse(edgeResponse, origin, 'edge-worker', {
            'x-api-proxy-code': 'ADMIN_PRODUCT_WRITE_OK'
          });
        }
        await storeCacheIfNeeded(edgeResponse, 'edge-worker');
      }

      if (isAdminProductPatch(path, request) && edgeResponse.status === 502) {
        return await buildAdminProductWrite502Response(origin, path, edgeResponse, 'edge-worker', 'upstream returned 502 for admin product patch');
      }

      // /api/suppliers 兼容：若上游未实现（404）则用 products snapshot 聚合回退
      if ((!edgeResponse.ok || edgeResponse.status === 404) && cacheable) {
        const snap = fromSnapshotByPath(path, url, origin);
        if (snap) return snap;
        const stale = await readStaleCache();
        if (stale) return stale;
      }
      return decorateResponse(edgeResponse, origin, 'edge-worker', { 'x-api-cache': 'MISS' });
    }

    // 未配置 EDGE_API_BASE：直接走 snapshot/stale，避免去 Render
    if (cacheable) {
      const snap = fromSnapshotByPath(path, url, origin);
      if (snap) return snap;
      const stale = await readStaleCache();
      if (stale) return stale;
    }

    const h = new Headers(corsHeaders(origin));
    h.set('Content-Type', 'application/json');
    return new Response(JSON.stringify({
      success: false,
      error: 'EDGE_API_BASE not configured and no snapshot available',
      path
    }), { status: 503, headers: h });
  } catch (e) {
    if (cacheable) {
      const snap = fromSnapshotByPath(path, url, origin);
      if (snap) return snap;
      const stale = await readStaleCache();
      if (stale) return stale;
    }

    const rawMsg = String(e && e.message ? e.message : e || 'unknown error');
    const lowerMsg = rawMsg.toLowerCase();
    const isTimeout = lowerMsg.includes('abort') || lowerMsg.includes('timed out') || lowerMsg.includes('timeout');

    if (isAdminProductPatch(path, request)) {
      return await buildAdminProductWrite502Response(
        origin,
        path,
        null,
        'edge-worker',
        isTimeout ? 'upstream timeout on admin product patch' : rawMsg
      );
    }

    const h = new Headers(corsHeaders(origin));
    h.set('Content-Type', 'application/json');
    h.set('x-api-proxy-source', 'edge-worker');
    h.set('x-api-proxy-code', isTimeout ? 'UPSTREAM_TIMEOUT' : 'UPSTREAM_FETCH_FAILED');

    return new Response(JSON.stringify({
      success: false,
      error: {
        code: isTimeout ? 'UPSTREAM_TIMEOUT' : 'UPSTREAM_FETCH_FAILED',
        message: isTimeout
          ? '上游接口超时，请稍后重试'
          : '上游接口请求失败',
        details: rawMsg,
        upstream: edgeUrl || null,
        path,
        method: request.method
      }
    }), {
      status: 502,
      headers: h,
    });
  }
}

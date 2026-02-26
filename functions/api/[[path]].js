/**
 * Cloudflare Pages Function: 代理 Render API，解决 CORS
 * 请求 /api/* 时转发到 ventax-46bs.onrender.com/api/*
 */
const RENDER_API = 'https://ventax-46bs.onrender.com';

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Session-Id',
  };
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
  const targetUrl = `${RENDER_API}/api/${path}${url.search}`;

  const headers = new Headers();
  request.headers.forEach((v, k) => {
    const lower = k.toLowerCase();
    if (lower !== 'host' && lower !== 'origin' && lower !== 'referer') {
      headers.set(k, v);
    }
  });

  try {
    const res = await fetch(targetUrl, {
      method: request.method,
      headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    });
    const resHeaders = new Headers(res.headers);
    Object.entries(corsHeaders(request.headers.get('Origin'))).forEach(([k, v]) => resHeaders.set(k, v));
    return new Response(res.body, { status: res.status, headers: resHeaders });
  } catch (e) {
    const h = new Headers(corsHeaders(request.headers.get('Origin')));
    h.set('Content-Type', 'application/json');
    return new Response(JSON.stringify({ error: 'Proxy error: ' + String(e.message) }), {
      status: 502,
      headers: h,
    });
  }
}

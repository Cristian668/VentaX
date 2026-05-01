#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VentaX 客服机器人 — 多频道 HTTP 服务器
提供 /chat（文字）、/chat/image（图片识别）API
+ WhatsApp webhook (/webhook/whatsapp)
+ TikTok webhook (/webhook/tiktok)
支持本地调试 + Render 云端部署
"""
import os
import json
import sys
import base64
import sqlite3
import logging
import threading
import queue
from datetime import datetime, timezone, timedelta
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ventax_customer_bot import chat, chat_with_image, chat_with_voice
from conversation_memory import save_message, save_customer_name, get_history, cleanup_old

logger = logging.getLogger("ventax_server")

# ── SSE 实时日志系统 ─────────────────────────────────────
_EC_TZ = timezone(timedelta(hours=-5))
_log_subscribers = []
_log_history = deque(maxlen=200)
_stats = {"messages": 0, "fast": 0, "llm": 0, "voice": 0, "errors": 0, "start_time": datetime.now(_EC_TZ).isoformat()}


def _push_log(level: str, text: str, extra: dict | None = None):
    """推送日志事件到所有 SSE 订阅者"""
    ts = datetime.now(_EC_TZ).strftime("%H:%M:%S")
    entry = {"ts": ts, "level": level, "text": text}
    if extra:
        entry.update(extra)
    _log_history.append(entry)
    dead = []
    for q in _log_subscribers:
        try:
            q.put_nowait(entry)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _log_subscribers.remove(q)
        except ValueError:
            pass


_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VentaX 24/7 Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px}
.header{background:#161b22;border-bottom:1px solid #30363d;padding:12px 20px;display:flex;align-items:center;gap:16px}
.header h1{font-size:18px;color:#58a6ff;font-weight:600}
.status{display:flex;gap:12px;margin-left:auto}
.stat{background:#21262d;border:1px solid #30363d;border-radius:8px;padding:8px 14px;text-align:center;min-width:80px}
.stat .num{font-size:22px;font-weight:700;color:#58a6ff}
.stat .label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
.stat.voice .num{color:#d2a8ff}
.stat.error .num{color:#f85149}
.stat.fast .num{color:#3fb950}
.hist-btn{background:#1f6feb;color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap;transition:background .2s}
.hist-btn:hover{background:#388bfd}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px}
.dot.on{background:#3fb950;box-shadow:0 0 6px #3fb950}
.dot.off{background:#f85149}
.conn-status{font-size:13px;color:#8b949e}
.main{display:flex;height:calc(100vh - 60px)}
.log-panel{flex:1;overflow-y:auto;padding:12px 16px;scroll-behavior:smooth}
.log-entry{padding:4px 0;border-bottom:1px solid #21262d;font-family:'Cascadia Code','Fira Code',monospace;font-size:13px;line-height:1.6;word-break:break-word}
.log-entry .ts{color:#484f58;margin-right:8px}
.log-entry.MSG .tag{color:#58a6ff}
.log-entry.REPLY .tag{color:#3fb950}
.log-entry.VOICE .tag{color:#d2a8ff}
.log-entry.ERROR .tag{color:#f85149}
.log-entry.INFO .tag{color:#e3b341}
.log-entry.WARN .tag{color:#d29922}
.log-entry.MANUAL .tag{color:#e3b341}
.tag{font-weight:600;margin-right:6px}
.path-badge{font-size:10px;padding:1px 5px;border-radius:3px;margin-left:6px;font-weight:600}
.path-badge.fast{background:#238636;color:#fff}
.path-badge.llm{background:#1f6feb;color:#fff}
.path-badge.voice{background:#8957e5;color:#fff}
.sidebar{width:280px;background:#161b22;border-left:1px solid #30363d;padding:16px;overflow-y:auto}
.sidebar h3{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
.info-item{padding:6px 0;border-bottom:1px solid #21262d;font-size:13px}
.info-item .k{color:#8b949e}.info-item .v{color:#c9d1d9;float:right}
@media(max-width:768px){.main{flex-direction:column}.sidebar{width:100%;max-height:200px}}
.conv-overlay{position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,.7);z-index:1000;display:flex;align-items:center;justify-content:center}
.conv-box{width:94vw;height:90vh;background:#0d1117;border:1px solid #30363d;border-radius:12px;display:flex;flex-direction:column;overflow:hidden}
.conv-top{background:#161b22;padding:10px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #30363d}
.conv-top h2{color:#58a6ff;font-size:15px;white-space:nowrap;margin:0}
.conv-top input{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:6px 10px;color:#c9d1d9;font-size:13px;outline:none}
.conv-top input:focus{border-color:#58a6ff}
.conv-top .cbtn{background:none;border:none;color:#8b949e;font-size:22px;cursor:pointer;padding:0 6px;line-height:1}
.conv-top .cbtn:hover{color:#f85149}
.conv-mid{display:flex;flex:1;overflow:hidden}
.conv-list{width:300px;border-right:1px solid #30363d;overflow-y:auto}
.conv-ci{padding:10px 14px;border-bottom:1px solid #21262d;cursor:pointer;transition:background .15s}
.conv-ci:hover{background:#161b22}
.conv-ci.active{background:#1f6feb22;border-left:3px solid #58a6ff}
.conv-ci .cn{color:#58a6ff;font-size:13px;font-weight:600}
.conv-ci .cm{color:#8b949e;font-size:11px;margin-top:2px}
.conv-ci .cl{color:#6e7681;font-size:12px;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conv-right{flex:1;display:flex;flex-direction:column;overflow:hidden}
.conv-rh{padding:10px 16px;background:#161b22;border-bottom:1px solid #30363d;color:#c9d1d9;font-size:14px;font-weight:600;display:none}
.conv-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
.conv-empty{color:#484f58;text-align:center;padding:60px 20px;font-size:14px}
.bubble{max-width:75%;padding:8px 12px;border-radius:10px;font-size:13px;line-height:1.5;word-break:break-word;white-space:pre-wrap}
.bubble.customer{align-self:flex-start;background:#21262d;color:#c9d1d9;border-bottom-left-radius:2px}
.bubble.assistant{align-self:flex-end;background:#1a4b2e;color:#d2f4d3;border-bottom-right-radius:2px}
.bubble .bts{font-size:10px;color:#484f58;margin-top:4px}
.conv-ft{display:none;gap:16px;padding:8px 16px;background:#161b22;border-top:1px solid #30363d;font-size:12px;color:#8b949e}
@media(max-width:768px){.conv-list{width:180px}}
.tab-bar{display:flex;gap:0;margin-left:16px;align-items:center}
.tab-bar .tab{padding:8px 20px;cursor:pointer;font-weight:600;font-size:14px;border-radius:8px;background:transparent;color:#8b949e;border:none}
.tab-bar .tab:hover{color:#c9d1d9;background:#21262d}
.tab-bar .tab.active{color:#58a6ff;background:#1f6feb22}
.tab-bar .tab.tab-tt.active{color:#00f2ea;background:#00f2ea22}
.panel-wa,.panel-tt{display:flex;flex:1;min-width:0;height:100%}
.panel-tt{flex-direction:column;overflow:hidden}
.panel-tt .tt-body{padding:20px;overflow-y:auto;flex:1;min-height:0}
</style></head><body>
<div class="header">
<h1>&#x1f6e0; VentaX 24/7</h1>
<nav class="tab-bar">
<button class="tab active" id="tabWa" onclick="switchTab('wa')">WhatsApp</button>
<button class="tab tab-tt" id="tabTt" onclick="switchTab('tt')">&#x1f3ae; TikTok (暂停)</button>
</nav>
<span class="conn-status"><span class="dot" id="dot"></span><span id="connText">Conectando...</span></span>
<button class="hist-btn" id="connBtn" onclick="toggleConn()" title="Desconectar (Esc)">Desconectar</button>
<button class="hist-btn" onclick="openConv()">&#x1f4ac; Historial</button>
<div class="status">
<div class="stat"><div class="num" id="sMsg">0</div><div class="label">Mensajes</div></div>
<div class="stat fast"><div class="num" id="sFast">0</div><div class="label">Fast</div></div>
<div class="stat"><div class="num" id="sLlm">0</div><div class="label">LLM</div></div>
<div class="stat voice"><div class="num" id="sVoice">0</div><div class="label">Voz</div></div>
<div class="stat error"><div class="num" id="sErr">0</div><div class="label">Errores</div></div>
</div></div>
<div class="main">
<div id="panelWa" class="panel-wa" style="display:flex">
<div class="log-panel" id="logs"></div>
<div class="sidebar">
<h3>Servicio</h3>
<div class="info-item"><span class="k">API</span><span class="v" id="apiUrl">127.0.0.1:8765</span></div>
<div class="info-item"><span class="k">WhatsApp</span><span class="v" id="waStatus">...</span></div>
<div class="info-item"><span class="k">Inicio</span><span class="v" id="startTime">...</span></div>
<h3 style="margin-top:16px">Atajos</h3>
<div class="info-item"><a href="/channels/status" target="_blank" style="color:#58a6ff">Estado de canales</a></div>
<div class="info-item"><a href="/health" target="_blank" style="color:#58a6ff">Health check</a></div>
<div class="info-item"><a href="#" onclick="openConv();return false" style="color:#58a6ff">Historial de clientes</a></div>
<div class="info-item"><a href="#" onclick="switchTab('tt');return false" style="color:#00f2ea">TikTok 评论与私信</a></div>
<div class="info-item"><a href="#" onclick="openOutreach();return false" style="color:#58a6ff">Outreach 拉客</a></div>
<h3 style="margin-top:16px">Tienda</h3>
<div class="info-item"><span class="k">Horario L-S</span><span class="v">9:00-18:30</span></div>
<div class="info-item"><span class="k">Domingo</span><span class="v">9:30-17:00</span></div>
<div class="info-item"><span class="k">WhatsApp</span><span class="v">0939962405</span></div>
</div></div>
<div id="panelTt" class="panel-tt" style="display:none">
<div class="tt-body">
<h2 style="color:#00f2ea;margin-bottom:8px">&#x1f3ae; TikTok — 每条视频的评论与私信</h2>
<p style="color:#8b949e;font-size:12px;margin-bottom:16px">自动回覆：评论（轮询每个视频） + 私信（需在 TikTok 开发者后台配置 Webhook 并订阅私信事件）</p>
<div class="info-item"><span class="k">账号</span><span class="v" id="ttAccount">—</span></div>
<button class="hist-btn" onclick="loadTikTokData()" style="margin:8px 0;background:#00f2ea22;color:#00f2ea">刷新</button>
<div id="tiktokVideos" style="margin-top:12px"></div>
<h3 style="color:#8b949e;font-size:12px;margin:16px 0 8px">Shop 对话</h3>
<div id="tiktokShop"></div>
<h3 style="color:#8b949e;font-size:12px;margin:16px 0 8px">私信 (DM)</h3>
<div id="tiktokDm"></div>
</div></div>
</div>
<div class="conv-overlay" id="outreachOv" style="display:none"><div class="conv-box" style="max-width:500px">
<div class="conv-top"><h2>Outreach 拉客</h2>
<button class="cbtn" onclick="closeOutreach()" title="Cerrar (Esc)">&times;</button></div>
<div style="padding:16px">
<div id="outreachRestrictWarn" style="display:none;background:#f8514922;border:1px solid #f85149;border-radius:6px;padding:10px;margin-bottom:12px;font-size:12px;color:#f85149">
<strong>⚠ 账户已限制</strong> — 拉客已停止。24-48h 后若确认安全，可点「清除限制」恢复。
<button class="hist-btn" onclick="clearOutreachRestriction()" style="margin-top:8px;font-size:11px">清除限制</button>
</div>
<div class="info-item"><span class="k">Estado</span><span class="v" id="outreachStatus">...</span> <button class="hist-btn" onclick="toggleOutreach()" style="padding:4px 8px;font-size:11px">切换</button></div>
<div class="info-item"><span class="k">模式</span><span class="v" id="outreachMode">chat</span></div>
<div class="info-item"><span class="k">联络人</span><span class="v" id="outreachContacts">0</span></div>
<button class="hist-btn" onclick="loadOutreachContacts()" style="margin:8px 0">刷新联络人</button>
<button class="hist-btn" onclick="runOutreach()" style="margin:8px 0;background:#238636">执行一轮</button>
<p id="outreachMsg" style="font-size:12px;color:#8b949e;margin-top:12px"></p>
</div></div></div>
<div class="conv-overlay" id="convOv" style="display:none"><div class="conv-box">
<div class="conv-top"><h2>&#x1f4ac; Historial</h2>
<input type="text" id="convQ" placeholder="Buscar por numero o nombre..." oninput="filterCust(this.value)">
<button class="cbtn" onclick="closeConv()" title="Cerrar (Esc)">&times;</button></div>
<div class="conv-mid">
<div class="conv-list" id="convList"></div>
<div class="conv-right">
<div class="conv-rh" id="convRH"></div>
<div class="conv-msgs" id="convMsgs"><div class="conv-empty">Selecciona un cliente para ver su historial</div></div>
<div class="conv-ft" id="convFt"></div>
</div></div></div></div>
<script>
const $ = id => document.getElementById(id);
const logs = $('logs');
let autoScroll = true;
logs.addEventListener('scroll', () => {
  autoScroll = logs.scrollTop + logs.clientHeight >= logs.scrollHeight - 40;
});
function addLog(e) {
  const div = document.createElement('div');
  div.className = 'log-entry ' + (e.level||'INFO');
  let badge = '';
  if (e.path) badge = `<span class="path-badge ${e.path}">${e.path.toUpperCase()}</span>`;
  div.innerHTML = `<span class="ts">${e.ts}</span><span class="tag">[${e.level}]</span>${e.text}${badge}`;
  logs.appendChild(div);
  if (logs.children.length > 500) logs.removeChild(logs.firstChild);
  if (autoScroll) logs.scrollTop = logs.scrollHeight;
}
let es;
let _manualDisconnect = false;
let _waAutoReply = true;
let _waConnected = false;
function renderConnUi() {
  const active = _waAutoReply && _waConnected;
  $('dot').className = active ? 'dot on' : 'dot off';
  $('connText').textContent = active ? 'Conectado' : 'Desconectado';
  $('connBtn').textContent = _waAutoReply ? 'Desconectar' : 'Conectar';
  $('connBtn').title = _waAutoReply ? 'Desconectar (Esc)' : 'Conectar (Esc)';
}
function applyAutoReplyUi(enabled) {
  _waAutoReply = !!enabled;
  renderConnUi();
}
function applyConnectedUi(connected) {
  _waConnected = !!connected;
  renderConnUi();
}
async function refreshBridgeStatus() {
  try {
    const r = await fetch('/bridge/status');
    const s = await r.json();
    if (s && s.ok) {
      applyConnectedUi(!!s.connected);
      applyAutoReplyUi(!!s.auto_reply);
      return;
    }
  } catch (e) {}
}
async function toggleConn() {
  const target = !_waAutoReply;
  try {
    const r = await fetch('/bridge/auto-reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: target })
    });
    const s = await r.json();
    if (s && typeof s.enabled === 'boolean') {
      applyAutoReplyUi(s.enabled);
    }
  } catch (e) {}
}
function connect() {
  _manualDisconnect = false;
  es = new EventSource('/dashboard/events');
  es.onmessage = ev => { try { addLog(JSON.parse(ev.data)); } catch(e){} };
  es.onerror = () => {
    if (_manualDisconnect) return;
    $('dot').className = 'dot off';
    $('connText').textContent = 'Reconectando...';
    es.close();
    setTimeout(connect, 3000);
  };
}
function disconnect() {
  _manualDisconnect = true;
  if (es) { es.close(); es = null; }
  $('dot').className = 'dot off';
  $('connText').textContent = 'Desconectado';
}
connect();
refreshBridgeStatus();
setInterval(refreshBridgeStatus, 5000);
async function loadStats() {
  try {
    const r = await fetch('/dashboard/stats');
    const s = await r.json();
    $('sMsg').textContent = s.messages||0;
    $('sFast').textContent = s.fast||0;
    $('sLlm').textContent = s.llm||0;
    $('sVoice').textContent = s.voice||0;
    $('sErr').textContent = s.errors||0;
    if (s.start_time) $('startTime').textContent = s.start_time.slice(11,19);
  } catch(e){}
}
loadStats(); setInterval(loadStats, 10000);
fetch('/channels/status').then(r=>r.json()).then(s=>{
  const wa = s.channels?.whatsapp;
  $('waStatus').textContent = wa?.enabled ? 'Activo' : 'Inactivo';
}).catch(()=>{});
let _allCust=[];
function escH(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML}
function linkify(s){
  if(!s||typeof s!=='string')return '';
  const escaped=escH(s);
  const urlRe=/(https?:\/\/[^\s<>"'`]+)/g;
  return escaped.replace(urlRe,m=>`<a href="${escH(m)}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:underline;cursor:pointer">${m}</a>`);
}
function openConv(){
  $('convOv').style.display='flex';
  $('convQ').value='';
  $('convMsgs').innerHTML='<div class="conv-empty">Cargando clientes...</div>';
  $('convRH').style.display='none';
  $('convFt').style.display='none';
  fetch('/dashboard/customers').then(r=>r.json()).then(d=>{
    _allCust=d;renderCust(d);
    if(!d.length) $('convMsgs').innerHTML='<div class="conv-empty">No hay conversaciones guardadas</div>';
    else $('convMsgs').innerHTML='<div class="conv-empty">Selecciona un cliente para ver su historial</div>';
  }).catch(()=>{$('convMsgs').innerHTML='<div class="conv-empty">Error al cargar</div>'});
}
function closeConv(){$('convOv').style.display='none'}
function switchTab(t){
  const wa=$('panelWa'),tt=$('panelTt'),tabWa=$('tabWa'),tabTt=$('tabTt');
  if(t==='tt'){
    wa.style.display='none';tt.style.display='flex';
    tabWa.classList.remove('active');tabTt.classList.add('active');
    loadTikTokData();
  }else{
    wa.style.display='flex';tt.style.display='none';
    tabWa.classList.add('active');tabTt.classList.remove('active');
  }
}
async function loadTikTokData(){
  const vEl=$('tiktokVideos'), sEl=$('tiktokShop'), dEl=$('tiktokDm');
  if(!vEl){return}
  vEl.innerHTML='<div class="conv-empty">Cargando...</div>';
  sEl.innerHTML=''; dEl.innerHTML='';
  try{
    const [vRes,aRes]=await Promise.all([fetch('/dashboard/tiktok/videos'),fetch('/dashboard/tiktok/activity')]);
    const vData=await vRes.json();
    const aData=await aRes.json();
    $('ttAccount').textContent=vData.tiktok_account||'—';
    const byVideo=aData.comments_by_video||{};
    if(!vData.videos||!vData.videos.length){
      vEl.innerHTML='<div class="conv-empty">在 config/tiktok_config.json 中配置 monitored_video_ids 后可在此查看评论。<br><span style="color:#8b949e;font-size:12px">示例：从视频链接 .../video/7123456789 取 ID，填为 [\"7123456789\"]</span></div>';
    }else{
      const safeId=s=>String(s).replace(/[^a-zA-Z0-9_-]/g,'_');
    vEl.innerHTML=vData.videos.map(v=>{
        const list=byVideo[v.video_id]||[];
        const pre=list.length?' — '+list.length+' 条':('');
        return '<div style="margin-bottom:16px;border:1px solid #30363d;border-radius:8px;padding:12px;background:#161b22"><b style="color:#00f2ea">视频 '+escH(v.video_id)+'</b><span style="color:#8b949e;font-size:12px">'+pre+'</span><div id="ttvid_'+safeId(v.video_id)+'" style="margin-top:8px"></div></div>';
      }).join('');
      vData.videos.forEach(v=>{
        const list=byVideo[v.video_id]||[];
        const div=document.getElementById('ttvid_'+safeId(v.video_id));
        if(!div)return;
        if(!list.length){div.innerHTML='<div class="conv-empty">暂无评论</div>';return;}
        div.innerHTML=list.map(t=>{
          const msgs=(t.messages||[]).map(m=>'<div class="bubble '+(m.role==='assistant'?'assistant':'customer')+'"><b>'+(m.role==='customer'?'用户':'回复')+'</b><br>'+linkify(m.content)+'<div class="bts">'+escH(m.ts||'')+'</div></div>').join('');
          return '<div style="margin-bottom:8px;padding:8px;background:#0d1117;border-radius:6px">'+msgs+'</div>';
        }).join('');
      });
    }
    (aData.shop||[]).forEach(t=>{
      const msgs=(t.messages||[]).map(m=>'<div class="bubble '+(m.role==='assistant'?'assistant':'customer')+'">'+linkify(m.content)+'<div class="bts">'+escH(m.ts||'')+'</div></div>').join('');
      sEl.innerHTML+='<div style="margin-bottom:8px;padding:8px;background:#161b22;border-radius:6px">'+msgs+'</div>';
    });
    if(!aData.shop||!aData.shop.length)sEl.innerHTML='<div class="conv-empty">暂无 Shop 对话</div>';
    (aData.dm||[]).forEach(t=>{
      const msgs=(t.messages||[]).map(m=>'<div class="bubble '+(m.role==='assistant'?'assistant':'customer')+'">'+linkify(m.content)+'<div class="bts">'+escH(m.ts||'')+'</div></div>').join('');
      dEl.innerHTML+='<div style="margin-bottom:8px;padding:8px;background:#161b22;border-radius:6px">'+msgs+'</div>';
    });
    if(!aData.dm||!aData.dm.length)dEl.innerHTML='<div class="conv-empty">暂无私信</div>';
  }catch(e){
    vEl.innerHTML='<div class="conv-empty">Error: '+escH(e.message)+'</div>';
  }
}
function openOutreach(){
  $('outreachOv').style.display='flex';
  loadOutreachStatus(); loadOutreachContacts();
}
function closeOutreach(){$('outreachOv').style.display='none'}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeConv();closeOutreach()}});
async function loadOutreachStatus(){
  try{
    const r=await fetch('/outreach/status');
    const s=await r.json();
    $('outreachStatus').textContent=s.enabled?'开启':'关闭';
    $('outreachMode').textContent=s.mode==='chat'?(s.hi_only?'简短问候诱导回覆':'打招呼拉家常'):'产品推荐';
    $('outreachRestrictWarn').style.display=s.restricted?'block':'none';
  }catch(e){$('outreachStatus').textContent='Error'}
}
async function clearOutreachRestriction(){
  try{
    const r=await fetch('/outreach/clear-restriction',{method:'POST'});
    const d=await r.json();
    if(d.ok){ loadOutreachStatus(); $('outreachMsg').textContent='限制已清除'; }
    else $('outreachMsg').textContent=d.error||'清除失败';
  }catch(e){$('outreachMsg').textContent='Error: '+e.message}
}
async function toggleOutreach(){
  try{
    const r=await fetch('/outreach/toggle',{method:'POST'});
    const d=await r.json();
    if(d.error)throw new Error(d.error);
    loadOutreachStatus();
  }catch(e){$('outreachMsg').textContent='Error: '+e.message}
}
async function loadOutreachContacts(){
  try{
    const r=await fetch('/outreach/contacts');
    const d=await r.json();
    $('outreachContacts').textContent=d.count||0;
  }catch(e){$('outreachContacts').textContent='-'}
}
async function runOutreach(){
  $('outreachMsg').textContent='执行中...';
  try{
    const r=await fetch('/outreach/run',{method:'POST'});
    const d=await r.json();
    let msg=d.sent?`已发送 → ${d.contact_id} (${d.product_name})`:(d.reason||d.error||'未发送');
    if(d.reason==='account_restricted'){ msg='⚠ 账户已限制，拉客已停止'; loadOutreachStatus(); }
    $('outreachMsg').textContent=msg;
    loadOutreachContacts();
  }catch(e){$('outreachMsg').textContent='Error: '+e.message}
}
function renderCust(list){
  const el=$('convList');
  if(!list.length){el.innerHTML='<div class="conv-empty">Sin resultados</div>';return}
  el.innerHTML=list.map(c=>{
    const ts=c.last_ts?(c.last_ts.slice(0,16)):'';
    const title=c.name?escH(c.name)+' ('+escH(c.id)+')':escH(c.id);
    return `<div class="conv-ci" data-id="${escH(c.id)}" onclick="loadConv('${escH(c.id)}')">
      <div class="cn">${title}</div>
      <div class="cm">${c.count} msgs &middot; ${escH(ts)}</div>
      <div class="cl">${escH(c.last_msg)}</div></div>`}).join('');
}
function filterCust(q){
  q=q.toLowerCase();
  renderCust(q?_allCust.filter(c=>c.id.includes(q)||(c.name&&c.name.toLowerCase().includes(q))):_allCust);
}
function loadConv(cid){
  document.querySelectorAll('.conv-ci').forEach(el=>el.classList.toggle('active',el.dataset.id===cid));
  const rh=$('convRH'),msgs=$('convMsgs'),ft=$('convFt');
  const cust=_allCust.find(c=>c.id===cid);
  rh.style.display='block';rh.textContent=cust&&cust.name?escH(cust.name)+' ('+escH(cid)+')':escH(cid);
  msgs.innerHTML='<div class="conv-empty">Cargando...</div>';
  ft.style.display='none';
  fetch('/dashboard/conversation/'+encodeURIComponent(cid)).then(r=>r.json()).then(d=>{
    if(!d.length){msgs.innerHTML='<div class="conv-empty">Sin mensajes</div>';return}
    msgs.innerHTML=d.map(m=>{
      const role=m.role==='assistant'?'assistant':'customer';
      const isManual=m.channel&&m.channel.indexOf('manual')===0;
      const label=role==='assistant'?(isManual?'Agente':'Bot'):'Cliente';
      const ts=m.ts?(m.ts.slice(0,16)):'';
      return `<div class="bubble ${role}"><b>${label}</b><br>${linkify(m.content)}<div class="bts">${escH(ts)} &middot; ${escH(m.channel||'')}</div></div>`
    }).join('');
    msgs.scrollTop=msgs.scrollHeight;
    ft.style.display='flex';
    ft.innerHTML='Total: '+d.length+' mensajes &middot; Primera: '+(d[0].ts||'-').slice(0,16)+' &middot; Ultima: '+(d[d.length-1].ts||'-').slice(0,16);
  }).catch(()=>{msgs.innerHTML='<div class="conv-empty">Error al cargar</div>'});
}
</script></body></html>"""

_FAST_MARKERS = [
    "Carolina", "Somos de Guayaquil", "hacemos env",
    "no lo tengo a mano", "Nuestro horario",
    "Muchas gracias amiga", "Gracias por sus palabras",
    "Puede escribirnos", "Por el momento solo",
    "El env\u00edo es aproximadamente", "Aceptamos transferencia",
    "manejamos precios", "Jaja",
    "con gusto le ayudo", "para ayudarle",
    "producto le interesa", "De nada amiga", "Que tenga buen",
    "Claro amiga", "le puedo ayudar",
    "Novedades Cristy", "Con gusto",
    "Le interesa", "le interesa",
    "No pude identificar", "No pude procesar",
]


def _detect_path(reply: str) -> str:
    if ("ventax.pages.dev" in reply and reply.count("\n") >= 2):
        return "fast"
    if any(m in reply for m in _FAST_MARKERS):
        return "fast"
    return "llm"


def _get_conv_db():
    """获取对话记忆数据库连接（只读）"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "config", "conversations.db"
    )
    db_path = os.path.normpath(db_path)
    if not os.path.isfile(db_path):
        return None
    try:
        return sqlite3.connect(db_path, timeout=5)
    except Exception:
        return None


def _get_html_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base, "..", "..", "test ocr", "ventax_customer_debug.html"))


def _register_channels(app):
    """注册 WhatsApp / TikTok 频道 Blueprint"""
    channels_loaded = []
    try:
        from whatsapp_channel import create_blueprint as wa_bp
        bp = wa_bp()
        if bp:
            app.register_blueprint(bp)
            channels_loaded.append("WhatsApp")
    except Exception as e:
        logger.warning(f"WhatsApp 频道加载失败: {e}")

    # NOTE: 24x7 服务下若设置 SKIP_TIKTOK=1 则暂停 TikTok 频道
    if os.environ.get("SKIP_TIKTOK", "").strip() in ("1", "true", "yes"):
        logger.info("TikTok 已暂停 (SKIP_TIKTOK)")
    else:
        try:
            from tiktok_channel import create_blueprint as tt_bp
            bp = tt_bp()
            if bp:
                app.register_blueprint(bp)
                channels_loaded.append("TikTok")
        except Exception as e:
            logger.warning(f"TikTok 频道加载失败: {e}")

    if channels_loaded:
        logger.info(f"已加载频道: {', '.join(channels_loaded)}")
    return channels_loaded


def create_app():
    """创建 Flask app — 支持 gunicorn 部署"""
    try:
        from flask import Flask, request, jsonify, send_file
        from flask_cors import CORS
    except ImportError:
        print("pip install flask flask-cors")
        return None

    app = Flask(__name__)
    CORS(app)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB

    # 注册多频道 webhook
    loaded_channels = _register_channels(app)

    @app.route("/")
    def index():
        p = _get_html_path()
        if os.path.isfile(p):
            r = send_file(p)
            r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return r
        return "<h1>VentaX Customer Bot API</h1><p>POST /chat or /chat/image</p>"

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "channels": loaded_channels})

    @app.route("/channels/status", methods=["GET"])
    def channels_status():
        """所有频道状态总览"""
        status = {"server": "running", "channels": {}}
        # WhatsApp
        try:
            from whatsapp_channel import _load_config as wa_cfg, _get as wa_get
            cfg = wa_cfg()
            status["channels"]["whatsapp"] = {
                "enabled": cfg.get("enabled", False),
                "configured": bool(wa_get("access_token") and wa_get("phone_number_id")),
                "webhook": "/webhook/whatsapp",
            }
        except Exception:
            status["channels"]["whatsapp"] = {"enabled": False, "error": "module not loaded"}
        # TikTok
        try:
            from tiktok_channel import _load_config as tt_cfg
            cfg = tt_cfg()
            status["channels"]["tiktok"] = {
                "enabled": cfg.get("enabled", False),
                "mode": cfg.get("mode", "disabled"),
                "webhook": "/webhook/tiktok",
            }
        except Exception:
            status["channels"]["tiktok"] = {"enabled": False, "error": "module not loaded"}
        return jsonify(status)

    @app.route("/chat", methods=["POST"])
    def chat_api():
        try:
            data = request.get_json() or {}
            msg = data.get("message", "").strip()
            if not msg:
                return jsonify({"error": "message vacío"}), 400
            cid = data.get("customer_id", "").strip()
            channel = data.get("channel", "whatsapp")
            cname = (data.get("customer_name") or "").strip()
            history = []
            if cid:
                if cname:
                    save_customer_name(cid, cname)
                history = get_history(cid)
                save_message(cid, "customer", msg, channel)
            reply = chat(msg, history=history if history else None)
            if cid:
                save_message(cid, "assistant", reply, channel)
            path = _detect_path(reply)
            _stats["messages"] += 1
            _stats["fast" if path == "fast" else "llm"] += 1
            _push_log("MSG", f"[{cid or 'anon'}] {msg}", {"type": "customer"})
            _push_log("REPLY", f"[→ {cid or 'anon'}] {reply}", {"type": "bot", "path": path})
            return jsonify({"reply": reply, "path": path})
        except Exception as e:
            _stats["errors"] += 1
            _push_log("ERROR", str(e)[:300])
            return jsonify({"error": str(e)}), 500

    @app.route("/chat/image", methods=["POST"])
    def chat_image_api():
        """图片识别 API — 接受 JSON(base64) 或 multipart/form-data"""
        try:
            img_b64 = None
            msg = ""
            mime = "image/jpeg"
            cid = ""
            cname = ""
            channel = "whatsapp"
            if request.content_type and "multipart" in request.content_type:
                f = request.files.get("image")
                if not f:
                    return jsonify({"error": "no image file"}), 400
                img_b64 = base64.b64encode(f.read()).decode("ascii")
                mime = f.content_type or "image/jpeg"
                msg = request.form.get("message", "")
                cid = request.form.get("customer_id", "").strip()
                cname = (request.form.get("customer_name") or "").strip()
            else:
                data = request.get_json() or {}
                img_b64 = data.get("image_base64", "")
                msg = data.get("message", "")
                mime = data.get("mime_type", "image/jpeg")
                cid = data.get("customer_id", "").strip()
                cname = (data.get("customer_name") or "").strip()
                channel = data.get("channel", "whatsapp")
            if not img_b64:
                return jsonify({"error": "no image data"}), 400
            if cid:
                if cname:
                    save_customer_name(cid, cname)
                save_message(cid, "customer", msg or "[imagen]", channel)
            reply = chat_with_image(msg, img_b64, mime)
            if cid:
                save_message(cid, "assistant", reply, channel)
            return jsonify({"reply": reply, "path": "vision"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/chat/voice", methods=["POST"])
    def chat_voice_api():
        """语音消息 API — Gemini 转录 + 回复"""
        try:
            data = request.get_json() or {}
            audio_b64 = data.get("audio_base64", "")
            if not audio_b64:
                return jsonify({"error": "no audio data"}), 400
            mime = data.get("mime_type", "audio/ogg")
            cid = data.get("customer_id", "").strip()
            cname = (data.get("customer_name") or "").strip()
            channel = data.get("channel", "whatsapp")
            history = []
            if cid:
                if cname:
                    save_customer_name(cid, cname)
                history = get_history(cid)
            reply, transcription = chat_with_voice(audio_b64, mime, history=history if history else None)
            # 语音转录成功后，用转录文本走 chat() 快速路径（含完整FAQ匹配）
            # 如果快速路径命中，用其更准确的回答替代 LLM 回复
            if transcription and len(transcription) > 2:
                fast_reply = chat(transcription, use_fast_path=True, history=history if history else None)
                if fast_reply and fast_reply != reply:
                    reply = fast_reply
            if cid:
                save_message(cid, "customer", transcription or "[audio]", channel)
                save_message(cid, "assistant", reply, channel)
            _stats["messages"] += 1
            _stats["voice"] += 1
            _push_log("VOICE", f"[{cid or 'anon'}] {transcription}", {"type": "customer"})
            _push_log("REPLY", f"[→ {cid or 'anon'}] {reply}", {"type": "bot", "path": "voice"})
            return jsonify({"reply": reply, "transcription": transcription, "path": "voice"})
        except Exception as e:
            _stats["errors"] += 1
            _push_log("ERROR", f"Voice: {str(e)[:300]}")
            return jsonify({"error": str(e)}), 500

    @app.route("/memory/cleanup", methods=["POST"])
    def memory_cleanup():
        """清理旧对话记录"""
        try:
            days = int((request.get_json() or {}).get("days", 60))
            deleted = cleanup_old(days)
            return jsonify({"deleted": deleted, "older_than_days": days})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/memory/manual-reply", methods=["POST"])
    def memory_manual_reply():
        """记录人工手动输入的客服回复"""
        try:
            data = request.get_json() or {}
            cid = (data.get("customer_id") or "").strip()
            content = (data.get("content") or "").strip()
            channel = (data.get("channel") or "").strip() or "whatsapp_manual"
            if not cid or not content:
                return jsonify({"ok": False, "error": "customer_id 或 content 不能为空"}), 400
            save_message(cid, "assistant", content, channel)
            _stats["messages"] += 1
            _push_log(
                "MANUAL",
                f"[→ {cid}] {content}",
                {"type": "manual", "channel": channel},
            )
            return jsonify({"ok": True})
        except Exception as e:
            _stats["errors"] += 1
            _push_log("ERROR", f"manual-reply: {str(e)[:300]}")
            return jsonify({"ok": False, "error": str(e)}), 500

    # ── Dashboard 网页仪表板 ─────────────────────────────
    @app.route("/dashboard")
    def dashboard():
        from flask import Response
        return Response(_DASHBOARD_HTML, content_type="text/html; charset=utf-8")

    @app.route("/dashboard/events")
    def dashboard_events():
        """SSE 实时日志流"""
        from flask import Response
        def stream():
            q = queue.Queue(maxsize=100)
            _log_subscribers.append(q)
            try:
                # 先发送历史日志
                for entry in list(_log_history):
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        entry = q.get(timeout=30)
                        yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        yield ": heartbeat\n\n"
            except GeneratorExit:
                pass
            finally:
                try:
                    _log_subscribers.remove(q)
                except ValueError:
                    pass
        return Response(stream(), content_type="text/event-stream",
                       headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/dashboard/stats")
    def dashboard_stats():
        return jsonify(_stats)

    @app.route("/dashboard/bridge-log", methods=["POST"])
    def dashboard_bridge_log():
        """接收 WhatsApp bridge 转发的日志"""
        try:
            data = request.get_json() or {}
            level = data.get("level", "INFO")
            text = data.get("text", "")[:800]
            if text:
                _push_log(level, f"[WA] {text}", {"type": "bridge"})
            return "", 204
        except Exception:
            return "", 204

    # ── WhatsApp Bridge 控制接口 ────────────────────────
    @app.route("/bridge/status")
    def bridge_status():
        """读取 WhatsApp bridge 状态"""
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8766/status", timeout=2) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
            return data, 200, {"Content-Type": "application/json; charset=utf-8"}
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    @app.route("/bridge/auto-reply", methods=["POST"])
    def bridge_auto_reply():
        """切换 WhatsApp 自动回复（转发到 bridge）"""
        try:
            import urllib.request
            payload = (request.get_json() or {})
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:8766/auto-reply",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.read().decode("utf-8", errors="ignore"), 200, {"Content-Type": "application/json; charset=utf-8"}
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    # ── 对话历史查看 API ───────────────────────────────
    @app.route("/dashboard/customers")
    def dashboard_customers():
        """客户列表（按最近活跃排序，含用户名称）"""
        conn = _get_conv_db()
        if not conn:
            return jsonify([])
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS customer_names (
                    customer_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            rows = conn.execute("""
                SELECT c1.customer_id, COUNT(*) as cnt,
                       MAX(c1.ts) as last_ts, MIN(c1.ts) as first_ts,
                       (SELECT content FROM conversations c2
                        WHERE c2.customer_id = c1.customer_id
                        ORDER BY c2.id DESC LIMIT 1) as last_msg,
                       n.display_name
                FROM conversations c1
                LEFT JOIN customer_names n ON n.customer_id = c1.customer_id
                GROUP BY c1.customer_id
                ORDER BY MAX(c1.ts) DESC
                LIMIT 200
            """).fetchall()
            return jsonify([{
                "id": r[0], "count": r[1],
                "last_ts": r[2], "first_ts": r[3],
                "last_msg": (r[4] or "")[:100],
                "name": r[5] or ""
            } for r in rows])
        except Exception:
            return jsonify([])
        finally:
            conn.close()

    @app.route("/dashboard/conversation/<cid>")
    def dashboard_conversation(cid):
        """获取某客户的完整对话"""
        conn = _get_conv_db()
        if not conn:
            return jsonify([])
        try:
            rows = conn.execute(
                "SELECT role, content, channel, ts FROM conversations WHERE customer_id=? ORDER BY id ASC LIMIT 500",
                (cid,)
            ).fetchall()
            return jsonify([{"role": r[0], "content": r[1], "channel": r[2], "ts": r[3]} for r in rows])
        except Exception:
            return jsonify([])
        finally:
            conn.close()

    # ── TikTok 仪表板：按视频的评论与私信 ─────────────────────
    @app.route("/dashboard/tiktok/videos")
    def dashboard_tiktok_videos():
        """TikTok 监控视频列表（来自配置）+ 每视频评论数"""
        try:
            from tiktok_channel import _load_config as _tt_cfg
            cfg = _tt_cfg()
            video_ids = list(cfg.get("monitored_video_ids") or [])
            conn = _get_conv_db()
            counts = {}
            if conn:
                try:
                    for vid in video_ids:
                        prefix = f"tiktok_video:{vid}:comment:"
                        row = conn.execute(
                            "SELECT COUNT(DISTINCT customer_id) FROM conversations WHERE channel='tiktok_comment' AND customer_id LIKE ?",
                            (prefix + "%",)
                        ).fetchone()
                        counts[vid] = row[0] if row else 0
                except Exception:
                    pass
                finally:
                    conn.close()
            return jsonify({
                "videos": [{"video_id": vid, "comment_count": counts.get(vid, 0)} for vid in video_ids],
                "tiktok_account": cfg.get("tiktok_account", ""),
            })
        except Exception as e:
            logger.warning("dashboard_tiktok_videos: %s", e)
            return jsonify({"videos": [], "tiktok_account": ""})

    @app.route("/dashboard/tiktok/activity")
    def dashboard_tiktok_activity():
        """按视频聚合的 TikTok 评论 + Shop 对话 + 私信，供单页展示"""
        conn = _get_conv_db()
        if not conn:
            return jsonify({"comments_by_video": {}, "shop": [], "dm": []})
        try:
            rows = conn.execute(
                """SELECT customer_id, role, content, ts FROM conversations
                   WHERE channel IN ('tiktok_comment','tiktok_shop','tiktok_dm') ORDER BY id ASC LIMIT 2000"""
            ).fetchall()
            # 按 customer_id 分组，每组即一条会话（评论/Shop/DM）
            threads = {}
            for customer_id, role, content, ts in rows:
                ts_str = ts[:19] if ts and len(ts) >= 19 else (ts or "")
                if customer_id not in threads:
                    threads[customer_id] = {"customer_id": customer_id, "messages": [], "ts": ts_str}
                threads[customer_id]["messages"].append({"role": role, "content": content or "", "ts": ts_str})
                if ts:
                    threads[customer_id]["ts"] = max(threads[customer_id]["ts"], ts_str)
            # 解析 customer_id 归类到 comments_by_video / shop / dm
            comments_by_video = {}
            shop_list = []
            dm_list = []
            for cid, data in threads.items():
                if cid.startswith("tiktok_video:"):
                    parts = cid.split(":", 3)
                    if len(parts) >= 4:
                        video_id = parts[1]
                        if video_id not in comments_by_video:
                            comments_by_video[video_id] = []
                        comments_by_video[video_id].append(data)
                elif cid.startswith("tiktok_shop:"):
                    shop_list.append(data)
                elif cid.startswith("tiktok_dm:"):
                    dm_list.append(data)
            return jsonify({
                "comments_by_video": comments_by_video,
                "shop": shop_list,
                "dm": dm_list,
            })
        except Exception as e:
            logger.warning("dashboard_tiktok_activity: %s", e)
            return jsonify({"comments_by_video": {}, "shop": [], "dm": []})
        finally:
            conn.close()

    # ── 拉客 Outreach API ─────────────────────────────────────
    @app.route("/outreach/contacts")
    def outreach_contacts():
        """获取 WhatsApp 联络人（已过滤 @ 结尾）"""
        try:
            from outreach_scheduler import get_contacts
            contacts = get_contacts()
            return jsonify({"ok": True, "count": len(contacts), "contacts": contacts})
        except Exception as e:
            logger.warning(f"outreach contacts: {e}")
            return jsonify({"ok": False, "error": str(e), "contacts": []})

    @app.route("/outreach/status")
    def outreach_status():
        """拉客配置状态"""
        try:
            from outreach_scheduler import _get_config, _get_products_catalog, get_restriction_status
            cfg = _get_config()
            products = _get_products_catalog()
            restriction = get_restriction_status()
            return jsonify({
                "enabled": cfg.get("enabled", False),
                "mode": cfg.get("mode", "product"),
                "hi_only": cfg.get("hi_only", True),
                "products_per_day": cfg.get("products_per_day", 2),
                "chat_per_day": cfg.get("chat_per_day", 100),
                "products_count": len(products),
                "send_window": f"{cfg.get('send_window_start','09:00')}-{cfg.get('send_window_end','20:00')}",
                "restricted": restriction.get("restricted", False),
                "restriction_reason": restriction.get("reason", ""),
            })
        except Exception as e:
            return jsonify({"enabled": False, "error": str(e)})

    @app.route("/outreach/clear-restriction", methods=["POST"])
    def outreach_clear_restriction():
        """手动清除限制标记（24-48h 后确认安全可调用）"""
        try:
            from outreach_scheduler import clear_restriction
            ok = clear_restriction()
            return jsonify({"ok": ok})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    @app.route("/outreach/run", methods=["POST"])
    def outreach_run():
        """执行一轮拉客"""
        try:
            from outreach_scheduler import run_one_round
            result = run_one_round()
            return jsonify(result)
        except Exception as e:
            logger.warning(f"outreach run: {e}")
            return jsonify({"sent": False, "error": str(e)})

    @app.route("/outreach/toggle", methods=["POST"])
    def outreach_toggle():
        """切换拉客开关"""
        try:
            cfg_path = os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "config", "outreach_config.json"
            ))
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["enabled"] = not cfg.get("enabled", False)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return jsonify({"enabled": cfg["enabled"]})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


# Render/gunicorn 入口：gunicorn ventax_customer_bot_server:app
app = create_app()


def main():
    if app is None:
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    port = int(os.environ.get("PORT", 8765))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"VentaX 多频道客服: http://{host}:{port}/")
    print("API: POST /chat (文字) | POST /chat/image (图片)")
    print("Webhook: /webhook/whatsapp | /webhook/tiktok")
    print("状态: GET /channels/status")
    print("修改代码后需重启。Esc/Ctrl+C 退出")
    # NOTE: 24x7 服务下 TikTok 评论自动回覆 — 若 SKIP_TIKTOK 则跳过
    if os.environ.get("SKIP_TIKTOK", "").strip() in ("1", "true", "yes"):
        logger.info("TikTok 评论轮询已暂停 (SKIP_TIKTOK)")
    else:
        try:
            from tiktok_channel import _load_config as _tt_cfg
            from tiktok_channel import CommentPoller
            tt_cfg = _tt_cfg()
            video_ids = list(tt_cfg.get("monitored_video_ids") or [])
            if video_ids and tt_cfg.get("auto_reply_comments"):
                interval = int(tt_cfg.get("comment_poll_interval") or 60)
                _poller = CommentPoller(video_ids=video_ids, interval=interval)
                _poller.start()
                logger.info("TikTok 评论轮询已启动: %d 个视频, 间隔 %ds", len(video_ids), interval)
            elif tt_cfg.get("auto_reply_comments") and not video_ids:
                logger.info("TikTok 评论轮询未启动: 请在 config/tiktok_config.json 中配置 monitored_video_ids")
        except Exception as e:
            logger.debug("TikTok 评论轮询未启动: %s", e)
    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VentaX 专用客服机器人 — 极简架构
单一职责：引导客户到 ventax.pages.dev 浏览产品
可扩展为多机器人协同中的一员
LLM 路径按高/低意图使用 distilled 模板
"""
import os
import json
import re
import random
import time
import unicodedata
import urllib.request
import urllib.error
from urllib.parse import quote
import socket
from datetime import datetime, timezone, timedelta


def _normalize(text: str) -> str:
    """去除重音 + 小写 + 展开缩略语 + 压缩重复字符 + 西语语音容错"""
    nfkd = unicodedata.normalize('NFKD', text.lower())
    t = ''.join(c for c in nfkd if not unicodedata.combining(c))
    t = _expand_slang(t)
    # 压缩 3+ 重复字符 → 1: "holaaaa"→"hola", "siiiii"→"si", "precioooo"→"precio"
    t = re.sub(r'(.)\1{2,}', r'\1', t)
    # 厄瓜多尔/拉美西语语音容错 — 映射常见发音混淆到标准拼写
    t = _spanish_phonetic(t)
    return t


# ── 西班牙语语音容错 ──────────────────────────────────────
# 厄瓜多尔/拉美 seseo + yeísmo + b/v 混淆 + h 省略
# 对输入和触发器都适用（触发器在代码中已用标准拼写，此函数将输入映射到标准形式）
_PHONETIC_RE = [
    # b/v 混淆 → 统一为 v（因触发器用 "vende/envio" 等标准拼写）
    (re.compile(r'\bb([aeiou])'), r'v\1'),       # "bende"→"vende" (词首 ba/be/bi/bo/bu)
    (re.compile(r'nb'), 'nv'),                     # "enbio"→"envio", "enbiar"→"enviar"
    # seseo: z→s 已在某些场景有用，但触发器用 "precio" 不用 "presio"
    # 所以反向映射：s→c before e/i（"presio"→"precio"）
    (re.compile(r'si([oa])'), r'cio'),             # "presio"→"precio", "presios"→"precios"
    (re.compile(r'se([^r\s])'), r'ce\1'),          # "serca"→"cerca", "serrado"→"cerrado"
    # h 省略 → 恢复常见词首 h
    (re.compile(r'\b(a)(cer|cen|ce|cemos|go|gan|sta|cia)'), r'h\1\2'),  # "acer"→"hacer", "asta"→"hasta" handled by slang
    (re.compile(r'\borario'), 'horario'),           # "orario"→"horario"
    (re.compile(r'\bora\b'), 'hora'),               # "ora"→"hora"
    (re.compile(r'\boras\b'), 'horas'),             # "oras"→"horas"
    # 常见键盘相邻键误触
    (re.compile(r'\bcusnto'), 'cuanto'),            # s 与 a 相邻
    (re.compile(r'\bproductp'), 'producto'),        # o 与 p 相邻
    (re.compile(r'\bprecuo'), 'precio'),            # i 与 u 相邻
    (re.compile(r'\btieme'), 'tiene'),              # n 与 m 相邻
    (re.compile(r'\btiene[sn]?\b'), lambda m: m.group()),  # 保护正确拼写不被后续规则破坏
]


def _spanish_phonetic(text: str) -> str:
    """将常见西语发音变体/打字错误映射回标准拼写，使触发器能匹配"""
    for pat, repl in _PHONETIC_RE:
        text = pat.sub(repl, text)
    return text


# 拉美/厄瓜多尔社交媒体缩略语 → 标准西班牙语
# 使用 \b 词边界正则匹配，避免替换词内子串（如 "atiende" 中的 "tien"）
# 按缩写长度降序排列，长词优先匹配
_SLANG_PAIRS = [
    # 多字符短语
    ("xfavor", "por favor"),
    ("xfa",    "por favor"),
    ("xf",     "por favor"),
    # porque 系
    ("xq",     "porque"),
    ("xk",     "porque"),
    ("pq",     "porque"),
    ("pk",     "porque"),
    # donde
    ("dnde",   "donde"),
    ("dond",   "donde"),
    ("dnd",    "donde"),
    # cuanto/a
    ("cnto",   "cuanto"),
    ("qnto",   "cuanto"),
    ("qnta",   "cuanta"),
    # tambien
    ("tmb",    "tambien"),
    ("tb",     "tambien"),
    # saludos
    ("bnos",   "buenos"),
    ("bns",    "buenas"),
    # gracias
    ("grcias", "gracias"),
    ("grax",   "gracias"),
    ("grc",    "gracias"),
    # necesito
    ("ncsito", "necesito"),
    ("ncesito","necesito"),
    # quiero/quieres
    ("kiero",  "quiero"),
    ("qiero",  "quiero"),
    ("qero",   "quiero"),
    ("kieres", "quieres"),
    ("qieres", "quieres"),
    # tiene/tienes (solo como palabra independiente)
    ("tien",   "tiene"),
    ("tiens",  "tienes"),
    # producto
    ("prodctos","productos"),
    ("prodcto","producto"),
    ("prducto","producto"),
    ("pdcto",  "producto"),
    # descuento
    ("dscount","descuento"),
    ("dscto",  "descuento"),
    # precio
    ("precx",  "precio"),
    ("prcio",  "precio"),
    # direccion
    ("direcc", "direccion"),
    ("direc",  "direccion"),
    # informacion
    ("msj",    "mensaje"),
    ("msg",    "mensaje"),
    # envio
    ("envx",   "envio"),
    # hora
    ("hra",    "hora"),
    ("hrs",    "horas"),
    # hasta
    ("hsta",   "hasta"),
    # aqui
    ("aqi",    "aqui"),
    ("aki",    "aqui"),
    # hacer/hacen
    ("asen",   "hacen"),
    ("acer",   "hacer"),
    ("aser",   "hacer"),
    # estar
    ("tamos",  "estamos"),
    # ── 常见打字错误/字母颠倒 ──
    # producto 变体
    ("prodcuto","producto"),
    ("porducto","producto"),
    ("prductos","productos"),
    ("producot","producto"),
    ("rpodcuto","producto"),
    ("porductos","productos"),
    # precio 变体
    ("preico",  "precio"),
    ("prceo",   "precio"),
    ("preicios","precios"),
    # envio 变体
    ("envoi",   "envio"),
    ("envios",  "envios"),
    ("emvio",   "envio"),
    ("enivo",   "envio"),
    ("eenvio",  "envio"),
    # tiene 变体
    ("teien",   "tiene"),
    ("itene",   "tiene"),
    ("teine",   "tiene"),
    ("teinen",  "tienen"),
    # vende 变体
    ("bende",   "vende"),
    ("benden",  "venden"),
    ("bnede",   "vende"),
    # cuanto 变体
    ("cuento",  "cuanto"),
    ("caunto",  "cuanto"),
    ("cunato",  "cuanto"),
    # donde 变体
    ("donee",   "donde"),
    ("odne",    "donde"),
    ("doned",   "donde"),
    # transferencia
    ("tranferencia", "transferencia"),
    ("tansferencia", "transferencia"),
    ("trasferencia", "transferencia"),
    ("tranferecia",  "transferencia"),
    # direccion
    ("direcion",  "direccion"),
    ("direcxion", "direccion"),
    ("direccon",  "direccion"),
    # horario
    ("horairo",   "horario"),
    ("horraio",   "horario"),
    # entrega
    ("entrgea",   "entrega"),
    ("entrga",    "entrega"),
    # deposito
    ("deposisto",  "deposito"),
    ("depsoito",   "deposito"),
    # pedido
    ("pdeido",     "pedido"),
    ("peiddo",     "pedido"),
    # catalogo
    ("catalgo",    "catalogo"),
    ("cataologo",  "catalogo"),
    # ubicacion
    ("ubicaion",   "ubicacion"),
    ("ubiccaion",  "ubicacion"),
    # 单字符/双字符（仅独立词）
    ("q",      "que"),
    ("d",      "de"),
    ("x",      "por"),
    ("pa",     "para"),
    ("bn",     "bien"),
    ("ps",     "pues"),
    ("nd",     "nada"),
    ("cm",     "como"),
    ("k",      "que"),
]

# 预编译正则：\b + escaped_abbr + \b，长词优先
_SLANG_RE = [
    (re.compile(r'\b' + re.escape(abbr) + r'\b'), full)
    for abbr, full in sorted(_SLANG_PAIRS, key=lambda p: -len(p[0]))
]


def _expand_slang(text: str) -> str:
    """将拉美社交媒体缩略语展开为标准西班牙语，用于下游触发器匹配"""
    for pat, full in _SLANG_RE:
        text = pat.sub(full, text)
    return text

# 非产品词（动词、代词、冠词、介词、运费相关等），提取关键词时排除
# CHANGE: 加入 en/stock/del/de/al 等，避免 "tiene en stock muñecas" → ?q=en
_NON_PRODUCT_WORDS = frozenset({
    "si", "no", "hola", "gracias", "ok", "usted", "ustedes", "tienen", "tiene",
    "venden", "vende", "tener", "vender", "producto", "productos", "que", "qué",
    "informacion", "información", "precio", "cuanto", "cuánto",
    "comprar", "ver", "buscar", "conseguir", "pedir",
    "el", "la", "los", "las", "un", "una",  # 冠词，避免 "cuanto cuesta el envio" → ?q=el
    "en", "del", "de", "al", "a", "con", "por", "para", "es", "da", "dan",  # 介词/副词
    "stock", "stok",  # 库存词，非产品名
    "envio", "envios", "enviar", "transporte", "encomienda",  # 运费相关
    "esto", "esta", "eso", "esa", "esos", "esas", "algo",  # 指代/泛称，绝不作为 ?q=
    "hacer", "hacerlo", "hacerla", "pedir", "pedido",  # 动词/动作，非产品名
})

# 混合模型：文字用 GPT-4o-mini（质量高），图片用 Gemini Flash（便宜40倍）
DEFAULT_MODEL = "openai/gpt-4o-mini"
VISION_MODEL = "google/gemini-2.0-flash-001"
FALLBACK_MODEL = "openai/gpt-4o-mini"
VENTAX_CATALOG = "https://ventax.pages.dev/pwa_cart/"

# 绝不引导客户到 ?q=productos（返回大量无信息错误产品），一律用主链接
_NEVER_Q_KEYWORDS = frozenset({"productos", "electrodomesticos", "electrodomestico", "hogar", "ropa", "juguetes", "esto", "esta", "eso", "esa", "algo", "hacer"})


def _sanitize_reply_urls(text: str) -> str:
    """将 ?q=productos/esto 等错误链接替换为主链接，确保绝不引导到无效搜索页"""
    if not text or VENTAX_CATALOG not in text:
        return text
    for banned in _NEVER_Q_KEYWORDS:
        text = re.sub(rf"https://ventax\.pages\.dev/pwa_cart/\?q={re.escape(banned)}(?=[^\w]|$)", VENTAX_CATALOG, text)
    return text


API_TIMEOUT = 25
API_RETRIES = 1
API_RETRY_DELAY = 2

# 未匹配消息日志 — 记录绕过所有快速路径的原始消息，用于发现新缩略语/打字模式
_UNMATCHED_LOG = os.path.join(os.path.dirname(__file__), "..", "config", "unmatched_messages.log")


def _log_unmatched(raw_message: str):
    """将未被快速路径匹配的消息记录到日志文件，供后续分析"""
    try:
        normalized = _normalize(raw_message)
        ts = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M")
        line = f"[{ts}] RAW: {raw_message.strip()[:120]}  |  NORM: {normalized[:120]}\n"
        with open(_UNMATCHED_LOG, "a", encoding="utf-8") as f:
            f.write(line)
        # 保持日志文件不超过 500 行
        try:
            with open(_UNMATCHED_LOG, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > 500:
                with open(_UNMATCHED_LOG, "w", encoding="utf-8") as f:
                    f.writelines(lines[-300:])
        except Exception:
            pass
    except Exception:
        pass

# 产品类极简 prompt（快速路径不调 LLM，此处仅作 fallback）
# NOTE: NUNCA usar "productos" como KEYWORD；?q=productos 会返回大量无信息错误产品
SYSTEM_PROMPT_PRODUCT = """Eres la asesora de VentaX Ecuador. Una sola regla:
Si el cliente menciona producto concreto (tiene agenda, lapiz, bolso, etc): usa ?q=palabra_del_producto.
Si NO menciona producto concreto (qué venden, q producto): usa solo https://ventax.pages.dev/pwa_cart/
NUNCA uses "productos" como KEYWORD en ?q=."""

# distilled 知识路径（相对于 internal 根）
_KNOWLEDGE_DIR = None
_SKILLS_DIR = None
_SKILLS_CACHE = None
_RULES_CACHE = None
_DISTILLED_CACHE = None


def _get_skills_dir():
    """获取 workspace skills 目录绝对路径"""
    global _SKILLS_DIR
    if _SKILLS_DIR is not None:
        return _SKILLS_DIR
    base = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(base, "..", "..", ".openclaw", "workspace", "skills"))
    _SKILLS_DIR = candidate if os.path.isdir(candidate) else ""
    return _SKILLS_DIR


def _get_workspace_dir():
    """获取 .openclaw/workspace 目录"""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base, "..", "..", ".openclaw", "workspace"))


def _load_workspace_rules() -> str:
    """
    加载小龙虾 OpenClaw 的 SOUL/AGENTS/IDENTITY/BOOTSTRAP 核心规则
    移植到本机器人，确保禁止规则与流程一致。
    结果缓存，避免每次 LLM 请求重复读文件，降低 CPU。
    """
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    wd = _get_workspace_dir()
    if not os.path.isdir(wd):
        _RULES_CACHE = ""
        return ""
    files = ["SOUL.md", "AGENTS.md", "IDENTITY.md", "BOOTSTRAP.md"]
    parts = []
    for name in files:
        path = os.path.join(wd, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read().strip()
                    if content and ("VENTAX" in content or "NUNCA" in content or "ventax" in content):
                        parts.append(content[:2500])  # 限制长度
            except Exception:
                pass
    if not parts:
        _RULES_CACHE = ""
        return ""
    _RULES_CACHE = "\n\n---\n\n".join(parts[:3])  # 最多 3 个文件，避免过长
    return _RULES_CACHE


def _load_skills() -> str:
    """
    加载外部客服/销售技能（customer_service, conversion_rate, trust_building, lead_scoring）
    返回合并后的技能文本
    """
    global _SKILLS_CACHE
    if _SKILLS_CACHE is not None:
        return _SKILLS_CACHE
    sd = _get_skills_dir()
    skill_files = [
        "human_like_sales_skills_2025.md",
        "customer_service_ecuador.md",
        "conversion_rate_intent_router_ec.md",
        "trust_building_anti_scam_ec.md",
        "lead_scoring_and_handoff.md",
        "multichannel_marketing_ecuador.md",
    ]
    parts = []
    for name in skill_files:
        path = os.path.join(sd, name) if sd else ""
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    parts.append(f.read().strip())
            except Exception:
                pass
    _SKILLS_CACHE = "\n\n---\n\n".join(parts) if parts else ""
    return _SKILLS_CACHE


def _get_knowledge_dir():
    """获取 knowledge 目录绝对路径"""
    global _KNOWLEDGE_DIR
    if _KNOWLEDGE_DIR is not None:
        return _KNOWLEDGE_DIR
    base = os.path.dirname(os.path.abspath(__file__))
    # internal/VentaX_json/modules -> internal/.openclaw/workspace/knowledge
    candidate = os.path.normpath(os.path.join(base, "..", "..", ".openclaw", "workspace", "knowledge"))
    _KNOWLEDGE_DIR = candidate if os.path.isdir(candidate) else ""
    return _KNOWLEDGE_DIR


def _load_distilled_content() -> tuple[str, str]:
    """加载 distilled 高/低意图内容，返回 (high_intent_md, low_intent_md)。结果缓存，降低 CPU。"""
    global _DISTILLED_CACHE
    if _DISTILLED_CACHE is not None:
        return _DISTILLED_CACHE
    kd = _get_knowledge_dir()
    high_path = os.path.join(kd, "whatsapp_distilled_high_intent.md") if kd else ""
    low_path = os.path.join(kd, "whatsapp_distilled_low_intent.md") if kd else ""
    high_md = ""
    low_md = ""
    if high_path and os.path.isfile(high_path):
        try:
            with open(high_path, encoding="utf-8") as f:
                high_md = f.read().strip()
        except Exception:
            pass
    if low_path and os.path.isfile(low_path):
        try:
            with open(low_path, encoding="utf-8") as f:
                low_md = f.read().strip()
        except Exception:
            pass
    _DISTILLED_CACHE = (high_md, low_md)
    return _DISTILLED_CACHE


def _detect_intent(user_text: str) -> str:
    """
    检测非产品类消息的意图：high / low
    高意图：价格、物流、确认数量、 closing pedido
    低意图：问候、模糊咨询
    """
    t = _normalize(user_text.strip())
    if not t or len(t) < 2:
        return "low"
    high_triggers = [
        "precio", "cuanto", "cuesta", "costo", "cotiz", "envio",
        "provincia", "ciudad", "tiempo", "llegar", "docena", "caja", "bulto",
        "cantidad", "unidad", "mayor", "menor", "transferencia", "pedido",
        "ruc", "cedula", "completar", "cerrar", "comprar",
    ]
    if any(tr in t for tr in high_triggers):
        return "high"
    low_triggers = ["hola", "buenos", "buenas", "precio?", "informacion", "ayuda", "ayudame"]
    if any(tr in t for tr in low_triggers) or len(t) < 15:
        return "low"
    return "high"  # 默认按高意图处理，避免漏掉转化机会


# 核心服务原则：两种情形 (1)指定产品→引导到网站 (2)不知要什么→倾听需求、真诚推荐、促成复购
_CORE_PRINCIPLE = """
Actúa como amiga de la cliente. Dos situaciones:
1) Si ya sabe qué producto quiere: guíala al enlace con búsqueda directa.
2) Si no sabe qué necesita: escucha su necesidad, uso y cantidad; recomienda productos del catálogo que le encajen. Objetivo: cliente contenta, recompra, ciclo virtuoso.

DATOS DE LA TIENDA (obligatorio usar cuando pregunten):
- Horario: Lunes a Sábado 9:00 AM — 6:30 PM | Domingo 9:30 AM — 5:00 PM
- Ubicación: Novedades Cristy — Lorenzo de Garaycoa 1521 y Colón, Guayaquil, Ecuador
- Envío: A todo Ecuador, costo aproximado $8 (varía según distancia/cantidad). Guayaquil: entrega al siguiente día hábil. Otras ciudades: 2-3 días hábiles.
- Pago: Transferencia bancaria o depósito.

IMPORTANTE — Escalamiento a humano:
Si el cliente tiene un problema que NO puedes resolver (reclamos graves, devoluciones, problemas de pago, errores de pedido, temas legales, o cualquier situación compleja), responde amablemente y pídele que llame por WhatsApp al 0939962405. Indica que puede tocar el número para llamar directamente. Ejemplo: "Para resolver esto de la mejor manera, te invito a llamarnos por WhatsApp 📞 al 0939962405. Puede tocar el número para llamar directamente. ¡Con gusto te atendemos personalmente!"
"""


def _build_system_prompt(intent: str, lite: bool = False) -> str:
    """
    按高/低意图构建 LLM system prompt，并入 distilled 模板 + 外部客服/销售技能 + 小龙虾规则
    lite=True: 极简 prompt，用于 400 重试（免费模型 token 限制）
    """
    base = "Eres la asesora de VentaX Ecuador. Responde en español, breve y amigable." + _CORE_PRINCIPLE + "\n"
    rules = _load_workspace_rules()
    if rules and not lite:
        base += "\n## Reglas críticas (OpenClaw/VentaX)\n\n" + rules[:1500] + "\n\n"
    if lite:
        return base + "Saludo corto. Si pregunta producto: da https://ventax.pages.dev/pwa_cart/ Si no sabe qué buscar: pregunta qué producto o cantidad necesita."
    high_md, low_md = _load_distilled_content()
    skills_md = _load_skills()
    if intent == "high" and high_md:
        prompt = base + "Sigue estas guías para clientes con alta intención de compra:\n\n" + high_md
    elif intent == "low" and low_md:
        prompt = base + "Sigue estas guías para contactos fríos (baja intención). Si no sabe qué buscar: escucha y recomienda según su necesidad y cantidad:\n\n" + low_md
    else:
        prompt = base + (
            "Si mencionan producto concreto (ej. agenda, lapiz), da: https://ventax.pages.dev/pwa_cart/?q=palabra. "
            "NUNCA uses 'productos' en ?q=. Si no saben qué buscar, da solo: https://ventax.pages.dev/pwa_cart/"
        )
    if skills_md:
        prompt += "\n\n## Habilidades de servicio y ventas (obligatorio seguir)\n\n" + skills_md
    return prompt


def _get_llm_fallback_reply(intent: str) -> str:
    """
    LLM 超时/失败时的意图兜底回复，确保客户仍获得有用信息
    """
    if intent == "high":
        return (
            "Con gusto le ayudo. Me indica producto, cantidad y ciudad "
            "para darle precio y envío. O revise aquí: "
            f"{VENTAX_CATALOG}\n"
            "Si necesita ayuda urgente, llámenos por WhatsApp 📞 al 0939962405 (toque el número para llamar)."
        )
    return (
        "Hola, con gusto le ayudo 😊 "
        "¿Qué producto busca hoy? Puede ver el catálogo aquí: "
        f"{VENTAX_CATALOG}"
    )


def _get_api_key():
    """从环境或 openclaw 配置读取 API key"""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    _base = os.path.join(os.path.dirname(__file__), "..", "..")
    # openclaw.json → env.OPENROUTER_API_KEY
    try:
        cfg_path = os.path.join(_base, ".openclaw", "openclaw.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
                k = cfg.get("env", {}).get("OPENROUTER_API_KEY")
                if k:
                    return k
    except Exception:
        pass
    # auth-profiles.json → openrouter:default.key
    try:
        ap_path = os.path.join(
            _base, ".openclaw", "agents", "main", "agent", "auth-profiles.json"
        )
        if os.path.exists(ap_path):
            with open(ap_path, encoding="utf-8") as f:
                ap = json.load(f)
                k = ap.get("profiles", {}).get("openrouter:default", {}).get("key")
                if k:
                    return k
    except Exception:
        pass
    return None


def _extract_product_keyword(text: str) -> str:
    """
    从用户消息提取产品关键词，排除动词/代词等非产品词
    que producto tienen / que venden → productos
    """
    text = _normalize(text.strip())

    # 模糊咨询：有哪些产品、卖什么 → productos
    if re.search(r"q\s*vendes?\s*(ustedes)?\s*$", text):
        return "productos"
    if re.search(r"q\s*producto\s*(tiene|tienen)?\s*", text):
        return "productos"
    if re.search(r"que\s+producto\s+(tienen|tiene|venden|vende)\b", text, re.I):
        return "productos"
    if re.search(r"que\s+(tienen|venden)\b", text, re.I):
        return "productos"
    if re.search(r"q\s*tienen\b", text, re.I):
        return "productos"

    # CHANGE: "tiene en stock X" / "hay en stock X" 优先于 "tiene X"，避免 ?q=en
    m = re.search(r"tiene[ns]?\s+en\s+stock\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"hay\s+en\s+stock\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    # CHANGE: "tiene el/la/los/las/un/una X" 跳过冠词提取产品名，避免 ?q=el
    m = re.search(r"tiene[ns]?\s+(?:el|la|los|las|un|una)\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"vende[ns]?\s+(?:el|la|los|las|un|una)\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    # tiene/tienes X, vende X, disponible X, hay X（X 为具体产品名）
    m = re.search(r"tiene[ns]?\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"vende[ns]?\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"disponible\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"hay\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"producto\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    # quiero comprar un/una X, quiero ver X, quiero X
    m = re.search(r"quiero\s+(?:comprar|ver|buscar|conseguir|pedir)\s+(?:un[oa]?\s+)?(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    for prefix in (r"busco\s+(\w+)", r"quiero\s+(\w+)", r"necesito\s+(\w+)", r"informacion\s+de\s+(\w+)"):
        m = re.search(prefix, text, re.I)
        if m and m.group(1) not in _NON_PRODUCT_WORDS:
            return m.group(1)
    # envía/mandame modelo de X, envía X — 加强产品关键词抓取
    m = re.search(r"(?:envia[ns]?|manda(?:me)?[ns]?)\s+(?:modelo\s+de\s+)?(?:las?\s+|los?\s+|un[oa]?\s+)?(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"modelo\s+de\s+(?:las?\s+|los?\s+)?(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    # "como salen las X", "a como da las X", "cuanto cuestan las X"
    m = re.search(r"(?:como\s+sale[ns]?|a\s+como\s+(?:da|dan|sale|salen)|cuanto\s+cuesta[ns]?)\s+(?:las?\s+|los?\s+|un[oa]?\s+)?(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)

    products = ("lapiz", "bolso", "cartera", "chupon", "maquillaje", "monedero", "juguete", "mochila", "cuaderno", "reloj", "zapato", "ropa", "moto", "espuma", "globo", "carioca", "karioca", "pistola", "pinata", "pinateria", "torta", "anilina", "paragua", "paraguas")
    # 语音 b→v 后的变体也视为产品词
    products_v = ("volso", "volsos", "voldo")
    for w in re.findall(r"\b[a-záéíóúñ]+\b", text):
        if w in products or w in products_v:
            return w
    return "productos"


def _fix_product_keyword(kw: str) -> str:
    """语音规则(b→v)/拼写纠错：volso/voldo/volsos→bolso，复数归一化；paragua→paraguas"""
    return {
        "volso": "bolso", "volsos": "bolso", "voldo": "bolso", "boldo": "bolso",
        "bolsa": "bolso", "bolsos": "bolso",
        "carteras": "cartera", "lapices": "lapiz",
        "paragua": "paraguas",  # 拉美常见缩写
    }.get(kw, kw)


_DESC_SKIP = frozenset({"opciones", "mas", "todo", "algunos", "varios", "diferentes", "tipos", "modelos"})


def _extract_product_from_description(text: str) -> str:
    """
    从产品描述/LLM 回复中提取关键词（如图片识别结果 "carros de juguete" → carros）
    用于智能生成 ?q= 链接
    """
    t = _normalize(text.strip())
    if not t or len(t) < 3:
        return ""
    # "X de juguete(s)" → X（carros de juguete, muñecas de juguete）
    m = re.search(r"(\w+)\s+de\s+juguetes?", t, re.I)
    if m:
        kw = m.group(1)
        if kw not in _NON_PRODUCT_WORDS and kw not in _DESC_SKIP:
            return kw
    # "set de N X" / "set de X" → X
    m = re.search(r"set\s+de\s+(?:\d+\s+)?(\w+)", t, re.I)
    if m:
        kw = m.group(1)
        if kw not in _NON_PRODUCT_WORDS and kw not in _DESC_SKIP:
            return kw
    # "X tipo Y"（muñecas tipo Barbie）
    m = re.search(r"(\w+)\s+tipo\s+\w+", t, re.I)
    if m:
        kw = m.group(1)
        if kw not in _NON_PRODUCT_WORDS and kw not in _DESC_SKIP:
            return kw
    # 已知产品词
    products = ("lapiz", "bolso", "cartera", "chupon", "maquillaje", "monedero", "juguete",
                "mochila", "cuaderno", "reloj", "zapato", "ropa", "moto", "espuma", "globo",
                "carioca", "karioca", "pistola", "pinata", "pinateria", "torta", "anilina",
                "carros", "carro", "munecas", "muneca", "dulces", "dulce")
    for w in re.findall(r"\b[a-záéíóúñ]+\b", t):
        if w in products and w not in _NON_PRODUCT_WORDS:
            return w
    return ""


def _keyword_for_url(raw_text: str, normalized_kw: str, fixed_kw: str) -> str:
    """
    为 URL ?q= 参数生成关键词，保留西语重音字符（ñ, á, é 等）。
    - 若经过拼写纠错（fixed != normalized）：使用纠错后的词
    - 否则：从原始文本按位置还原带重音的关键词（muñecas 而非 munecas）
    """
    if fixed_kw != normalized_kw:
        return fixed_kw  # 纠错词无重音，直接返回
    raw_stripped = raw_text.strip()
    norm_text = _normalize(raw_stripped)
    pos = norm_text.find(normalized_kw)
    if pos >= 0 and pos + len(normalized_kw) <= len(raw_stripped):
        raw_kw = raw_stripped[pos : pos + len(normalized_kw)]
        return raw_kw
    return normalized_kw


_ESCALATION_PHONE = "0939962405"

_ESCALATION_TRIGGERS = [
    "devolucion", "devolver", "reclamo", "queja", "reembolso", "estafa",
    "demanda", "abogado", "legal", "defecto", "danado", "roto",
    "no funciona", "no sirve", "llego mal", "pedido equivocado",
    "me cobraron", "doble cobro", "no me llego", "no llega",
    "pago erroneo", "error de pago", "problema con el pago",
]


def _escalation_reply(user_text: str) -> str | None:
    """检测需要转人工的复杂问题"""
    t = _normalize(user_text.strip())
    if any(tr in t for tr in _ESCALATION_TRIGGERS):
        return (
            f"Lamento mucho lo sucedido 😔 Para resolver esto de la mejor manera, "
            f"te invito a llamarnos por WhatsApp 📞 al {_ESCALATION_PHONE}. "
            f"Puede tocar el número para llamar directamente. ¡Con gusto te atendemos personalmente!"
        )
    return None


def _call_for_order_reply(user_text: str) -> str | None:
    """
    客户想打电话下单 — 给电话号码，勿返回产品链接
    例：Quiero hacer una llamada directa para un pedido
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 100:
        return None
    triggers = [
        "llamada directa", "llamar directo", "llamar para pedido",
        "hacer llamada para pedido", "llamada para pedido",
        "quiero llamar", "llamar para ordenar", "ordenar por telefono",
    ]
    if not any(tr in t for tr in triggers):
        return None
    return (
        f"Claro, puede llamarnos al {_ESCALATION_PHONE} para hacer su pedido. "
        "Puede tocar el número para llamar directamente."
    )


def _human_support_followup_reply(user_text: str) -> str | None:
    """
    转人工后的追问 — 客户问「会有人接吗」「是真人吗」等，需连贯回答
    例：Me va a contestar una persona / ¿Me atiende una persona?
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 80:
        return None
    triggers = [
        "contestara una persona", "contestar una persona", "contestaran una persona",
        "me va a contestar una persona", "va a contestar una persona",
        "atiende una persona", "atienden una persona", "me atiende una persona",
        "es una persona", "persona real", "es persona", "humano", "humana",
        "hablo con una persona", "hablar con persona", "hablar con una persona",
        "me contesta una persona", "contesta una persona",
    ]
    if not any(tr in t for tr in triggers):
        return None
    return (
        f"Sí, cuando llame al {_ESCALATION_PHONE} recibirá atención personalizada "
        "de una persona de nuestro equipo. Puede tocar el número para llamar directamente."
    )


def _fast_reply(user_text: str) -> str | None:
    """
    快速规则匹配 — 不调 LLM，直接返回链接
    当检测到明确的产品意图时使用
    """
    t = _normalize(user_text.strip())
    if not t or len(t) < 3:
        return None
    triggers = [
        "tiene", "tienen", "vende", "venden", "disponible", "hay",
        "producto", "productos",
        "lapiz", "bolso", "volso", "bolsos", "boldo", "cartera", "chupon", "maquillaje", "juguete", "mochila", "monedero",
        "electrodomesticos", "electrodomestico", "hogar", "ropa",
        "envia", "envíame", "mandame", "manda", "modelo de",
        "q vende", "que venden", "que tienen",
        "q juguete", "que juguete", "juguete bueno", "juguete para",
        "busco", "quiero", "necesito", "informacion de", "precio de",
        "cuanto vale", "tienen algo", "que hay", "que ofrecen",
        "lista de productos", "ver productos", "que productos",
        "algo de", "algun producto", "tipos de producto",
        "como salen", "como sale", "cuanto cuesta",
        "como estan los precios", "como esta el precio", "como esta la",
        "que modelos", "fotos de", "tiene fotos",
        "pinateria", "pinata",
    ]
    if not any(tr in t for tr in triggers):
        if re.search(r'\ba como\b', t):
            pass
        else:
            return None
    # 排除运费/物流/营业时间类 — 这些应由 _business_faq_reply 处理
    non_product_context = [
        "envio", "enviar", "envios", "transporte", "encomienda",
        "despacho", "delivery", "deliberi",
        "que hora", "horario", "atiende", "abre", "cierra",
    ]
    if any(nc in t for nc in non_product_context):
        return None
    norm_kw = _extract_product_keyword(user_text)
    fixed_kw = _fix_product_keyword(norm_kw)
    if not fixed_kw or fixed_kw in _NON_PRODUCT_WORDS:
        fixed_kw = "productos"

    # CHANGE: 泛/宽泛名词一律用主链接，除非确认数据库有该类别
    # 含指代词 esto/esta/eso 等、动词 hacer 等，绝非产品名
    _BANNED_Q_KEYWORDS = frozenset({
        "productos", "electrodomesticos", "electrodomestico", "hogar", "ropa",
        "juguetes", "cosas", "articulos", "items", "mercancia",
        "esto", "esta", "eso", "esa", "esos", "esas", "algo",
        "hacer", "pedir", "pedido",
    })
    if fixed_kw in _BANNED_Q_KEYWORDS:
        openings = [
            "Sí amiga, claro 👇",
            "Claro que sí 👇",
            "Aquí está 👇",
            "Sí, aquí lo ve 👇",
        ]
        closings = [
            "Abre y veras modelos/fotos al instante.",
            "Ahí verás fotos y precios.",
            "Ahí está todo el catálogo.",
        ]
        return f"{random.choice(openings)}\n{VENTAX_CATALOG}\n{random.choice(closings)}"

    # NOTE: 保留西语重音（muñecas 而非 munecas）以提升 pwa_cart 搜索匹配
    url_kw = _keyword_for_url(user_text, norm_kw, fixed_kw)
    q_encoded = quote(url_kw, safe="")

    # 人性化：多种开场白，避免机械重复
    openings = [
        "Sí amiga, claro 👇",
        "Claro que sí 👇",
        "Aquí está 👇",
        "Sí, aquí lo ve 👇",
    ]
    closings = [
        "Abre y veras modelos/fotos al instante.",
        "Ahí verás fotos y precios.",
        "Ahí está todo el catálogo.",
    ]
    opening = random.choice(openings)
    closing = random.choice(closings)
    return f"{opening}\n{VENTAX_CATALOG}?q={q_encoded}\n{closing}"


def _greeting_reply(user_text: str) -> str | None:
    """
    纯问候类消息快速回复 — 不调 LLM
    确保 hola/como esta/buenos dias 等始终有友好回复
    """
    t = _normalize(user_text.strip())
    if not t or len(t) < 2:
        return None
    greeting_triggers = [
        "hola", "buenos", "buenas", "buena trade", "buena tarde", "buena noche",
        "como esta", "como estas", "que tal", "saludos",
        "buen dia", "buenas tardes", "buenas noches",
    ]
    if not any(tr in t for tr in greeting_triggers):
        return None
    biz_keywords = [
        "donde", "ubica", "encuentr", "envio", "enviar", "direccion",
        "ubicado", "ubicar", "uvicar", "hubican", "deliberi", "delivery",
        "provincia", "local", "tienda",
    ]
    if any(bk in t for bk in biz_keywords):
        return None
    product_triggers = [
        "tiene", "vende", "disponible", "hay", "producto", "lapiz", "bolso", "cartera", "chupon",
        "maquillaje", "como salen", "como sale", "a como", "moto", "juguete",
        "precio", "cuanto", "fotos",
    ]
    if any(tr in t for tr in product_triggers):
        return None
    compliment_words = ["combo", "publicidad", "creciendo", "felicit", "excelente", "genial producto", "ganga"]
    if any(cw in t for cw in compliment_words):
        return None

    EC_TZ = timezone(timedelta(hours=-5))
    hour = datetime.now(EC_TZ).hour
    if hour < 12:
        saludo = "Buenos días"
    elif hour < 18:
        saludo = "Buenas tardes"
    else:
        saludo = "Buenas noches"
    greetings = [
        f"¡{saludo}! 😊 Soy Carolina de Novedades Cristy, ¿en qué le puedo ayudar?",
        f"¡{saludo} amiga! Con gusto le atiendo 😊 ¿Qué busca hoy?",
        f"¡{saludo}! Bienvenida 😊 Cuénteme, ¿qué necesita?",
    ]
    return random.choice(greetings)


def _identity_reply(user_text: str) -> str | None:
    """
    身份/名字类问题快速回复 — 不调 LLM
    como te llama, cual es tu nombre, como se llama su local 等
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 60:
        return None
    # 店名问题 — 优先于个人身份
    store_triggers = [
        "como se llama su local", "como se llama el local",
        "como se llama su tienda", "como se llama el negocio",
        "como se llama la tienda", "nombre del local", "nombre de la tienda",
    ]
    if any(st in t for st in store_triggers):
        return (
            "Nuestro local se llama Novedades Cristy 😊\n"
            "📍 Lorenzo de Garaycoa 1521 y Colón, Guayaquil\n"
            "📌 https://maps.app.goo.gl/n1v5m8E4QS9vKnvZ6"
        )
    identity_triggers = [
        "como te llama", "como te llamas",
        "cual es tu nombre", "quien eres",
    ]
    if not any(tr in t for tr in identity_triggers):
        return None
    has_greeting = any(g in t for g in ["hola", "buenas", "buenos", "como esta", "que tal"])
    if has_greeting:
        replies = [
            "¡Hola! Bien, gracias 😊 Soy Carolina de Novedades Cristy. ¿En qué le ayudo?",
            "¡Todo bien, gracias! Me llamo Carolina 😊 ¿Qué anda buscando?",
        ]
    else:
        replies = [
            "Soy Carolina, asesora de Novedades Cristy 😊 ¿En qué puedo ayudarte?",
            "Me llamo Carolina 😊 Estoy para servirle. ¿Qué necesita?",
        ]
    return random.choice(replies)


def _help_reply(user_text: str) -> str | None:
    """
    通用求助/需求不清晰类 — 直接引导到主页，不调 LLM
    CHANGE: 需求唔清晰时直接给链接，后续系统升级会加产品类别分类
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 80:
        return None
    help_triggers = [
        "me puede ayudar", "puede ayudarme", "ayudame", "me ayudas", "ayudas con",
        "me ayuda", "ayuda en algo", "puede ayudar en algo",
        "cosas de hogar", "cosas para el hogar", "articulos de hogar",
    ]
    if not any(tr in t for tr in help_triggers):
        return None
    replies = [
        f"Claro, con gusto 😊 Puede ver todo el catálogo aquí: {VENTAX_CATALOG}",
        f"Sí, aquí está el catálogo con productos: {VENTAX_CATALOG}",
        f"¡Con gusto! Revise aquí: {VENTAX_CATALOG}",
    ]
    return random.choice(replies)


def _business_faq_reply(user_text: str) -> str | None:
    """
    常见商务问题快速回复 — 不调 LLM
    ubicacion, envios, donde, horario, direccion, whatsapp 等
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 180:
        return None

    # 营业时间类 — 必须在位置和运费之前，避免 "que hora" 被 _off_topic_reply 拦截成当前时间
    # 节假日 — 不营业，须引导到网页 24/7
    feriado_triggers = ["feriado", "feriados", "dias feriados", "atiende feriado", "abren feriado"]
    if any(tr in t for tr in feriado_triggers):
        return (
            "No, en feriados no atendemos en el local.\n"
            "🕘 Horario: Lunes a Sábado 9:00 AM — 6:30 PM | Domingo 9:30 AM — 5:00 PM\n"
            f"🛒 Puede comprar en línea 24/7: {VENTAX_CATALOG}"
        )

    hours_triggers = [
        "que hora atiende", "hasta que hora", "a que hora",
        "que hora abre", "que hora cierra", "que hora abren", "que hora cierran",
        "horario de atencion", "horario atencion", "horario",
        "hora de atencion", "hora atencion",
        "cuando atiende", "cuando abren", "cuando cierran",
        "esta abierto", "estan abierto", "abierto hoy",
        "esta cerrado", "estan cerrado", "cerrado hoy",
        "dias de atencion", "que dias atiende", "que dias abren",
        "atiende hoy", "abren hoy", "trabajan hoy",
        "atiende domingo", "abren domingo", "trabajan domingo",
        "atiende sabado", "abren sabado",
    ]
    if any(tr in t for tr in hours_triggers):
        return (
            "Nuestro horario de atención:\n"
            "🕘 Lunes a Sábado: 9:00 AM — 6:30 PM\n"
            "🕤 Domingo: 9:30 AM — 5:00 PM\n"
            "📍 Novedades Cristy — Lorenzo de Garaycoa 1521 y Colón, Guayaquil\n"
            f"🛒 Puede comprar en línea 24/7 sin esperar: {VENTAX_CATALOG}"
        )

    # 位置/地址类 — _normalize 已去重音，只需无重音版
    location_triggers = [
        "donde esta", "donde estan", "donde queda", "ubicacion",
        "direccion", "en donde", "donde amiga",
        "donde es", "donde son", "de donde",
        "donde se ubican", "donde se encuentran", "se ubican", "se encuentran",
        "donde te ubico", "donde les ubico", "donde los ubico",
        "donde les puedo", "donde los puedo", "donde lo puedo",
        "donde se hubican", "donde les puedo uvicar",
        "ubican", "ubicado", "ubicados", "ubicar", "ubicacion",
        "uvicar", "hubican",
        "encuentran", "donde se encuentra",
        "en q parte", "en que parte", "dn q parte", "dn que parte",
        "de donde eres", "de donde res", "de donde son",
        "en donde estan", "en donde esta",
        "donde les encuentro", "donde los encuentro",
        "por donde",
        "donde lo podemos conseguir", "donde podemos conseguir",
        "donde lo consigo", "donde consigo",
        "donde puedo comprar", "donde puedo pedir", "donde compro",
        "que provincia", "en que provincia",
        "donde se pide", "donde pido", "como pido", "pedir en linea",
        "comprar en linea", "comprar online", "pedir online",
    ]
    if any(tr in t for tr in location_triggers):
        return (
            "Somos de Guayaquil, Ecuador 🇪🇨\n"
            "📍 Novedades Cristy — Lorenzo de Garaycoa 1521 y Colón\n"
            "Hacemos envío a nivel nacional 📦\n"
            "📌 https://maps.app.goo.gl/n1v5m8E4QS9vKnvZ6"
        )

    # 到店自提类 — 必须包含地址链接
    retiro_triggers = [
        "retiro en el local", "retiro en local", "retirar en el local", "retirar en local",
        "retiro en tienda", "retirar en tienda", "puedo retirar", "retiro en su local",
        "pasar a retirar", "ir a retirar", "buscar en el local", "recoger en local",
    ]
    if any(tr in t for tr in retiro_triggers):
        return (
            "¡Claro! Puede retirar su pedido en nuestro local.\n"
            "📍 Novedades Cristy — Lorenzo de Garaycoa 1521 y Colón, Guayaquil\n"
            "📌 https://maps.app.goo.gl/n1v5m8E4QS9vKnvZ6\n"
            "😊 Si decide hacer la compra, avíseme y coordinamos su retiro."
        )

    # 运费价格类 — 优先匹配，避免被产品搜索拦截（如 "cuanto cuesta el envio" → ?q=el）
    shipping_cost_triggers = [
        "cuanto cuesta el envio", "cuanto cuesta envio",
        "costo del envio", "costo envio",
        "cuanto es el envio", "precio del envio", "precio envio",
        "cuanto cobra", "cobran envio", "valor del envio", "valor envio",
        "cuanto cuesta enviar", "cuanto cuesta envios",
        "cuanto vale el envio", "cuanto vale enviar",
        "precio del transporte", "costo del transporte", "precio transporte",
        "cuanto cuesta el transporte", "cuanto es el transporte",
        "que precio tiene el transporte", "que precio tiene el envio",
        "que cuesta el envio", "que cuesta enviar",
        "que tiene el envio", "que valor tiene el envio",
    ]
    # 加拉帕戈斯 — 须在 shipping_cost 之前，避免被通用运费拦截
    # CHANGE: 加入 galaspago/galapago 等拼写变体，智能识别客户咨询
    galapagos_triggers = [
        "galapagos", "galápagos", "galaspago", "galapago",
        "islas galapagos", "santa cruz galapagos", "puerto ayora", "baltra", "san cristobal"
    ]
    # 匹配：地区名 + 运费/发货相关词（envio/hacer envio/si envian 等）
    galapagos_envio_words = ["envio", "envios", "enviar", "envian", "hacer envio", "hacen envio", "llega", "mandan", "como", "costo", "precio", "cuanto"]
    if any(tr in t for tr in galapagos_triggers) and any(w in t for w in galapagos_envio_words):
        return (
            "Sí, hacemos envíos a Galápagos 📦\n"
            "Solo por mar o avión (no mensajería terrestre). El costo depende del peso y cantidad; le damos la info para que consulte:\n\n"
            "• Carga/contenedor (mar): Pacific Cargo Line (PCL) — Guayaquil ↔ Galápagos\n"
            "  📍 Domingo Comín S/L 29, Edif. Puertogal, Guayaquil\n"
            "  📞 +593 96-707-8696 | guayaquil@pcl.ec\n"
            "• Vuelos (paquete pequeño): LATAM, Avianca — Quito/Guayaquil ↔ Baltra/San Cristóbal\n"
            "  Consulte horarios y precios directamente con las aerolíneas.\n\n"
            f"¿Qué producto le interesa? {VENTAX_CATALOG}"
        )

    # CHANGE: "cuanto cuesta/vale/es" + envio/envios/enviar 组合也视为运费问题（语音截断等）
    is_shipping_cost = any(tr in t for tr in shipping_cost_triggers) or (
        any(phrase in t for phrase in ["cuanto cuesta", "cuanto vale", "cuanto es"]) and
        any(w in t for w in ["envio", "envios", "enviar"])
    )
    # CHANGE: "X bultos/cajas a [ciudad]" + costo/precio/cuanto → 运费咨询（避免被 ?q=5 产品搜索拦截）
    # 例: "que costo tiene 5 bultos a Machala" / "precio 3 cajas a Quito"
    # NOTE: _normalize 会将 b→v（西语语音），故 bultos→vultos，需同时匹配
    _ciudades_ec = ("machala", "quito", "guayaquil", "cuenca", "manta", "santo domingo",
                    "loja", "ambato", "portoviejo", "esmeraldas", "duran", "milagro")
    _bultos_words = ("bultos", "bulto", "vultos", "vulto", "cajas", "caja")
    is_bultos_envio = (
        any(w in t for w in ["costo", "precio", "cuanto", "cuesta", "valor"]) and
        any(w in t for w in _bultos_words) and
        (re.search(r"\ba\s+\w", t) or any(ci in t for ci in _ciudades_ec) or "provincia" in t or "ciudad" in t)
    )
    if is_shipping_cost or is_bultos_envio:
        return (
            "El envío es aproximadamente $8, a nivel nacional 🇪🇨📦\n"
            "Puede variar según la distancia, cantidad de productos y número de cajas. "
            "Si es más, le avisamos; si es menos, se le devuelve la diferencia 😊"
        )

    # 国际运输
    intl_countries = ["peru", "mexico", "colombia", "venezuela", "chile", "argentina", "estados unidos", "usa"]
    is_intl = any(c in t for c in intl_countries)
    if is_intl and (len(t) < 25 or any(w in t for w in ["envio", "enviar", "llegan", "mandan", "despachan", "encuentran", "ubican"])):
        return (
            "Por el momento solo hacemos envíos dentro de Ecuador 🇪🇨📦 "
            "Somos de Guayaquil. ¿Le interesa algún producto? "
            f"Puede ver el catálogo aquí: {VENTAX_CATALOG}"
        )

    # 发货/物流类
    # 发货时间类 — "cuanto tarda", "que tiempo se demora" 等
    shipping_time_triggers = [
        "cuanto tarda", "cuanto demora", "cuanto se demora",
        "que tiempo se demora", "que tiempo demora",
        "tiempo de entrega", "tiempo de envio",
        "cuando llega", "en cuanto llega",
        "cuantos dias tarda", "cuantos dias demora",
        "se demora en enviar", "se demora en llegar",
    ]
    if any(tr in t for tr in shipping_time_triggers):
        return (
            "El envío demora de 1 a 3 días hábiles 📦 dependiendo de la ciudad.\n"
            "Guayaquil: entrega al siguiente día hábil.\n"
            "Otras ciudades: 2-3 días hábiles.\n"
            "¿Me indica su ciudad y qué producto le interesa? 😊"
        )

    # 本地/同城摩托/的士送货 — 客人对运费敏感，灵活提供多种选项
    motorizado_triggers = [
        "motorizado", "moto", "motocicleta", "taxi", "taxista",
        "envio en moto", "enviar con moto", "delivery en moto",
        "envio en taxi", "enviar con taxi", "rapido", "rappi",
        "mensajero", "mensajeria local", "entrega local",
    ]
    if any(tr in t for tr in motorizado_triggers):
        return (
            "Para envío local rápido (moto/taxi), tenemos varias opciones:\n"
            "• Podemos ayudarle a coordinar con servicio de moto/taxi; el costo del envío lo asume el cliente.\n"
            "• O puede buscar su propio servicio de mensajería local.\n"
            "• También puede retirar en nuestro local: Lorenzo de Garaycoa 1521 y Colón, Guayaquil.\n"
            "📌 https://maps.app.goo.gl/n1v5m8E4QS9vKnvZ6\n"
            "Servicios de entrega local que puede consultar:\n"
            "• Rappi: https://www.rappi.com.ec\n"
            "• inDrive: https://indrive.com (taxi/moto, usted propone el precio)\n"
            "• MiMensajeroExpress: https://www.mimensajeroexpress.com\n"
            "• Delivereo: https://delivereo.com\n"
            "• Gacela Delivery, Rueda Express, Moto Express Guayaquil (busque en Google/WhatsApp)\n"
            "😊 ¿Qué producto le interesa?"
        )

    # Servientrega — 大部份外省小件用 Servientrega
    servientrega_triggers = ["servientrega", "envian por servientrega", "envio por servientrega", "usan servientrega"]
    if any(tr in t for tr in servientrega_triggers):
        return (
            "Sí, para la mayoría de pedidos a provincia usamos Servientrega 📦\n"
            "El envío cuesta aproximadamente $8 (puede variar según distancia y cantidad).\n"
            "Me indica su ciudad y el producto que le interesa para darle detalles. "
            f"Puede ver los productos aquí: {VENTAX_CATALOG}"
        )

    # Transporte pesado — 尽量满足，可能需协商；偏远地区可能退而求其次用 Servientrega
    pesado_triggers = ["transporte pesado", "trasporte pesado", "transporte pesada", "carga pesada", "envio pesado", "bultos grandes"]
    if any(tr in t for tr in pesado_triggers):
        return (
            "Podemos intentar coordinar transporte pesado según su necesidad 😊\n"
            "A veces hay que negociar porque algunos no llegan a zonas apartadas; "
            "en ese caso usamos Servientrega como alternativa.\n"
            "Me indica qué producto, cantidad y ciudad para ver la mejor opción. "
            f"Catálogo: {VENTAX_CATALOG}"
        )

    shipping_triggers = [
        "hacen envio", "hacer envio", "envio", "envios", "enviar",
        "pueden enviar", "puede enviar", "puedo enviar",
        "despacho", "entrega a", "llega", "llegar", "demora",
        "a mi ciudad", "a provincia", "servientrega", "tramaco",
        "deliberi", "delivery", "deliveri",
        "asen envio", "hacen deliberi",
        "mandan a", "despachan a", "envian a",
        "entregas en", "envios a",
        "envio a provincia", "envian a provincia",
        "transporte", "encomienda",
    ]
    if any(tr in t for tr in shipping_triggers):
        return (
            "Sí, hacemos envíos a todo Ecuador 📦\n"
            "El envío cuesta aproximadamente $8 (puede variar según distancia y cantidad).\n"
            "Me indica su ciudad y el producto que le interesa para darle detalles. "
            f"Puede ver los productos aquí: {VENTAX_CATALOG}"
        )

    # 支付方式类
    payment_triggers = [
        "forma de pago", "formas de pago", "como pago",
        "transferencia", "deposito", "efectivo", "contra entrega",
        "cuenta para", "numero de cuenta", "cuenta bancaria",
    ]
    if any(tr in t for tr in payment_triggers):
        return (
            "Aceptamos transferencia bancaria y depósito 🏦 "
            "Una vez confirmado el pago, despachamos su pedido. "
            f"Puede ver productos y precios aquí: {VENTAX_CATALOG}"
        )

    wholesale_triggers = [
        "por mayor", "al por mayor", "mayorista", "precio por mayor",
        "docena", "bulto", "cantidad minima",
    ]
    if any(tr in t for tr in wholesale_triggers):
        return (
            "Sí, manejamos precios por mayor 💰 "
            "Me indica qué producto y cantidad le interesa para darle precio. "
            f"Vea el catálogo aquí: {VENTAX_CATALOG}"
        )

    return None


def _catalog_redirect_reply(user_text: str) -> str | None:
    """
    通用咨询类 — 引导去浏览网站
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 80:
        return None
    catalog_triggers = [
        "informacion", "catalogo", "precios", "precio",
        "que venden", "que tienen", "ver productos", "ver catalogo",
        "lista de precios", "que hay", "que ofrecen",
        "ver todo", "productos que tienen", "que productos tienen",
        "mercaderia nueva", "que le llego", "que llego",
        "me puede indicar precio", "indicar precio", "indicame precio",
        "pagina web", "pagina", "link", "enlace",
    ]
    if not any(tr in t for tr in catalog_triggers):
        return None
    # 若已有产品词，交给 _fast_reply
    if any(p in t for p in ["tiene ", "vende ", "disponible ", "hay ", "lapiz", "bolso", "cartera", "juguete"]):
        return None
    replies = [
        f"Con gusto 😊 Aquí está el catálogo con fotos y precios: {VENTAX_CATALOG}",
        f"Puede ver todo aquí: {VENTAX_CATALOG} Ahí están los productos y precios.",
    ]
    return random.choice(replies)


def _off_topic_reply(user_text: str) -> str | None:
    """
    离题/无法回答类问题快速回复 — 礼貌引导回产品
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 120:
        return None
    EC_TZ = timezone(timedelta(hours=-5))
    now_ec = datetime.now(EC_TZ)
    DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    # "que hora atiende/abren/cierran" → 营业时间（已在 _business_faq_reply 处理）
    # CHANGE: "hasta que hora" 一定是问店铺营业时间，绝不返回当前时间
    if "hasta que hora" in t or "asta que hora" in t:
        return None
    # 这里只处理纯时间查询 "que hora es"
    time_triggers = ["q hora", "que hora", "hora es", "la hora"]
    biz_hour_words = ["atiende", "atienden", "abre", "abren", "cierra", "cierran", "atencion"]
    if any(tr in t for tr in time_triggers) and not any(bw in t for bw in biz_hour_words):
        h = now_ec.strftime("%I:%M %p")
        return (
            f"Son las {h} (hora Ecuador 🇪🇨). "
            f"¿En qué le puedo ayudar? 😊"
        )
    date_triggers = ["q dia", "que dia", "dia es", "que fecha", "fecha es", "fecha de hoy"]
    if any(tr in t for tr in date_triggers):
        d = DIAS[now_ec.weekday()]
        return (
            f"Hoy es {d} {now_ec.day} de {MESES[now_ec.month]} de {now_ec.year} 😊 "
            f"¿Le puedo ayudar con algo?"
        )
    weather_triggers = ["clima", "tiempo hace", "llueve"]
    if any(tr in t for tr in weather_triggers):
        return (
            "No tengo info del clima 😅 pero sí le puedo ayudar con productos. "
            f"¿Qué busca? {VENTAX_CATALOG}"
        )
    queue_triggers = ["hay cola", "hay fila", "mucha gente", "esta lleno", "lleno el local"]
    if any(tr in t for tr in queue_triggers):
        return (
            "No tengo esa info en tiempo real 😊 Pero puede hacer su pedido en línea "
            f"y lo recoge sin esperar: {VENTAX_CATALOG}\n"
            "O escríbanos por WhatsApp para consultar."
        )
    troll_triggers = ["t-rex", "dinosaurio", "fosil", "alien", "ovni", "roblox"]
    if any(tt in t for tt in troll_triggers):
        replies = [
            "Jaja 😄 ¿En qué le puedo ayudar con productos?",
            f"😄 Aquí vendemos productos, puede verlos aquí: {VENTAX_CATALOG}",
        ]
        return random.choice(replies)
    # "cerrado/abierto/horario" 已在 _business_faq_reply 处理，这里不再重复
    return None


def _compliment_reply(user_text: str) -> str | None:
    """赞美/评论类消息快速回复 — 不调 LLM"""
    t = _normalize(user_text.strip())
    if not t or len(t) > 120:
        return None
    compliment_triggers = [
        "buena publicidad", "buen trabajo", "excelente", "muy buenos",
        "sigan asi", "felicit", "bonito", "me encanta",
        "que buenos", "buenisimo",
        "espero siga creciendo", "siga creciendo", "mucho exito",
        "tremenda ganga", "buena ganga", "que ganga",
    ]
    if not any(ct in t for ct in compliment_triggers):
        return None
    replies = [
        "Muchas gracias amiga, nos alegra mucho 😊 Si le interesa algún producto, con gusto le ayudo.",
        "Gracias por sus palabras 😊 Aquí estamos para servirle. ¿Busca algún producto?",
        f"Muchas gracias 😊 Si desea ver productos: {VENTAX_CATALOG}",
    ]
    return random.choice(replies)


def _contact_reply(user_text: str) -> str | None:
    """联系方式/WhatsApp请求快速回复"""
    t = _normalize(user_text.strip())
    if not t or len(t) > 100:
        return None
    contact_triggers = [
        "contacto", "numero", "whatsapp", "telefono",
        "celular", "llamar", "comunicar",
    ]
    if not any(ct in t for ct in contact_triggers):
        return None
    # 排除含产品/价格关键词（如 "contacto para comprar" → 更适合 LLM）
    if any(w in t for w in ["producto", "precio", "comprar", "envio"]):
        return None
    return (
        "Puede escribirnos por aquí mismo o visitar nuestra tienda 😊\n"
        "📍 Novedades Cristy — Lorenzo de Garaycoa 1521 y Colón, Guayaquil\n"
        f"O vea los productos en: {VENTAX_CATALOG}"
    )


def _comment_fallback_reply(user_text: str) -> str | None:
    """
    评论/meme/非问题消息快速兜底 — 避免无谓 LLM 调用
    检测不含购买意图的闲聊/评论/sticker 等
    """
    t = _normalize(user_text.strip())
    if not t:
        return None
    # sticker / 纯 emoji → 快速友好回复
    raw = user_text.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return f"😊 ¿Le interesa algún producto? Vea el catálogo: {VENTAX_CATALOG}"
    # 超长消息（copy-paste spam / 无关话题）
    if len(t) > 150 and not any(w in t for w in ["producto", "precio", "tiene", "envio", "quiero", "busco"]):
        return f"😊 ¿Le puedo ayudar con algún producto? Vea el catálogo: {VENTAX_CATALOG}"
    # 购买意图检测 — 有意图的不拦截，留给 LLM
    buy_intent = [
        "quiero", "busco", "necesito", "tiene", "tienen", "vende",
        "precio", "cuanto", "envio", "enviar", "comprar", "pedir",
        "donde", "ubicacion", "ayuda", "como",
    ]
    if any(bi in t for bi in buy_intent):
        return None
    # 评论/感叹/闲聊（无购买意图的短消息）
    comment_triggers = [
        "sin plata", "no tengo plata", "no tengo dinero",
        "ganga", "yapa", "barato", "caro",
        "mi papa", "mi mama", "mi nieta", "mi abuela", "mi hijo",
        "jaja", "jeje", "xd", "lol",
        "viendo esto", "esperando", "compartiendolo",
        "falta mas", "falta",
        "para no ir", "para eso me compro", "para eso",
        "no son muy", "no son tan", "no sirve", "no funciona",
        "que pasaria", "que pasa si",
        "plata", "dinero",
    ]
    if any(ct in t for ct in comment_triggers):
        replies = [
            f"Jaja 😄 Si le interesa algo, aquí estoy para ayudarle. Catálogo: {VENTAX_CATALOG}",
            f"😊 ¿Le interesa algún producto? {VENTAX_CATALOG}",
            f"😊 ¿En qué le puedo ayudar? {VENTAX_CATALOG}",
        ]
        return random.choice(replies)
    # 消息全大写 + 无购买意图 → 可能是情绪表达
    if raw.isupper() and len(t) < 50 and "?" not in raw:
        return f"😊 ¿En qué le puedo ayudar? Vea los productos aquí: {VENTAX_CATALOG}"
    return None


def _thanks_reply(user_text: str) -> str | None:
    """感谢类消息快速回复 — 人性化收尾"""
    t = _normalize(user_text.strip())
    thanks_triggers = ["gracias", "muchas gracias", "ok gracias", "ok, gracias", "perfecto", "genial", "dale"]
    if not any(tr in t for tr in thanks_triggers) or len(t) > 25:
        return None
    replies = [
        "De nada amiga 😊 Cualquier cosa me escribe.",
        "Con gusto! Si necesita algo más, aquí estoy.",
        "De nada! Que tenga buen día.",
    ]
    return random.choice(replies)


def chat(user_message: str, model: str | None = None, use_fast_path: bool = True, _force_lite: bool = False, history: list | None = None) -> str:
    """
    客服回复入口
    :param user_message: 用户消息
    :param model: 可选，覆盖默认模型
    :param use_fast_path: 为 True 时，产品类问题直接返回链接，不调 LLM
    :param _force_lite: 内部用，400 重试时强制用极简 prompt
    :param history: 对话历史 [{"role":"user"/"assistant","content":"..."},...]
    """
    def _ret(val: str) -> str:
        return _sanitize_reply_urls(val) if val else val

    if use_fast_path:
        identity = _identity_reply(user_message)
        if identity:
            return _ret(identity)
        escalation = _escalation_reply(user_message)
        if escalation:
            return _ret(escalation)
        human_followup = _human_support_followup_reply(user_message)
        if human_followup:
            return _ret(human_followup)
        call_order = _call_for_order_reply(user_message)
        if call_order:
            return _ret(call_order)
        # 商务FAQ（运费/营业时间/ubicación/pago）必须在 _fast_reply 之前，
        # 否则 "cuanto cuesta el envio" 会被产品搜索拦截
        biz = _business_faq_reply(user_message)
        if biz:
            return _ret(biz)
        fast = _fast_reply(user_message)
        if fast:
            return _ret(fast)
        greeting = _greeting_reply(user_message)
        if greeting:
            return _ret(greeting)
        help_r = _help_reply(user_message)
        if help_r:
            return _ret(help_r)
        catalog = _catalog_redirect_reply(user_message)
        if catalog:
            return _ret(catalog)
        compliment = _compliment_reply(user_message)
        if compliment:
            return _ret(compliment)
        contact = _contact_reply(user_message)
        if contact:
            return _ret(contact)
        off_topic = _off_topic_reply(user_message)
        if off_topic:
            return _ret(off_topic)
        thanks = _thanks_reply(user_message)
        if thanks:
            return _ret(thanks)
        # 最后一道防线：评论/meme/非问题 → 快速兜底，避免无谓 LLM 超时
        comment = _comment_fallback_reply(user_message)
        if comment:
            return _ret(comment)

    # 所有快速路径都未匹配 → 记录原始消息供后续分析/添加新缩略语
    _log_unmatched(user_message)

    api_key = _get_api_key()
    if not api_key:
        return "Lo siento, no está configurada la API. Revisa OPENROUTER_API_KEY."

    intent = _detect_intent(user_message)
    mdl = model or DEFAULT_MODEL
    url = "https://openrouter.ai/api/v1/chat/completions"

    def _do_request(system_content: str, max_tok: int = 180, timeout_sec: int = API_TIMEOUT) -> str:
        msgs = [{"role": "system", "content": system_content}]
        if history:
            msgs.extend(history)
        msgs.append({"role": "user", "content": user_message})
        payload = {
            "model": mdl,
            "messages": msgs,
            "max_tokens": max_tok,
            "temperature": 0.3,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://ventax.pages.dev",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            out = json.loads(resp.read().decode())
            text = out.get("choices", [{}])[0].get("message", {}).get("content", "")
            raw = text.strip() or "¿En qué más puedo ayudarte?"
            # CHANGE: 后处理替换泛名词 ?q=，及 volso→bolso 等拼写纠错
            for bad, good in [("volso", "bolso"), ("boldo", "bolso"), ("volsos", "bolso"), ("voldo", "bolso")]:
                raw = raw.replace(f"?q={bad}", f"?q={good}")
            return _sanitize_reply_urls(raw)

    sys_prompt = _build_system_prompt(intent, lite=_force_lite)

    for attempt in range(API_RETRIES + 1):
        try:
            return _do_request(sys_prompt)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            if e.code in (400, 402) and not _force_lite:
                try:
                    return _do_request(_build_system_prompt(intent, lite=True), max_tok=128)
                except Exception:
                    return chat(user_message, model=FALLBACK_MODEL, use_fast_path=False, _force_lite=True)
            if e.code == 404:
                return chat(user_message, model=FALLBACK_MODEL, use_fast_path=False)
            if e.code == 408:
                return _get_llm_fallback_reply(intent)
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
                continue
            hint = f" ({err_body})" if err_body else ""
            return _get_llm_fallback_reply(intent)
        except (socket.timeout, TimeoutError, OSError, urllib.error.URLError):
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
                continue
            return _get_llm_fallback_reply(intent)
        except Exception:
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
                continue
            return _get_llm_fallback_reply(intent)


def chat_with_image(user_message: str, image_base64: str, mime_type: str = "image/jpeg") -> str:
    """
    图片识别入口 — 用 Gemini Flash（便宜40倍）
    :param user_message: 用户附带的文字（可为空）
    :param image_base64: 图片的 base64 编码
    :param mime_type: 图片 MIME 类型
    """
    api_key = _get_api_key()
    if not api_key:
        return "Lo siento, no está configurada la API."

    text_part = user_message.strip() if user_message else "¿Qué producto es este? Descríbelo brevemente."
    sys_prompt = (
        "Eres la asesora de VentaX Ecuador. El cliente envió una foto. "
        "Identifica el producto en la imagen y responde en español, breve y amigable. "
        "Si reconoces el producto, dile que puede verlo en el catálogo: "
        f"{VENTAX_CATALOG} "
        "Si no lo reconoces, pregúntale qué busca."
    )
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": text_part},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime_type};base64,{image_base64}"
                }},
            ]},
        ],
        "max_tokens": 200,
        "temperature": 0.3,
    }
    url = "https://openrouter.ai/api/v1/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://ventax.pages.dev",
        },
        method="POST",
    )
    _BANNED_Q = frozenset({"productos", "electrodomesticos", "electrodomestico", "hogar", "ropa", "juguetes"})
    for attempt in range(API_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                out = json.loads(resp.read().decode())
                text = out.get("choices", [{}])[0].get("message", {}).get("content", "")
                reply = text.strip() or f"No pude identificar el producto. Puede buscarlo aquí: {VENTAX_CATALOG}"
                # NOTE: 若识别出产品，将主链接替换为 ?q=关键词 以提升搜索精准度
                norm_kw = _extract_product_from_description(reply)
                if norm_kw and norm_kw not in _BANNED_Q:
                    fixed_kw = _fix_product_keyword(norm_kw)
                    url_kw = _keyword_for_url(reply, norm_kw, fixed_kw)
                    q_encoded = quote(url_kw, safe="")
                    reply = reply.replace(VENTAX_CATALOG, f"{VENTAX_CATALOG}?q={q_encoded}")
                return _sanitize_reply_urls(reply)
        except Exception:
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
                continue
            return (
                "En el mercado a veces es difícil encontrar algo exactamente igual, "
                f"pero en nuestra página tenemos muchos productos nuevos donde seguro encuentra algo similar. ¡Échale un vistazo aquí! 👉 {VENTAX_CATALOG}"
            )


def chat_with_voice(audio_base64: str, mime_type: str = "audio/ogg", history: list | None = None) -> tuple[str, str]:
    """
    语音消息入口 — Gemini Flash 理解音频并回复
    :param audio_base64: 音频 base64
    :param mime_type: 音频 MIME (通常 audio/ogg; codecs=opus)
    :param history: 对话历史
    :return: (reply, transcription) 回复文本和转录文本
    """
    api_key = _get_api_key()
    if not api_key:
        return "Lo siento, no está configurada la API.", ""

    clean_mime = mime_type.split(";")[0].strip() if mime_type else "audio/ogg"

    sys_prompt = (
        "Eres Carolina, asesora de Novedades Cristy / VentaX Ecuador. "
        "El cliente envió un audio por WhatsApp. "
        "Primero transcribe lo que dice, luego responde.\n\n"
        "DATOS DE LA TIENDA (usa estos datos reales al responder):\n"
        "- Tienda: Novedades Cristy\n"
        "- Dirección: Lorenzo de Garaycoa 1521 y Colón, Guayaquil, Ecuador\n"
        "- Google Maps: https://maps.app.goo.gl/n1v5m8E4QS9vKnvZ6\n"
        "- Horario: Lunes a Sábado 9:00 AM — 6:30 PM | Domingo 9:30 AM — 5:00 PM\n"
        "- Envío: A todo Ecuador, costo aprox. $8 (varía según distancia/cantidad). "
        "Guayaquil: siguiente día hábil. Otras ciudades: 2-3 días hábiles.\n"
        "- Pago: Transferencia bancaria o depósito.\n"
        "- Catálogo en línea: " + VENTAX_CATALOG + "\n"
        "- WhatsApp atención personalizada: 0939962405\n\n"
        "Formato obligatorio:\n"
        "[TRANSCRIPCIÓN]: (lo que dijo el cliente)\n"
        "[RESPUESTA]: (tu respuesta como asesora)\n\n"
        "Responde en español, breve y amigable. "
        "Si menciona un producto concreto (ej. agenda, lapiz), dale: " + VENTAX_CATALOG + "?q=PRODUCTO. "
        "NUNCA uses 'productos' como palabra clave. Si no menciona producto concreto, usa solo: " + VENTAX_CATALOG
    )

    msgs = [{"role": "system", "content": sys_prompt}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": [
        {"type": "text", "text": "El cliente envió este audio:"},
        {"type": "image_url", "image_url": {
            "url": f"data:{clean_mime};base64,{audio_base64}"
        }},
    ]})

    payload = {
        "model": VISION_MODEL,
        "messages": msgs,
        "max_tokens": 300,
        "temperature": 0.3,
    }
    url = "https://openrouter.ai/api/v1/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://ventax.pages.dev",
        },
        method="POST",
    )
    for attempt in range(API_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode())
                text = out.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if not text:
                    return f"No pude entender el audio. ¿Puedes escribirme? {VENTAX_CATALOG}", ""
                transcription = ""
                reply = text
                if "[TRANSCRIPCIÓN]:" in text and "[RESPUESTA]:" in text:
                    parts = text.split("[RESPUESTA]:")
                    transcription = parts[0].replace("[TRANSCRIPCIÓN]:", "").strip()
                    reply = parts[1].strip() if len(parts) > 1 else text
                for bad, good in [("volso", "bolso"), ("boldo", "bolso"), ("volsos", "bolso"), ("voldo", "bolso")]:
                    reply = reply.replace(f"?q={bad}", f"?q={good}")
                return _sanitize_reply_urls(reply), transcription
        except Exception:
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
                continue
            return "Disculpa, no pude procesar tu audio. ¿Puedes escribirme tu consulta? 😊", ""


def main():
    """REPL 测试"""
    print("VentaX 客服机器人 — 输入消息，Esc 或 quit 退出\n")
    while True:
        try:
            msg = input("Cliente> ").strip()
            if not msg or msg.lower() in ("quit", "exit", "esc"):
                break
            reply = chat(msg)
            print("Bot>", reply, "\n")
        except (KeyboardInterrupt, EOFError):
            break
    print("Bye.")


# === 多机器人协同接口 ===
# 其他机器人可 import chat 作为专用客服节点
# 例: from ventax_customer_bot import chat; reply = chat(user_msg)
# 后续可加 router：根据意图分发到 ventax_customer_bot / 其他 bot

if __name__ == "__main__":
    main()

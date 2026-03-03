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
import socket
from datetime import datetime, timezone, timedelta


def _normalize(text: str) -> str:
    """去除重音符号并小写：dónde→donde, qué→que, cómo→como"""
    nfkd = unicodedata.normalize('NFKD', text.lower())
    return ''.join(c for c in nfkd if not unicodedata.combining(c))

# 非产品词（动词、代词等），提取关键词时排除
_NON_PRODUCT_WORDS = frozenset({
    "si", "no", "hola", "gracias", "ok", "usted", "ustedes", "tienen", "tiene",
    "venden", "vende", "tener", "vender", "producto", "productos", "que", "qué",
    "informacion", "información", "precio", "cuanto", "cuánto",
    "comprar", "ver", "buscar", "conseguir", "pedir",
})

# 混合模型：文字用 GPT-4o-mini（质量高），图片用 Gemini Flash（便宜40倍）
DEFAULT_MODEL = "openai/gpt-4o-mini"
VISION_MODEL = "google/gemini-2.0-flash-001"
FALLBACK_MODEL = "openai/gpt-4o-mini"
VENTAX_CATALOG = "https://ventax.pages.dev/pwa_cart/"

API_TIMEOUT = 25
API_RETRIES = 1
API_RETRY_DELAY = 2

# 产品类极简 prompt（快速路径不调 LLM，此处仅作 fallback）
SYSTEM_PROMPT_PRODUCT = """Eres la asesora de VentaX Ecuador. Una sola regla:
Si el cliente menciona producto (tiene X, qué venden, q producto, lapiz, bolso, etc):
Responde exactamente 3 líneas:
1. Sí amiga, claro 👇
2. https://ventax.pages.dev/pwa_cart/?q=KEYWORD
3. Abre y veras modelos/fotos al instante.
Usa la palabra del producto como KEYWORD. Nada más."""

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
        prompt = base + "Si mencionan producto, da el enlace: https://ventax.pages.dev/pwa_cart/"
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
            f"{VENTAX_CATALOG}"
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
    try:
        cfg_path = os.path.join(
            os.path.dirname(__file__), "..", "..", ".openclaw", "openclaw.json"
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("env", {}).get("OPENROUTER_API_KEY")
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

    # tiene X, vende X（X 为具体产品名）
    m = re.search(r"tiene\s+(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)
    m = re.search(r"vende[ns]?\s+(\w+)", text, re.I)
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
    # "como salen las X", "a como da las X", "cuanto cuestan las X"
    m = re.search(r"(?:como\s+sale[ns]?|a\s+como\s+(?:da|dan|sale|salen)|cuanto\s+cuesta[ns]?)\s+(?:las?\s+|los?\s+|un[oa]?\s+)?(\w+)", text, re.I)
    if m and m.group(1) not in _NON_PRODUCT_WORDS:
        return m.group(1)

    products = ("lapiz", "bolso", "cartera", "chupon", "maquillaje", "monedero", "juguete", "mochila", "cuaderno", "reloj", "zapato", "ropa", "moto", "espuma", "globo", "carioca", "karioca", "pistola", "pinata", "pinateria", "torta", "anilina")
    for w in re.findall(r"\b[a-záéíóúñ]+\b", text):
        if w in products:
            return w
    return "productos"


def _fast_reply(user_text: str) -> str | None:
    """
    快速规则匹配 — 不调 LLM，直接返回链接
    当检测到明确的产品意图时使用
    """
    t = _normalize(user_text.strip())
    if not t or len(t) < 3:
        return None
    triggers = [
        "tiene", "tienen", "vende", "venden", "producto", "productos",
        "lapiz", "bolso", "cartera", "chupon", "maquillaje", "juguete", "mochila", "monedero",
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
    kw = _extract_product_keyword(user_text)
    if not kw or kw in _NON_PRODUCT_WORDS:
        kw = "productos"

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
    return f"{opening}\n{VENTAX_CATALOG}?q={kw}\n{closing}"


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
        "tiene", "vende", "producto", "lapiz", "bolso", "cartera", "chupon",
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
    通用求助类消息快速回复 — 不调 LLM
    """
    t = _normalize(user_text.strip())
    if not t or len(t) > 60:
        return None
    help_triggers = [
        "me puede ayudar", "puede ayudarme", "ayudame",
        "me ayuda", "ayuda en algo", "puede ayudar en algo",
    ]
    if not any(tr in t for tr in help_triggers):
        return None
    replies = [
        "Claro amiga, con gusto 😊 ¿Qué producto busca o qué necesita?",
        "Sí, aquí estoy para ayudarle. ¿Qué anda buscando?",
        "Hola! ¿En qué le puedo ayudar? Puede ver el catálogo aquí: " + VENTAX_CATALOG,
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

    # 运费价格类
    shipping_cost_triggers = [
        "cuanto cuesta el envio", "costo del envio",
        "cuanto es el envio", "precio del envio",
        "cuanto cobra", "cobran envio", "valor del envio",
    ]
    if any(tr in t for tr in shipping_cost_triggers):
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
    shipping_triggers = [
        "hacen envio", "hacer envio", "envio", "envios", "enviar",
        "pueden enviar", "puede enviar", "puedo enviar",
        "despacho", "entrega a", "llega", "llegar", "demora",
        "cuanto tarda", "tiempo de entrega",
        "a mi ciudad", "a provincia", "servientrega", "tramaco",
        "deliberi", "delivery", "deliveri",
        "asen envio", "hacen deliberi",
        "mandan a", "despachan a", "envian a",
        "entregas en", "envios a",
    ]
    if any(tr in t for tr in shipping_triggers):
        return (
            "Sí, hacemos envíos a todo Ecuador 📦 "
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
    if any(p in t for p in ["tiene ", "vende ", "lapiz", "bolso", "cartera", "juguete"]):
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
    time_triggers = ["q hora", "que hora", "hora es", "la hora"]
    if any(tr in t for tr in time_triggers):
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
    if any(w in t for w in ["cerrado", "serrado", "cerrada", "serrada", "abierto", "abierta", "horario"]):
        return (
            "Nuestro horario es de lunes a sábado 😊\n"
            "📍 Novedades Cristy — Lorenzo de Garaycoa 1521 y Colón, Guayaquil\n"
            f"También puede comprar en línea: {VENTAX_CATALOG}"
        )
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


def chat(user_message: str, model: str | None = None, use_fast_path: bool = True, _force_lite: bool = False) -> str:
    """
    客服回复入口
    :param user_message: 用户消息
    :param model: 可选，覆盖默认模型
    :param use_fast_path: 为 True 时，产品类问题直接返回链接，不调 LLM
    :param _force_lite: 内部用，400 重试时强制用极简 prompt
    """
    if use_fast_path:
        identity = _identity_reply(user_message)
        if identity:
            return identity
        fast = _fast_reply(user_message)
        if fast:
            return fast
        # 商务FAQ（ubicación/envíos/pago）优先于问候，避免 "hola donde están" 被问候拦截
        biz = _business_faq_reply(user_message)
        if biz:
            return biz
        greeting = _greeting_reply(user_message)
        if greeting:
            return greeting
        help_r = _help_reply(user_message)
        if help_r:
            return help_r
        catalog = _catalog_redirect_reply(user_message)
        if catalog:
            return catalog
        compliment = _compliment_reply(user_message)
        if compliment:
            return compliment
        contact = _contact_reply(user_message)
        if contact:
            return contact
        off_topic = _off_topic_reply(user_message)
        if off_topic:
            return off_topic
        thanks = _thanks_reply(user_message)
        if thanks:
            return thanks
        # 最后一道防线：评论/meme/非问题 → 快速兜底，避免无谓 LLM 超时
        comment = _comment_fallback_reply(user_message)
        if comment:
            return comment

    api_key = _get_api_key()
    if not api_key:
        return "Lo siento, no está configurada la API. Revisa OPENROUTER_API_KEY."

    intent = _detect_intent(user_message)
    mdl = model or DEFAULT_MODEL
    url = "https://openrouter.ai/api/v1/chat/completions"

    def _do_request(system_content: str, max_tok: int = 180, timeout_sec: int = API_TIMEOUT) -> str:
        payload = {
            "model": mdl,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message},
            ],
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
            return text.strip() or "¿En qué más puedo ayudarte?"

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
    for attempt in range(API_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                out = json.loads(resp.read().decode())
                text = out.get("choices", [{}])[0].get("message", {}).get("content", "")
                return text.strip() or f"No pude identificar el producto. Puede buscarlo aquí: {VENTAX_CATALOG}"
        except Exception:
            if attempt < API_RETRIES:
                time.sleep(API_RETRY_DELAY)
                continue
            return f"No pude procesar la imagen. Puede ver el catálogo aquí: {VENTAX_CATALOG}"


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

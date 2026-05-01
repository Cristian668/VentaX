#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PWA购物车API服务器
为PWA购物车网页提供RESTful API接口
与现有Telegram Bot系统无缝集成

逻辑关系（与 product_background_processor_gui2、7 ventaX_unified_system97 统一约定）：
- 数据：DatabaseManager 与 97 同源（database/spanish_product_database.db）；Cristy 可回退 PostgreSQL。
- 图片：pwa_cart/Ya Subio（ULTIMO 用 Cristy 子目录）；支持 ._AI.jpg（与 gui2 产出一致）；product_id 支持如 2202_AI。
- 前端产品页 URL 格式：{web_shop_base_url}/#/product/{product_id}，由 97/gui2 配置并同步到 app_settings.json。
- 详见：MD/product_background_processor_pwa_unified_system_逻辑关系.md
"""

import difflib
import os
import re
import subprocess
import sys
import json
import logging
from urllib.parse import quote
import sqlite3
import hashlib  # CHANGE: hashlib是标准库，应该始终可用，移到外面
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

# CHANGE: API 响应缓存，减少重复请求对 DB 的压力
_API_CACHE = {}
_API_CACHE_TTL_PRODUCTS = 60   # 产品列表缓存 60 秒
_API_CACHE_TTL_BANK = 300     # 银行信息缓存 5 分钟


def _products_list_effective_page_limit(req):
    """
    与前端分页对齐：app.js 使用 offset+limit，后端原先只认 page，会导致永远第 1 页；
    且缓存 key 未含 offset 时，所有 offset 请求命中同一缓存，返回重复数据并触发海量重试。
    """
    try:
        limit = int(req.args.get('limit', 30))
        if limit < 1:
            limit = 30
    except (TypeError, ValueError):
        limit = 30
    offset_val = req.args.get('offset', type=int)
    if offset_val is not None and offset_val >= 0:
        page = (offset_val // limit) + 1
    else:
        try:
            page = int(req.args.get('page', 1))
        except (TypeError, ValueError):
            page = 1
        if page < 1:
            page = 1
    return page, limit


def _products_list_cache_key(req):
    pg, lim = _products_list_effective_page_limit(req)
    return f"products_{req.args.get('supplier') or ''}_{req.args.get('search') or ''}_{req.args.get('category') or ''}_{pg}_{lim}"

# CHANGE: 暂时註销 SQLite 产品数据，产品列表/详情仅用 PostgreSQL（购物车/订单/登录仍用 CartManager 内 db）
USE_SQLITE_FOR_PRODUCTS = False

# ULTIMO_IMAGE_DIR 在 PWA_YA_SUBIO_* 定义后赋值

# 尝试导入 psycopg2（ULTIMO 产品从 PostgreSQL 读取时使用）
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    RealDictCursor = None
    PSYCOPG2_AVAILABLE = False

# 添加模块路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# CHANGE: 产品图片目录改为 pwa_cart 内，与 97/gui2 保存与移动一致
PWA_YA_SUBIO_BASE = os.path.normpath(os.path.join(current_dir, 'pwa_cart', 'Ya Subio'))
PWA_YA_SUBIO_CRISTY = os.path.normpath(os.path.join(PWA_YA_SUBIO_BASE, 'Cristy'))
ULTIMO_IMAGE_DIR = PWA_YA_SUBIO_CRISTY

# 尝试导入Flask
try:
    from flask import Flask, jsonify, request, redirect
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("⚠️ Flask未安装，请运行: pip install flask flask-cors")

# CHANGE: 尝试导入JWT库（sys 已在文件顶部导入）
try:
    import jwt
    import secrets
    JWT_AVAILABLE = True
    print(f"✅ JWT库导入成功，版本: {jwt.__version__}")  # 控制台输出
    print(f"✅ JWT库位置: {jwt.__file__ if hasattr(jwt, '__file__') else 'N/A'}")  # 控制台输出
except ImportError as e:
    JWT_AVAILABLE = False
    print(f"⚠️ JWT库未安装，请运行: pip install PyJWT, 错误: {e}")  # 控制台输出
    print(f"💡 安装命令: {sys.executable} -m pip install PyJWT")  # 控制台输出
except Exception as e:
    JWT_AVAILABLE = False
    import traceback
    print(f"❌ JWT库导入失败（非ImportError）: {e}")  # 控制台输出
    print(traceback.format_exc())  # 控制台输出

# 导入现有模块
try:
    from database_manager import DatabaseManager
    from cart_manager import CartManager
except ImportError as e:
    print(f"⚠️ 导入模块失败: {e}")
    DatabaseManager = None
    CartManager = None

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# CHANGE: 记录JWT库状态（logger初始化后）
if JWT_AVAILABLE:
    try:
        import jwt
        logger.info(f"✅ JWT库可用，版本: {jwt.__version__}")
        print(f"✅ JWT库可用，版本: {jwt.__version__}")  # 控制台输出
    except Exception:
        logger.warning("⚠️ JWT_AVAILABLE=True但无法导入jwt模块")
        print("⚠️ JWT_AVAILABLE=True但无法导入jwt模块")  # 控制台输出
else:
    logger.warning("⚠️ JWT库不可用，JWT_AVAILABLE=False")
    print("⚠️ JWT库不可用，JWT_AVAILABLE=False")  # 控制台输出

# CHANGE: 数据库 ruta_imagen 可能带方括号，实际文件在 pwa_cart/Ya Subio 无括号；统一去掉方括号便于匹配
def _normalize_image_filename(name):
    """去掉文件名两侧方括号，使 DB 路径与 pwa_cart/Ya Subio 实际文件名一致；支持全角【】"""
    if not name or not isinstance(name, str):
        return name
    s = name.strip()
    if len(s) >= 2 and s[0] == '[' and s[-1] == ']':
        return s[1:-1].strip()
    # CHANGE: 支持全角括号 【30568】-> 30568，便于 DB 路径与磁盘文件名匹配
    s = re.sub(r'[【\[](\d+)[】\]]', r'\1', s)
    return s


def _normalize_base_ai_al(base):
    """CHANGE: 文件名 ._AI 与 ._Al 等价（磁盘可能存为 ._Al.jpg），便于匹配"""
    if not base or not isinstance(base, str):
        return (base or '').strip().lower()
    s = base.strip().lower()
    if s.endswith('._al') and not s.endswith('._ai'):
        return s[:-4] + '._ai'
    return s


def _product_id_candidates(pid):
    """CHANGE: 根据 URL 传入的 product_id（如 10060_Al、10060_A）生成候选 key，用于查 DB/PG。
    前端/Telegram 可能用 10060_Al、10060_A，DB 存 10060 或 10060._AI，需多候选匹配。"""
    if not pid or not isinstance(pid, str):
        return [pid]
    s = (pid or '').strip()
    candidates = [s]
    # 规范 _Al/_A 为 _AI（与 DB/PG 可能存的 codigo 一致）
    low = s.lower()
    if low.endswith('_al') and not low.endswith('._ai'):
        candidates.append(s[:-3] + '_AI')
    if low.endswith('_a') and len(low) > 2 and low[-3] == '_':
        candidates.append(s[:-2] + 'AI')
    # 规范为 ._AI 形式（如 10060_Al -> 10060._AI）
    if '_al' in low or '_a' in low:
        base = re.sub(r'[._\-]?(?:al|ai|a)$', '', low, flags=re.IGNORECASE).rstrip('._-')
        if base:
            candidates.append(base + '._AI')
    # 纯数字部分（10060_Al -> 10060）
    nums = re.findall(r'^\d+', s)
    if nums:
        candidates.append(nums[0])
    # 去重且保持顺序
    seen = set()
    out = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _load_display_code_mapping():
    """CHANGE: 读取 Telegram 写入的展示码->PWA key 映射，使 #/product/18bf4405 等链接能解析到真实产品。"""
    try:
        mapping_file = os.path.join(os.path.dirname(__file__), '..', 'telegram_display_code_mapping.json')
        if not os.path.isfile(mapping_file):
            return {}
        with open(mapping_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _clear_port_occupation(port: int) -> None:
    """CHANGE: 启动前自动清除占用端口的旧进程（Windows netstat+taskkill）"""
    if os.name != 'nt':
        return
    try:
        print(f"[1/2] Checking port {port}...")
        out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
        pids = set()
        needle = f":{port}"
        for line in out.splitlines():
            if needle in line and "LISTENING" in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
                print(f"      Killing PID {pid}")
            except Exception:
                pass
        if pids:
            import time
            time.sleep(0.5)
        print(f"      Port {port} free.")
    except Exception as e:
        logger.debug(f"清除端口占用时出错（可忽略）: {e}")


# CHANGE: 全局Telegram客服链接常量 - 强制使用正确的频道链接
TELEGRAM_CUSTOMER_SERVICE_LINK = "https://t.me/NovedadesCristy_gye"

# CHANGE: JWT配置
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'ventax-secret-key-change-in-production-2024')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = 24 * 7  # 7天


def _split_multi_categories(raw_value: Any) -> List[str]:
    """支持多分类：按逗号/竖线/分号/顿号切分并去重，返回稳定顺序列表。"""
    s = str(raw_value or '').strip()
    if not s:
        return []
    parts = re.split(r'[,|/;，、]+', s)
    out = []
    seen = set()
    for p in parts:
        t = str(p or '').strip()
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _normalize_multi_category_storage(raw_value: Any) -> str:
    """多分类标准存储格式：CatA, CatB, CatC；空值回 default。"""
    cats = _split_multi_categories(raw_value)
    return ', '.join(cats) if cats else 'default'


def _multi_category_contains(raw_value: Any, query_value: Any) -> bool:
    """判断 query 分类是否命中商品多分类。"""
    q = str(query_value or '').strip().lower()
    if not q:
        return True
    cats = _split_multi_categories(raw_value)
    if not cats:
        return q == 'default'
    return any(str(c).strip().lower() == q for c in cats)


def _cached_api_response(cache_key_fn, ttl):
    """CHANGE: API 响应缓存装饰器，cache_key_fn(request) 返回缓存 key"""
    def decorator(f):
        def wrapped(*a, **kw):
            from flask import request
            key = cache_key_fn(request)
            if key in _API_CACHE:
                exp, data = _API_CACHE[key]
                if exp > time.time():
                    return jsonify(data)
            result = f(*a, **kw)
            resp = result[0] if isinstance(result, tuple) else result
            try:
                d = resp.get_json() if hasattr(resp, 'get_json') else None
                if d and d.get('success'):
                    _API_CACHE[key] = (time.time() + ttl, d)
            except Exception:
                pass
            return result
        return wrapped
    return decorator


class PWACartAPIServer:
    """PWA购物车API服务器类"""
    
    def __init__(self, host='127.0.0.1', port=5000, debug=False):
        """初始化API服务器"""
        self.host = host
        self.port = port
        self.debug = debug
        
        # 记录当前工作目录和模块路径
        logger.info(f"📁 API服务器初始化: 工作目录={os.getcwd()}")
        logger.info(f"📁 API服务器初始化: 模块目录={os.path.dirname(os.path.abspath(__file__))}")
        
        # 初始化数据库和购物车管理器
        # NOTE: 暂时註销 SQLite 产品数据（USE_SQLITE_FOR_PRODUCTS=False），产品仅从 PostgreSQL 读；db 仍由 CartManager 提供供购物车/订单/登录用
        if DatabaseManager and USE_SQLITE_FOR_PRODUCTS:
            self.db = DatabaseManager()
            logger.info(f"📁 DatabaseManager数据库路径: {self.db.db_path}")
            logger.info(f"📁 数据库文件存在: {os.path.exists(self.db.db_path)}")
            # CHANGE: 验证订单ID生成函数是否正确
            try:
                from utils import generate_unified_order_id  # type: ignore
                test_order_id = generate_unified_order_id("ORD", 1)
                parts = test_order_id.split('_')
                if len(parts) == 4:
                    logger.info(f"✅ 订单ID生成函数验证通过: {test_order_id} (新格式)")
                    print(f"✅ 订单ID生成函数验证通过: {test_order_id} (新格式)")
                else:
                    logger.warning(f"⚠️ 订单ID生成函数格式异常: {test_order_id} (部分数: {len(parts)})")
                    print(f"⚠️ 订单ID生成函数格式异常: {test_order_id} (部分数: {len(parts)})")
            except ImportError as e:
                logger.warning(f"⚠️ 无法导入generate_unified_order_id: {e}，将在需要时使用database_manager中的函数")
                print(f"⚠️ 无法导入generate_unified_order_id: {e}")
            except Exception as e:
                logger.error(f"❌ 订单ID生成函数验证失败: {e}")
                print(f"❌ 订单ID生成函数验证失败: {e}")
        else:
            self.db = None
            if not USE_SQLITE_FOR_PRODUCTS:
                logger.info("📁 SQLite 产品数据已暂时註销，产品列表/详情仅用 PostgreSQL")
            else:
                logger.error("❌ DatabaseManager未可用")

        if CartManager:
            # 使用相同或由 CartManager 创建的 DatabaseManager 实例（购物车/订单/登录需 db）
            self.cart_manager = CartManager(db=self.db)
            # 若已註销 SQLite 产品，则用 CartManager 的 db 作为 self.db 供订单/登录等用
            if self.db is None and getattr(self.cart_manager, 'db', None):
                self.db = self.cart_manager.db
                logger.info(f"📁 使用 CartManager 的 db 供订单/登录: {self.db.db_path}")
            logger.info(f"✅ CartManager初始化成功: {self.cart_manager}")
            logger.info(f"📁 CartManager使用的数据库路径: {self.cart_manager.db.db_path if self.cart_manager.db else 'N/A'}")
        else:
            self.cart_manager = None
            logger.error("❌ CartManager未可用")
        
        # CHANGE: 可配置图片路径（port_config.json 或环境变量），不再写死 D:\Ya Subio
        self.product_image_dirs = []
        self.other_supplier_codes = ['Importadora_Chinito', 'IMP158', 'Importadorawoni', 'ayacuchoamoreshop', 'ecuarticulos']
        _config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'port_config.json')
        try:
            if os.path.isfile(_config_path):
                with open(_config_path, 'r', encoding='utf-8') as f:
                    _cfg = json.load(f)
                _pwa = _cfg.get('pwa_cart') or {}
                _dirs = _pwa.get('product_image_dirs')
                if isinstance(_dirs, list) and _dirs:
                    self.product_image_dirs = [os.path.normpath(str(d).strip()) for d in _dirs if str(d).strip()]
                _codes = _pwa.get('other_supplier_codes')
                if isinstance(_codes, list) and _codes:
                    self.other_supplier_codes = [str(c).strip() for c in _codes if str(c).strip()]
                # CHANGE: 排除未完成处理的产品图片（如仅OCR未白底），避免 404 影响旧产品显示
                _excl = _pwa.get('productos_exclude_file_prefixes')
                self.productos_exclude_file_prefixes = [str(p).strip().lower() for p in _excl if str(p).strip()] if isinstance(_excl, list) else []
        except Exception as e:
            logger.warning(f"⚠️ 读取 port_config.json 图片路径失败: {e}")
        if not getattr(self, 'productos_exclude_file_prefixes', None):
            _env_excl = (os.getenv('PRODUCTOS_EXCLUDE_FILE_PREFIXES') or '').strip()
            if _env_excl:
                self.productos_exclude_file_prefixes = [p.strip().lower() for p in _env_excl.split(',') if p.strip()]
            else:
                self.productos_exclude_file_prefixes = ['importadorawoni_']  # 默认排除未完成白底处理的
        if not self.product_image_dirs and os.getenv('PWA_PRODUCT_IMAGE_DIRS'):
            self.product_image_dirs = [os.path.normpath(p.strip()) for p in os.getenv('PWA_PRODUCT_IMAGE_DIRS', '').split(',') if p.strip()]
        # CHANGE: 方案 A 云端部署时用 R2 或 Cloudflare Pages；图片 URL 指向云端，不需本机 /api/images/
        self.r2_image_base_url = (os.getenv('R2_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
        self.pages_image_base_url = (os.getenv('PAGES_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
        # CHANGE: 重置密码链接固定指向前端地址（如 https://ventax.pages.dev/pwa_cart），邮件/响应都用此 base
        _reset_base = (os.getenv('RESET_LINK_BASE_URL', '') or '').strip().rstrip('/')
        if not _reset_base:
            try:
                if os.path.isfile(_config_path):
                    with open(_config_path, 'r', encoding='utf-8') as _f:
                        _rc = json.load(_f)
                    _reset_base = (str((_rc.get('pwa_cart') or {}).get('reset_link_base_url', '') or '').strip().rstrip('/'))
            except Exception:
                pass
        self.reset_link_base_url = _reset_base or None
        if self.reset_link_base_url:
            logger.info(f"🔗 [API] 重置链接固定 base: {self.reset_link_base_url}")
        if self.r2_image_base_url:
            logger.info(f"📷 [API] 使用 R2 图片 base URL: {self.r2_image_base_url}")
        if self.pages_image_base_url:
            logger.info(f"📷 [API] 使用 Cloudflare Pages 图片 base URL: {self.pages_image_base_url}")
        if not self.product_image_dirs:
            self.product_image_dirs = [PWA_YA_SUBIO_BASE]
            logger.info(f"📷 使用默认图片目录: {self.product_image_dirs}")
        else:
            logger.info(f"📷 可配置图片目录（共 {len(self.product_image_dirs)} 个）: {self.product_image_dirs}")
        logger.info(f"📷 [API] PRODUCTOS 其他供应商白名单: {self.other_supplier_codes}")
        if self.productos_exclude_file_prefixes:
            logger.info(f"📷 [API] PRODUCTOS 排除文件前缀（未完成处理）: {self.productos_exclude_file_prefixes}")
        # CHANGE: 启动时加入 Telegram 同步图片目录，使 serve_product_image 能提供 telegram_xxx.jpg（否则 get_products 匹配到图但 /api/images/ 返回 404）
        _modules_dir = os.path.dirname(os.path.abspath(__file__))
        _telegram_product_images = os.path.normpath(os.path.abspath(os.path.join(_modules_dir, '..', 'database', 'product_images')))
        if _telegram_product_images not in self.product_image_dirs and os.path.isdir(_telegram_product_images):
            self.product_image_dirs.append(_telegram_product_images)
            logger.info(f"📷 已加入 Telegram 图片目录（供 /api/images/）: {_telegram_product_images}")
        # CHANGE: 加入 97 主程序 output_images，使 PRODUCTOS 能显示其他供应商产品图（codigo_proveedor != Cristy）
        _output_images = os.path.normpath(os.path.abspath(os.path.join(_modules_dir, '..', '..', 'output_images')))
        if _output_images not in self.product_image_dirs and os.path.isdir(_output_images):
            self.product_image_dirs.append(_output_images)
            logger.info(f"📷 已加入 output_images（PRODUCTOS 其他供应商图）: {_output_images}")
        print(f"📷 [API] 图片目录: {self.product_image_dirs}")
        
        # 创建Flask应用
        if FLASK_AVAILABLE:
            # 设置静态文件目录
            static_folder = os.path.join(os.path.dirname(__file__), 'pwa_cart')
            self.app = Flask(__name__, static_folder=static_folder, static_url_path='/pwa_cart')
            # CHANGE: 明确允许云端页 ventax.pages.dev、ventaxpages.com、预览部署 *.ventax.pages.dev 与本机，避免 CORS 拦截
            _cors_origins = [
                "https://ventax.pages.dev", "https://ventaxpages.com",
                "http://localhost:5000", "http://127.0.0.1:5000",
                "http://localhost", "http://127.0.0.1"
            ]
            _extra = (os.getenv('CORS_EXTRA_ORIGINS') or '').strip().split(',')
            _cors_origins.extend([o.strip() for o in _extra if o.strip()])
            _cors_origins.append("https://df6334cd.ventax.pages.dev")  # Wrangler 预览部署
            CORS(self.app, origins=_cors_origins, supports_credentials=True)

            # CHANGE: 所有响应（含 4xx/5xx）都加 CORS，避免 Render 错误响应无头导致浏览器报 CORS
            _cors_origins_set = set(_cors_origins)

            # CHANGE: 最先处理 OPTIONS 预检，确保 CORS 预检成功；放宽 origin 匹配以支持 Cloudflare Pages
            @self.app.before_request
            def cors_options_preflight():
                if request.method != 'OPTIONS':
                    return None
                origin = request.environ.get('HTTP_ORIGIN') or request.headers.get('Origin')
                # ventax.pages.dev、*.ventax.pages.dev、ventaxpages.com 及本地
                allow = origin and (
                    origin in _cors_origins_set
                    or re.match(r'^https://([a-z0-9-]+\.)*ventax\.pages\.dev$', origin)
                    or re.match(r'^https://([a-z0-9-]+\.)*ventaxpages\.com$', origin)
                )
                resp = jsonify({'ok': True})
                if allow:
                    resp.headers['Access-Control-Allow-Origin'] = origin
                    resp.headers['Access-Control-Allow-Credentials'] = 'true'
                    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
                    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Session-Id, X-Admin-Token'
                    resp.headers['Access-Control-Max-Age'] = '86400'
                    return resp
                # 未知 origin 时仍返回 CORS 头，避免预检失败掩盖真实问题（实际响应由 after_request 控制）
                if origin:
                    resp.headers['Access-Control-Allow-Origin'] = origin
                    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
                    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Session-Id'
                return resp

            @self.app.after_request
            def cors_ventax_pages_preview(response):
                origin = request.environ.get('HTTP_ORIGIN') or request.headers.get('Origin')
                if not origin:
                    return response
                allow = (
                    origin in _cors_origins_set
                    or re.match(r'^https://([a-z0-9-]+\.)*ventax\.pages\.dev$', origin)
                    or re.match(r'^https://([a-z0-9-]+\.)*ventaxpages\.com$', origin)
                )
                if allow:
                    response.headers['Access-Control-Allow-Origin'] = origin
                    response.headers['Access-Control-Allow-Credentials'] = 'true'
                    response.headers['Access-Control-Expose-Headers'] = 'Content-Type'
                    if request.method == 'OPTIONS':
                        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
                        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Session-Id'
                return response
            
            # CHANGE: /pwa_cart/api/* 重写为 /api/*，便于前端在 /pwa_cart/ 页时统一用 /pwa_cart/api 避免 404（如反向代理只转发 /pwa_cart 时）
            @self.app.before_request
            def rewrite_pwa_cart_api():
                if request.path.startswith('/pwa_cart/api'):
                    new_path = '/api' + request.path[len('/pwa_cart/api'):]
                    request.environ['PATH_INFO'] = new_path
                    logger.debug(f"📌 重写 PATH_INFO: {request.path} -> {new_path}")

            # CHANGE: 添加认证中间件（无登录模式：优先用 session_id 作为 guest_id，不再强制登录）
            @self.app.before_request
            def authenticate_request():
                """从请求头提取 token 或 X-Session-Id；无登录时用 session_id 转为 guest_id"""
                if request.path.startswith('/api/auth/'):
                    return
                auth_header = request.headers.get('Authorization')
                if auth_header and auth_header.startswith('Bearer '):
                    token = auth_header[7:]
                    payload = self._verify_token(token)
                    if payload:
                        setattr(request, 'user_id', payload.get('user_id'))
                        setattr(request, 'user_email', payload.get('email'))
                        return
                # CHANGE: 无 token 时用 X-Session-Id 转为 guest_id，实现免登录购物车/下单
                session_id = request.headers.get('X-Session-Id', '').strip()
                if session_id:
                    guest_id = self._session_to_guest_id(session_id)
                    setattr(request, 'user_id', guest_id)
                    setattr(request, 'user_email', None)
                else:
                    setattr(request, 'user_id', None)
                    setattr(request, 'user_email', None)
            
            # 添加请求日志中间件
            @self.app.before_request
            def log_request_info():
                logger.info(f"📥 收到请求: {request.method} {request.path}")
                print(f"📥 收到请求: {request.method} {request.path}")  # 同时输出到控制台
                if request.is_json:
                    request_body = json.dumps(request.get_json(), ensure_ascii=False)
                    logger.info(f"📥 请求体: {request_body}")
                    print(f"📥 请求体: {request_body}")  # 同时输出到控制台
            
            @self.app.after_request
            def log_response_info(response):
                logger.info(f"📤 响应状态: {response.status_code}")
                print(f"📤 响应状态: {response.status_code}")  # 同时输出到控制台
                # CHANGE: 检查响应中是否包含bank-info，如果是则验证Telegram链接
                if request.path == '/api/payment/bank-info' and response.status_code == 200:
                    try:
                        import json as json_lib
                        if response.is_json:
                            data = response.get_json()
                            if data and 'data' in data and 'customer_service' in data['data']:
                                telegram = data['data']['customer_service'].get('telegram', '')
                                if telegram != TELEGRAM_CUSTOMER_SERVICE_LINK:
                                    logger.error(f"❌❌❌ after_request检测到错误链接: {telegram}，强制修正为: {TELEGRAM_CUSTOMER_SERVICE_LINK}")
                                    print(f"❌❌❌ after_request检测到错误链接: {telegram}，强制修正为: {TELEGRAM_CUSTOMER_SERVICE_LINK}")
                                    data['data']['customer_service']['telegram'] = TELEGRAM_CUSTOMER_SERVICE_LINK
                                    response.set_data(json_lib.dumps(data, ensure_ascii=False))
                                else:
                                    logger.info(f"✅ after_request验证通过: {telegram}")
                                    print(f"✅ after_request验证通过: {telegram}")
                    except Exception as e:
                        logger.error(f"⚠️ after_request验证失败: {e}")
                return response
            
            # CHANGE: 添加全局错误处理器，确保所有错误都返回JSON格式
            @self.app.errorhandler(Exception)
            def handle_exception(e):
                """全局异常处理器，确保所有错误都返回JSON格式"""
                import traceback
                error_traceback = traceback.format_exc()
                error_msg = str(e)
                error_type = type(e).__name__
                
                # 记录错误
                logger.error(f"❌ 未捕获的异常: {error_msg}")
                logger.error(f"❌ 错误类型: {error_type}")
                logger.error(f"❌ 完整错误堆栈:\n{error_traceback}")
                print(f"\n{'='*60}")
                print(f"❌ 未捕获的异常: {error_msg}")
                print(f"❌ 错误类型: {error_type}")
                print(f"❌ 完整错误堆栈:\n{error_traceback}")
                print(f"{'='*60}\n")
                
                # 返回JSON格式的错误响应
                response = jsonify({
                    "success": False,
                    "error": f"Error interno del servidor: {error_msg}",
                    "error_type": error_type,
                    "details": error_traceback if self.debug else None
                })
                response.status_code = 500
                return response
            
            self._setup_routes()
        else:
            self.app = None
            logger.error("❌ Flask未安装，无法创建API服务器")
    
    # CHANGE: JWT工具方法
    def _generate_token(self, user_id, email):
        """生成JWT token"""
        if not JWT_AVAILABLE:
            logger.error("❌ JWT库未安装，无法生成token")
            return None
        try:
            payload = {
                'user_id': user_id,
                'email': email,
                'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
                'iat': datetime.utcnow()
            }
            token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
            # CHANGE: PyJWT 2.0+版本返回bytes，需要转换为字符串
            if isinstance(token, bytes):
                token = token.decode('utf-8')
            return token
        except Exception as e:
            logger.error(f"❌ 生成token失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _verify_token(self, token):
        """验证JWT token"""
        if not JWT_AVAILABLE:
            return None
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("⚠️ Token已过期")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"⚠️ Token无效: {e}")
            return None
    
    def _hash_password(self, password):
        """哈希密码"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _verify_password(self, password, password_hash):
        """验证密码"""
        return self._hash_password(password) == password_hash

    def _session_to_guest_id(self, session_id: str) -> int:
        """CHANGE: 将 session_id 转为负整数 guest_id，用于无登录模式下的购物车/订单"""
        if not session_id or not isinstance(session_id, str):
            return 0
        h = int(hashlib.sha256(session_id.strip().encode()).hexdigest()[:8], 16)
        return -abs(h % 99999999)

    # CHANGE: 云端用户存储 - 当 DATABASE_URL 存在时，用户数据写入 PostgreSQL（Neon），避免 Render 冷启动后 SQLite 重置导致用户丢失
    def _use_pg_for_users(self) -> bool:
        """是否使用 PostgreSQL 存储用户（云端部署时 True）"""
        return bool(self._get_pg_config())

    def _ensure_pwa_users_table(self, pg_config: Dict) -> bool:
        """确保 PostgreSQL 中存在 pwa_users 表"""
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return False
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pwa_users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE,
                    password_hash TEXT,
                    google_id VARCHAR(255) UNIQUE,
                    name VARCHAR(255),
                    avatar_url TEXT,
                    registration_method VARCHAR(50) DEFAULT 'email',
                    email_verified BOOLEAN DEFAULT FALSE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    password_reset_token TEXT,
                    password_reset_expires TIMESTAMP
                )
            """)
            conn.commit()
            cur.close()
            logger.info("✅ pwa_users 表已就绪（PostgreSQL）")
            return True
        except Exception as e:
            logger.error(f"❌ 创建 pwa_users 表失败: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def _pg_get_user_by_email(self, email: str) -> Optional[Dict]:
        """从 PostgreSQL 按邮箱获取用户"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT id, email, password_hash, google_id, name, avatar_url,
                       registration_method, email_verified, is_active, created_at, last_login
                FROM pwa_users WHERE LOWER(email) = LOWER(%s)
            """, (email.strip().lower() if email else '',))
            row = cur.fetchone()
            cur.close()
            if not row:
                return None
            r = dict(row)
            return {
                'id': r.get('id'),
                'email': r.get('email'),
                'password_hash': r.get('password_hash') or '',
                'google_id': r.get('google_id'),
                'name': r.get('name'),
                'avatar_url': r.get('avatar_url'),
                'registration_method': r.get('registration_method') or 'email',
                'email_verified': bool(r.get('email_verified')),
                'is_active': bool(r.get('is_active', True)),
                'created_at': r.get('created_at'),
                'last_login': r.get('last_login')
            }
        except Exception as e:
            logger.error(f"❌ _pg_get_user_by_email 失败: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _pg_get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """从 PostgreSQL 按 ID 获取用户"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT id, email, password_hash, google_id, name, avatar_url,
                       registration_method, email_verified, is_active, created_at, last_login
                FROM pwa_users WHERE id = %s
            """, (user_id,))
            row = cur.fetchone()
            cur.close()
            if not row:
                return None
            r = dict(row)
            return {
                'id': r.get('id'),
                'email': r.get('email'),
                'password_hash': r.get('password_hash') or '',
                'google_id': r.get('google_id'),
                'name': r.get('name'),
                'avatar_url': r.get('avatar_url'),
                'registration_method': r.get('registration_method') or 'email',
                'email_verified': bool(r.get('email_verified')),
                'is_active': bool(r.get('is_active', True)),
                'created_at': r.get('created_at'),
                'last_login': r.get('last_login')
            }
        except Exception as e:
            logger.error(f"❌ _pg_get_user_by_id 失败: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _pg_create_user(self, email: str, password_hash: str, name: str = None,
                        google_id: str = None, avatar_url: str = None,
                        registration_method: str = 'email') -> Tuple[Optional[int], Optional[str]]:
        """在 PostgreSQL 创建用户，返回 (user_id, error)"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None, "PostgreSQL 未配置"
        self._ensure_pwa_users_table(pg_config)
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor()
            cur.execute("SELECT id FROM pwa_users WHERE LOWER(email) = LOWER(%s)", (email.strip().lower(),))
            if cur.fetchone():
                cur.close()
                return None, "邮箱已被注册"
            display_name = (name or email.split('@')[0]) if email else ''
            cur.execute("""
                INSERT INTO pwa_users (email, password_hash, name, google_id, avatar_url, registration_method, email_verified)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (email.strip().lower(), password_hash, display_name, google_id, avatar_url,
                  registration_method, 1 if google_id else 0))
            row = cur.fetchone()
            user_id = row[0] if row else None
            conn.commit()
            cur.close()
            logger.info(f"✅ PostgreSQL 用户创建成功: user_id={user_id}, email={email}")
            return user_id, None
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ _pg_create_user 失败: {e}")
            return None, str(e)
        finally:
            if conn:
                conn.close()

    def _pg_update_user_last_login(self, user_id: int) -> bool:
        """更新 PostgreSQL 用户最后登录时间"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return False
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor()
            cur.execute("UPDATE pwa_users SET last_login = NOW() WHERE id = %s", (user_id,))
            conn.commit()
            cur.close()
            return True
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ _pg_update_user_last_login 失败: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def _pg_create_password_reset_token(self, email: str, token_hash: str, expires_at) -> Optional[int]:
        """在 PostgreSQL 创建密码重置 token，返回 user_id"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None
        user = self._pg_get_user_by_email(email)
        if not user:
            return None
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor()
            cur.execute("""
                UPDATE pwa_users SET password_reset_token = %s, password_reset_expires = %s WHERE id = %s
            """, (token_hash, expires_at, user['id']))
            conn.commit()
            cur.close()
            return user['id']
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ _pg_create_password_reset_token 失败: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _pg_get_user_by_reset_token(self, token_hash: str) -> Optional[Dict]:
        """从 PostgreSQL 按重置 token 获取用户"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT id, email, password_hash, name FROM pwa_users
                WHERE password_reset_token = %s AND password_reset_expires > NOW()
            """, (token_hash,))
            row = cur.fetchone()
            cur.close()
            if not row:
                return None
            r = dict(row)
            return {'id': r['id'], 'email': r['email'], 'password_hash': r.get('password_hash'), 'name': r.get('name')}
        except Exception as e:
            logger.error(f"❌ _pg_get_user_by_reset_token 失败: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _pg_update_password_and_clear_reset(self, user_id: int, password_hash: str) -> bool:
        """在 PostgreSQL 更新密码并清除重置 token"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return False
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor()
            cur.execute("""
                UPDATE pwa_users SET password_hash = %s, password_reset_token = NULL, password_reset_expires = NULL WHERE id = %s
            """, (password_hash, user_id))
            conn.commit()
            cur.close()
            return True
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"❌ _pg_update_password_and_clear_reset 失败: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def _get_pg_config(self) -> Optional[Dict[str, Any]]:
        """从 database_config.json 或 DATABASE_URL 环境变量读取 PostgreSQL 配置（ULTIMO 产品数据源）。
        CHANGE: 方案 A 云端部署时优先用 DATABASE_URL（Neon 等托管 PG 提供）。
        CHANGE: 若从 Neon Console 复制了 psql 命令行格式（psql 'postgresql://...'），自动剥掉外层只取 URI。"""
        db_url = os.getenv('DATABASE_URL', '').strip()
        if db_url:
            # Neon Console 复制的是 psql 'postgresql://...' 或 psql "postgresql://..."，psycopg2 需要纯 URI
            if db_url.lower().startswith("psql '") and db_url.endswith("'"):
                db_url = db_url[6:-1].strip()  # 去掉 psql ' 和末尾 '
            elif db_url.lower().startswith('psql "') and db_url.endswith('"'):
                db_url = db_url[6:-1].strip()  # 去掉 psql " 和末尾 "
            elif db_url.lower().startswith("psql "):
                db_url = db_url[4:].strip().strip("'\"").strip()
            return {'_connection_string': db_url}
        cfg_path = os.path.join(current_dir, '..', 'database_config.json')
        try:
            if os.path.isfile(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                pg = (cfg.get('postgresql') or {}).copy()
                if pg.get('database') and pg.get('user'):
                    return pg
        except Exception as e:
            logger.warning(f"⚠️ 读取 PostgreSQL 配置失败: {e}")
        return None

    def _format_image_path(self, ruta_from_db: str, supplier: Optional[str] = None) -> str:
        """CHANGE: 根据 R2_IMAGE_BASE_URL 或 PAGES_IMAGE_BASE_URL 返回图片 URL；本地用 /api/images/。"""
        if not ruta_from_db or not isinstance(ruta_from_db, str):
            return ''
        ruta = ruta_from_db.strip()
        if isinstance(ruta, bytes):
            ruta = ruta.decode('utf-8', errors='replace').strip()
        if not ruta:
            return ''
        # CHANGE: Render 上为 Linux，os.path.basename('D:\Cristy\Procesado\xx.jpg') 会返回整串（无 /）；先统一为 / 再取 basename
        ruta_norm = ruta.replace('\\', '/')
        basename = _normalize_image_filename(os.path.basename(ruta_norm))
        if not basename:
            return ''
        # CHANGE: 若配置 PAGES_IMAGE_BASE_URL，则直接返回 Pages 上的图片 URL（用于 pages.dev 部署）
        pages_base = (self.pages_image_base_url or '').strip().rstrip('/') if hasattr(self, 'pages_image_base_url') else ''
        if pages_base:
            sub_dir = 'Cristy/' if (supplier or '').strip().lower() == 'cristy' else ''
            return f"{pages_base}/Ya%20Subio/{sub_dir}{quote(basename)}"
        # CHANGE: 默认返回 /api/images/xxx，由前端根据当前站点拼出 Pages 或本机 API 地址
        return '/api/images/' + basename

    def _pg_connect(self, pg_config: Dict) -> "Any":
        """根据 pg_config 建立 PostgreSQL 连接。支持 DATABASE_URL 或 host/port/db 形式。"""
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None
        conn_str = pg_config.get('_connection_string')
        if conn_str:
            return psycopg2.connect(conn_str, connect_timeout=10)
        return psycopg2.connect(
            host=pg_config.get('host', 'localhost'),
            port=int(pg_config.get('port', 8888)),
            database=pg_config.get('database', 'ventax_db'),
            user=pg_config.get('user', 'postgres'),
            password=pg_config.get('password', ''),
            connect_timeout=10,
        )

    def _get_ultimo_products_from_postgres(self) -> List[Tuple[Any, Dict]]:
        """从 PostgreSQL 读取 ULTIMO 产品：codigo_proveedor='Cristy' 且 inventario>=0（含库存为0，与 D:\\Ya Subio\\Cristy 图片一致）。返回 [(product_id, product_info), ...]。"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return []
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # CHANGE: inventario>=0 使库存为 0 的 Cristy 产品也能在列表/ULTIMO 显示（图片在 D:\Ya Subio\Cristy）
            cur.execute(
                """
                SELECT id_producto, codigo_producto, nombre_producto, descripcion,
                       precio_unidad, precio_mayor, precio_bulto, categoria, ruta_imagen,
                       inventario, codigo_proveedor, fecha_creacion, esta_activo
                FROM products
                WHERE codigo_proveedor = 'Cristy'
                  AND (inventario IS NULL OR inventario >= 0)
                  AND (esta_activo IS NULL OR esta_activo = TRUE)
                ORDER BY fecha_creacion DESC NULLS LAST
                """
            )
            rows = cur.fetchall()
            cur.close()
            out = []
            for r in rows:
                # CHANGE: 兼容 PG 返回列名大小写（部分环境为小写）
                try:
                    _r = {str(k).lower(): v for k, v in r.items()}
                except Exception:
                    _r = dict(r) if hasattr(r, '__iter__') else {}
                pid = _r.get('id_producto')
                if pid is None:
                    continue
                created_at = _r.get('fecha_creacion')
                if created_at is not None and hasattr(created_at, 'isoformat'):
                    created_at = created_at.isoformat()
                # CHANGE: 库内 ruta_imagen 多为 D:\Cristy\Procesado\xxx.jpg；云端用 R2/Pages URL，本地用 /api/images/
                ruta = self._format_image_path(str(_r.get('ruta_imagen') or ''), 'Cristy')
                _name = _r.get('nombre_producto')
                _code = _r.get('codigo_producto')
                pinfo = {
                    'name': (_name if _name is not None else '').strip() if isinstance(_name, str) else str(_name or ''),
                    'product_code': (_code if _code is not None else '').strip() if isinstance(_code, str) else str(_code or ''),
                    'price': float(_r.get('precio_unidad') or 0),
                    'wholesale_price': float(_r.get('precio_mayor') or 0),
                    'bulk_price': float(_r.get('precio_bulto') or 0),
                    'description': (str(_r.get('descripcion') or _r.get('description') or '')).strip(),
                    'category_id': (str(_r.get('categoria') or 'default')).strip(),
                    'image_path': ruta,
                    'stock': int(_r.get('inventario') or 0),
                    'codigo_proveedor': 'Cristy',
                    'created_at': created_at or '',
                    'is_active': 1,
                }
                # 列表用 codigo_producto 作为 id，便于前端 #/product/10060._AI 链接与详情页一致
                code = (pinfo.get('product_code') or '').strip() or str(pid)
                out.append((code, pinfo))
            logger.info(f"📦 [API] PostgreSQL ULTIMO 产品: {len(out)} 条（Cristy，库存>=0）")
            return out
        except Exception as e:
            logger.warning(f"⚠️ PostgreSQL ULTIMO 产品查询失败: {e}")
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _get_others_products_from_postgres(self) -> List[Tuple[Any, Dict]]:
        """从 PostgreSQL 读取非 Cristy 产品（PRODUCTOS 用），供「以图为准」时与 SQLite 合并建 _image_to_product，解决仅存 PG 的产品无法映射。"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return []
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            if not conn:
                return []
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # CHANGE: codigo_proveedor 可能为空，用 channel_username 判断；若 channel_username 列不存在则回退原查询
            try:
                cur.execute(
                    """
                    SELECT id_producto, codigo_producto, nombre_producto, descripcion,
                           precio_unidad, precio_mayor, precio_bulto, categoria, ruta_imagen,
                           codigo_proveedor, channel_username, fecha_creacion, esta_activo
                    FROM products
                    WHERE (esta_activo IS NULL OR esta_activo = TRUE)
                      AND (
                        (codigo_proveedor IS NOT NULL AND codigo_proveedor != '' AND codigo_proveedor != 'Cristy')
                        OR (channel_username IS NOT NULL AND channel_username != '' AND LOWER(channel_username) NOT IN ('novedadescristy_gye', 'cristy'))
                      )
                    ORDER BY fecha_creacion DESC NULLS LAST
                    """
                )
                rows = cur.fetchall()
            except Exception as e:
                err_msg = str(e).lower() if e else ''
                if 'channel_username' in err_msg or 'column' in err_msg or 'does not exist' in err_msg:
                    # CHANGE: 包含 codigo_proveedor=NULL 的产品，由 _filter 用 ruta_imagen 路径推断供应商
                    cur.execute(
                        """
                        SELECT id_producto, codigo_producto, nombre_producto, descripcion,
                               precio_unidad, precio_mayor, precio_bulto, categoria, ruta_imagen,
                               codigo_proveedor, fecha_creacion, esta_activo
                        FROM products
                        WHERE (esta_activo IS NULL OR esta_activo = TRUE)
                          AND (codigo_proveedor IS NULL OR codigo_proveedor = '' OR (codigo_proveedor IS NOT NULL AND codigo_proveedor != 'Cristy'))
                        ORDER BY fecha_creacion DESC NULLS LAST
                        """
                    )
                    rows = cur.fetchall()
                    logger.info(f"📦 [API] channel_username 列不存在，使用原查询: {len(rows)} 条")
                else:
                    raise
            cur.close()
            out = []
            for r in rows:
                try:
                    _r = {str(k).lower(): v for k, v in r.items()}
                except Exception:
                    _r = dict(r) if hasattr(r, '__iter__') else {}
                pid = _r.get('id_producto')
                if pid is None:
                    continue
                created_at = _r.get('fecha_creacion')
                if created_at is not None and hasattr(created_at, 'isoformat'):
                    created_at = created_at.isoformat()
                ruta = self._format_image_path(str(_r.get('ruta_imagen') or ''), (_r.get('codigo_proveedor') or '').strip())
                _name = _r.get('nombre_producto')
                _code = _r.get('codigo_producto')
                _ruta_raw = str(_r.get('ruta_imagen') or '').strip()
                pinfo = {
                    'name': (_name if _name is not None else '').strip() if isinstance(_name, str) else str(_name or ''),
                    'product_code': (_code if _code is not None else '').strip() if isinstance(_code, str) else str(_code or ''),
                    'price': float(_r.get('precio_unidad') or 0),
                    'wholesale_price': float(_r.get('precio_mayor') or 0),
                    'bulk_price': float(_r.get('precio_bulto') or 0),
                    'description': (str(_r.get('descripcion') or _r.get('description') or '')).strip(),
                    'category_id': (str(_r.get('categoria') or 'default')).strip(),
                    'image_path': ruta,
                    'ruta_imagen': ruta,
                    'ruta_imagen_raw': _ruta_raw,  # CHANGE: 保留原始路径，供云端 fallback 提取 output_images 子目录
                    'codigo_proveedor': (_r.get('codigo_proveedor') or '').strip(),
                    'channel_username': (_r.get('channel_username') or '').strip(),
                    'created_at': created_at or '',
                    'is_active': 1,
                }
                code = (pinfo.get('product_code') or '').strip() or str(pid)
                out.append((pid, pinfo))
            if out:
                logger.info(f"📦 [API] PostgreSQL 非Cristy产品: {len(out)} 条（用于PRODUCTOS图名映射）")
            return out
        except Exception as e:
            logger.warning(f"⚠️ PostgreSQL 非Cristy产品查询失败: {e}")
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _get_single_product_from_postgres(self, product_id: str) -> Optional[Dict]:
        """从 PostgreSQL 按 id_producto 查询单条 Cristy 产品，供详情页使用。未找到返回 None。"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            if not conn:
                return []
            cur = conn.cursor(cursor_factory=RealDictCursor)
            pid_str = str(product_id).strip()
            # id_producto 可能为整数，codigo_producto 为文本如 100001._AI；用 ::text 与 codigo_producto 匹配
            cur.execute(
                """
                SELECT id_producto, codigo_producto, nombre_producto, descripcion,
                       precio_unidad, precio_mayor, precio_bulto, categoria, ruta_imagen,
                       inventario, codigo_proveedor, fecha_creacion, esta_activo
                FROM products
                WHERE codigo_proveedor = 'Cristy'
                  AND (codigo_producto = %s OR id_producto::text = %s)
                  AND (esta_activo IS NULL OR esta_activo = TRUE)
                LIMIT 1
                """,
                (pid_str, pid_str),
            )
            r = cur.fetchone()
            cur.close()
            if not r:
                return None
            try:
                _r = {str(k).lower(): v for k, v in r.items()}
            except Exception:
                _r = dict(r)
            created_at = _r.get('fecha_creacion')
            if created_at is not None and hasattr(created_at, 'isoformat'):
                created_at = created_at.isoformat()
            # CHANGE: ruta_imagen 多为 D:\Cristy\Procesado\xxx.jpg，实际在 pwa_cart/Ya Subio/Cristy，返回 /api/images/ 或 Pages URL
            ruta = self._format_image_path(str(_r.get('ruta_imagen') or ''), 'Cristy')
            return {
                'id': _r.get('id_producto'),
                'name': (str(_r.get('nombre_producto') or '')).strip(),
                'product_code': (str(_r.get('codigo_producto') or '')).strip(),
                'price': float(_r.get('precio_unidad') or 0),
                'wholesale_price': float(_r.get('precio_mayor') or 0),
                'bulk_price': float(_r.get('precio_bulto') or 0),
                'description': (str(_r.get('descripcion') or _r.get('description') or '')).strip(),
                'category_id': (str(_r.get('categoria') or 'default')).strip(),
                'image_path': ruta,
                'stock': int(_r.get('inventario') or 0),
                'codigo_proveedor': 'Cristy',
                'created_at': created_at or '',
                'is_active': 1,
            }
        except Exception as e:
            logger.warning(f"⚠️ PostgreSQL 单产品查询失败 product_id={product_id}: {e}")
            return None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _get_products_dict_from_postgres(self) -> Dict[Any, Dict]:
        """当 USE_SQLITE_FOR_PRODUCTS=False 时，从 PostgreSQL 合并 Cristy + 非Cristy 得到与 get_all_products() 同结构的 dict。"""
        out = {}
        cristy = self._get_ultimo_products_from_postgres()
        others = self._get_others_products_from_postgres()
        for pid, pinfo in cristy:
            if pid is not None:
                out[pid] = pinfo
                code = (pinfo.get('product_code') or '').strip() or str(pid)
                if code:
                    out[code] = pinfo
        for pid, pinfo in others:
            if pid is not None:
                out[pid] = pinfo
                code = (pinfo.get('product_code') or '').strip() or str(pid)
                if code:
                    out[code] = pinfo
        if out:
            logger.info(f"📦 [API] PostgreSQL 产品字典: {len(out)} 条（Cristy+非Cristy，替代 SQLite）")
        return out

    def _get_single_product_from_postgres_any(self, product_id: str) -> Optional[Dict]:
        """从 PostgreSQL 按 id_producto/codigo_producto 查询单条产品（不限制 Cristy），供详情页/购物车/同步补全。
        CHANGE: 不再过滤 esta_activo，确保其他供应商产品（可能未设或为 FALSE）也能查到 name/code。
        CHANGE: 若完整 pid 查不到，提取数字部分（如 TG_JUGUETESFANG_90029 -> 90029）再查 id_producto，Neon 中其他供应商常用 id 当主键。"""
        pg_config = self._get_pg_config()
        if not pg_config or not PSYCOPG2_AVAILABLE or psycopg2 is None:
            return None
        conn = None
        try:
            conn = self._pg_connect(pg_config)
            if not conn:
                return None
            cur = conn.cursor(cursor_factory=RealDictCursor)
            pid_str = str(product_id).strip()
            ids_to_try = [pid_str]
            # CHANGE: 提取数字部分（如 TG_JUGUETESFANG_90029 -> 90029），Neon 中 codigo_producto 可能为 XE02，id_producto=90029
            nums = re.findall(r'\d+', pid_str)
            for n in reversed(nums):
                if n and n not in ids_to_try:
                    ids_to_try.append(n)
            r = None
            for try_id in ids_to_try:
                cur.execute(
                    """
                    SELECT id_producto, codigo_producto, nombre_producto, descripcion,
                           precio_unidad, precio_mayor, precio_bulto, categoria, ruta_imagen,
                           inventario, codigo_proveedor, fecha_creacion, esta_activo
                    FROM products
                    WHERE codigo_producto = %s OR id_producto::text = %s
                    LIMIT 1
                    """,
                    (try_id, try_id),
                )
                r = cur.fetchone()
                if r:
                    if try_id != pid_str:
                        logger.info("📋 [PG any] 用数字 id=%s 匹配到 product_id=%s", try_id, pid_str)
                    break
            cur.close()
            if not r:
                logger.debug("📋 [PG any] 未找到 product_id=%s（已尝试 %s）", pid_str, ids_to_try)
                return None
            try:
                _r = {str(k).lower(): v for k, v in r.items()}
            except Exception:
                _r = dict(r)
            created_at = _r.get('fecha_creacion')
            if created_at is not None and hasattr(created_at, 'isoformat'):
                created_at = created_at.isoformat()
            ruta = self._format_image_path(str(_r.get('ruta_imagen') or ''), (_r.get('codigo_proveedor') or '').strip())
            code = (str(_r.get('codigo_producto') or '')).strip()
            name = (str(_r.get('nombre_producto') or '')).strip()
            logger.info("📋 [PG any] 找到 product_id=%s -> codigo=%s, nombre=%s", pid_str, code, (name or "")[:50])
            return {
                'id': _r.get('id_producto'),
                'name': name,
                'product_code': code,
                'price': float(_r.get('precio_unidad') or 0),
                'wholesale_price': float(_r.get('precio_mayor') or 0),
                'bulk_price': float(_r.get('precio_bulto') or 0),
                'description': (str(_r.get('descripcion') or _r.get('description') or '')).strip(),
                'category_id': (str(_r.get('categoria') or 'default')).strip(),
                'image_path': ruta,
                'stock': int(_r.get('inventario') or 0),
                'codigo_proveedor': (_r.get('codigo_proveedor') or '').strip(),
                'created_at': created_at or '',
                'is_active': 1,
            }
        except Exception as e:
            logger.warning(f"⚠️ PostgreSQL 单产品(any)查询失败 product_id={product_id}: {e}")
            return None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _sync_products_to_web(self, clear_cache=False):
        """将 Telegram/主程序 数据库同步到网页文件夹（与 pwa_cart/同步数据库.py 逻辑一致）。
        clear_cache=True 时：先关闭 DB 连接、删除目标库文件，再全量复制源库并重新初始化连接。"""
        import shutil
        base_dir = os.path.dirname(os.path.abspath(__file__))
        source_db = os.path.abspath(os.path.join(base_dir, '..', 'database', 'spanish_product_database.db'))
        target_db = os.path.abspath(os.path.join(base_dir, 'pwa_cart', 'spanish_product_database.db'))
        if not os.path.exists(source_db):
            return False, f"源数据库不存在: {source_db}"
        try:
            if clear_cache:
                # 关闭 DB 连接以便删除/覆盖目标文件
                _db = getattr(self, 'db', None)
                if _db and hasattr(_db, 'close'):
                    try:
                        _db.close()  # type: ignore[union-attr]
                        logger.info("✅ 已关闭数据库连接（准备清缓存全量同步）")
                    except Exception as e:
                        logger.warning(f"关闭 DB 时: {e}")
                _cm = getattr(self, 'cart_manager', None)
                if _cm and getattr(_cm, 'db', None) and hasattr(getattr(_cm, 'db'), 'close'):
                    try:
                        getattr(_cm.db, 'close')()  # type: ignore[union-attr]
                    except Exception:
                        pass
                self.db = None
                self.cart_manager = None
                if os.path.exists(target_db):
                    try:
                        os.remove(target_db)
                        logger.info(f"✅ 已删除目标库（清缓存）: {target_db}")
                    except Exception as e:
                        return False, f"删除目标库失败: {e}"
            shutil.copy2(source_db, target_db)
            if not os.path.exists(target_db) or os.path.getsize(target_db) != os.path.getsize(source_db):
                return False, "同步后大小不一致"
            # CHANGE: 每次同步成功后都重新初始化 DB 连接，确保网页 API 立即读到新库内容（否则会继续读旧连接/缓存）
            _need_reinit = clear_cache and self.db is None
            if not _need_reinit and (getattr(self, 'db', None) or getattr(self, 'cart_manager', None)):
                _db = getattr(self, 'db', None)
                if _db and hasattr(_db, 'close'):
                    try:
                        _db.close()  # type: ignore[union-attr]
                    except Exception:
                        pass
                _cm = getattr(self, 'cart_manager', None)
                if _cm and getattr(_cm, 'db', None) and hasattr(getattr(_cm, 'db'), 'close'):
                    try:
                        getattr(_cm.db, 'close')()  # type: ignore[union-attr]
                    except Exception:
                        pass
                self.db = None
                self.cart_manager = None
                _need_reinit = True
            if _need_reinit and CartManager:
                if USE_SQLITE_FOR_PRODUCTS and DatabaseManager:
                    self.db = DatabaseManager()
                    logger.info(f"✅ 已重新初始化数据库: {self.db.db_path}")
                    self.cart_manager = CartManager(db=self.db)
                else:
                    self.cart_manager = CartManager(db=None)
                    self.db = getattr(self.cart_manager, 'db', None)
                    logger.info("✅ 已重新初始化 CartManager（产品数据用 PostgreSQL）")
                logger.info("✅ 已重新初始化 CartManager")
            return True, f"已{'清缓存并' if clear_cache else ''}全量同步到 {target_db}"
        except Exception as e:
            return False, str(e)

    def _filter_products_cristy_and_others(
        self,
        products: Dict,
        cristy_from_pg: List[Tuple[Any, Dict]],
        one_month_ago: Optional[datetime] = None,  # CHANGE: 已弃用，PRODUCTOS 不再按日期过滤
        own_supplier: str = 'Cristy',
    ) -> Tuple[List[Tuple[Any, Dict]], List[Tuple[Any, Dict]], int, int]:
        """筛选 Cristy 与其它供应商产品。返回 (cristy_products, all_filtered_products, skipped_by_date, skipped_cristy_by_stock)"""
        cristy_products = list(cristy_from_pg) if cristy_from_pg else []
        all_filtered = []
        skipped_by_date = 0
        skipped_cristy = 0
        whitelist = getattr(self, 'other_supplier_codes', None) or ['Importadora_Chinito', 'IMP158', 'Importadorawoni', 'ayacuchoamoreshop', 'ecuarticulos']
        if not cristy_products:
            for pid, pinfo in products.items():
                if not pinfo.get('is_active', 1):
                    continue
                if (pinfo.get('codigo_proveedor') or '').strip().lower() != own_supplier.lower():
                    continue
                try:
                    st = int(pinfo.get('stock') or 999) if pinfo.get('stock') is not None else 999
                except (TypeError, ValueError):
                    st = 999
                if st < 6:
                    skipped_cristy += 1
                    continue
                cristy_products.append((pid, pinfo))
        for pid, pinfo in products.items():
            if not pinfo.get('is_active', 1):
                continue
            cp = (pinfo.get('codigo_proveedor') or '').strip().lower()
            if cp == own_supplier.lower():
                if cristy_from_pg:
                    continue
                try:
                    st = int(pinfo.get('stock') or 999) if pinfo.get('stock') is not None else 999
                except (TypeError, ValueError):
                    st = 999
                if st < 6:
                    skipped_cristy += 1
                    continue
                cristy_products.append((pid, pinfo))
                continue
            # CHANGE: codigo_proveedor 可能为空，用 channel_username 回退匹配（如 Importadora_Chinito）
            chan = (pinfo.get('channel_username') or '').strip().lower().lstrip('@')
            # CHANGE: 供应商识别改为仅用 channel_username；为空时回退图片路径推断。
            chan_match = chan and chan in [c.lower() for c in whitelist if c]
            path_match = False
            if not chan_match:
                _ruta = (pinfo.get('ruta_imagen_raw') or pinfo.get('ruta_imagen') or pinfo.get('image_path') or '')
                if _ruta and isinstance(_ruta, str):
                    _ruta_lower = _ruta.replace('\\', '/').lower()
                    for _w in whitelist:
                        if _w and _w.lower() in _ruta_lower:
                            path_match = True
                            break
            if not chan_match and not path_match:
                continue
            # CHANGE: 排除未完成处理的产品（如图片路径含 importadoraWoni_ 且仅OCR未白底）
            _excl_prefixes = getattr(self, 'productos_exclude_file_prefixes', None) or []
            if _excl_prefixes:
                _img = (pinfo.get('ruta_imagen_raw') or pinfo.get('ruta_imagen') or pinfo.get('image_path') or '')
                _img_base = os.path.basename(str(_img).replace('\\', '/')).lower() if _img else ''
                if _img_base and any(_img_base.startswith(p) for p in _excl_prefixes):
                    continue
            all_filtered.append((pid, pinfo))
        return cristy_products, all_filtered, skipped_by_date, skipped_cristy

    def _select_products_by_supplier(
        self,
        cristy_products: List[Tuple[Any, Dict]],
        all_filtered_products: List[Tuple[Any, Dict]],
        products: Dict,
        supplier_lower: str,
        search: Optional[str],
        own_supplier_code: str = 'Cristy',
    ) -> List[Tuple[Any, Dict]]:
        """根据 supplier 参数选择要返回的产品列表，降低 get_products 复杂度"""
        if supplier_lower == own_supplier_code.lower():
            if len(cristy_products) > 0:
                return cristy_products
            if len(all_filtered_products) > 0:
                return all_filtered_products
            return [(pid, pinfo) for pid, pinfo in products.items() if pinfo.get('is_active', 1)]
        if supplier_lower == 'others':
            if len(all_filtered_products) > 0:
                return all_filtered_products
            return [(pid, pinfo) for pid, pinfo in products.items()
                    if pinfo.get('is_active', 1)
                    and (pinfo.get('codigo_proveedor') or '').strip().lower() != own_supplier_code.lower()]
        if supplier_lower:
            return [(pid, pinfo) for pid, pinfo in all_filtered_products
                    if (pinfo.get('codigo_proveedor') or '').strip().lower() == supplier_lower.strip()]
        # 无 supplier 时返回 ULTIMO + PRODUCTOS 并集
        seen = set()
        combined = []
        for pid, pinfo in cristy_products:
            seen.add(pid)
            combined.append((pid, pinfo))
        for pid, pinfo in all_filtered_products:
            if pid not in seen:
                seen.add(pid)
                combined.append((pid, pinfo))
        combined.sort(key=lambda x: x[1].get('created_at', '') or '', reverse=True)
        if len(combined) > 0:
            return combined
        out = [(pid, pinfo) for pid, pinfo in products.items() if pinfo.get('is_active', 1)]
        out.sort(key=lambda x: x[1].get('created_at', '') or '', reverse=True)
        return out

    def _setup_routes(self):
        """设置API路由"""
        if not self.app:
            logger.error("❌ Flask应用未初始化，无法设置路由")
            return
        # CHANGE: 云端部署时启动时确保 pwa_users 表存在
        if self._use_pg_for_users():
            pg_cfg = self._get_pg_config()
            if pg_cfg:
                self._ensure_pwa_users_table(pg_cfg)
                logger.info("✅ DATABASE_URL 已配置，用户数据将写入 PostgreSQL (pwa_users)")
        else:
            logger.warning("⚠️ DATABASE_URL 未配置！用户数据将使用 SQLite，Render 冷启动后丢失。请在 Render 环境变量中设置 DATABASE_URL（Neon 连接串）")

        @self.app.route('/health')
        def health():
            """CHANGE: 轻量健康检查，供 Render/UptimeRobot 快速 ping，避免 No open HTTP ports"""
            return jsonify({"status": "ok"}), 200

        @self.app.route('/')
        def home():
            """主页 - 重定向到PWA"""
            from flask import redirect
            return redirect('/pwa_cart/')
        
        @self.app.route('/pwa_cart/')
        def pwa_home():
            """PWA主页；云端部署无前端文件时重定向到 Pages"""
            from flask import send_from_directory, redirect
            index_path = os.path.join(self.app.static_folder, 'index.html') if (self.app and self.app.static_folder) else ''
            if not self.app or not self.app.static_folder or not (os.path.exists(index_path) and os.path.isfile(index_path)):
                return redirect(os.getenv('PAGES_IMAGE_BASE_URL', 'https://ventax.pages.dev/pwa_cart').rstrip('/') + '/', code=302)
            resp = send_from_directory(self.app.static_folder, 'index.html')
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
            return resp
        
        @self.app.route('/favicon.ico')
        def favicon():
            """Favicon图标"""
            from flask import Response
            # 返回一个简单的SVG favicon
            svg_favicon = '''<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
                <rect width="32" height="32" fill="#4CAF50" rx="6"/>
                <text x="16" y="24" font-size="20" text-anchor="middle" fill="white" font-family="Arial, sans-serif">🛒</text>
            </svg>'''
            return Response(svg_favicon, mimetype='image/svg+xml', headers={'Content-Type': 'image/svg+xml'})
        
        # 优先处理PNG图标请求（在通用静态文件路由之前）
        @self.app.route('/pwa_cart/icon-<int:size>.png')
        def pwa_icon_png(size):
            """处理PNG图标请求，返回对应的SVG"""
            from flask import send_from_directory, Response
            import os
            logger.info(f"🖼️ PNG图标请求: icon-{size}.png, 返回SVG版本")
            
            if not self.app or not self.app.static_folder:
                from flask import redirect
                return redirect(os.getenv('PAGES_IMAGE_BASE_URL', 'https://ventax.pages.dev/pwa_cart').rstrip('/') + '/', code=302)
            # 确定对应的SVG文件名
            if size == 192:
                svg_filename = 'icon-192.svg'
            elif size == 512:
                svg_filename = 'icon-512.svg'
            else:
                svg_filename = 'icon-192.svg'
            
            # 尝试返回对应的SVG文件
            svg_path = os.path.join(self.app.static_folder, svg_filename)
            if os.path.exists(svg_path):
                response = send_from_directory(self.app.static_folder, svg_filename)
                response.headers['Content-Type'] = 'image/svg+xml; charset=utf-8'
                return response
            
            # 如果SVG也不存在，生成一个SVG图标（作为PNG的替代）
            svg_icon = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
                <defs>
                    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" style="stop-color:#4CAF50;stop-opacity:1" />
                        <stop offset="100%" style="stop-color:#45a049;stop-opacity:1" />
                    </linearGradient>
                </defs>
                <rect width="{size}" height="{size}" fill="url(#grad)" rx="{size//10}"/>
                <circle cx="{size//2}" cy="{size//2}" r="{size//3}" fill="white" opacity="0.3"/>
                <text x="{size//2}" y="{size//2 + size//12}" font-size="{size//2.5}" text-anchor="middle" fill="white" font-family="Arial, sans-serif" font-weight="bold">🛒</text>
            </svg>'''
            # 返回SVG，但设置正确的MIME类型
            return Response(svg_icon, mimetype='image/svg+xml', headers={
                'Content-Type': 'image/svg+xml',
                'Cache-Control': 'public, max-age=31536000'
            })
        
        @self.app.route('/pwa_cart/<path:filename>')
        def pwa_static(filename):
            """PWA静态文件"""
            from flask import send_from_directory, abort, Response
            import os

            try:
                logger.debug(f"📁 PWA静态文件请求: {filename}, static_folder: {self.app.static_folder if self.app else 'N/A'}")
                if not self.app or not self.app.static_folder:
                    from flask import redirect
                    return redirect(os.getenv('PAGES_IMAGE_BASE_URL', 'https://ventax.pages.dev/pwa_cart').rstrip('/') + '/', code=302)

                safe_name = str(filename or '').replace('\\', '/').strip('/')
                if not safe_name:
                    abort(404)

                file_path = os.path.join(self.app.static_folder, safe_name)
                logger.debug(f"📁 文件路径: {file_path}, 存在: {os.path.exists(file_path)}")
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    response = send_from_directory(self.app.static_folder, safe_name)
                    # CHANGE: HTML/JS/CSS/manifest/service-worker 禁止缓存，避免 Chrome/Telegram 内置浏览器加载旧版本
                    _no_cache = ('index.html', '404.html', 'app.js', 'styles.css', 'config.js', 'manifest.json', 'service-worker.js')
                    if safe_name in _no_cache or safe_name.endswith(('.html', '.js', '.css', '.json')):
                        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
                        response.headers['Pragma'] = 'no-cache'
                        response.headers['Expires'] = '0'
                    if safe_name.endswith('.svg'):
                        response.headers['Content-Type'] = 'image/svg+xml; charset=utf-8'
                    return response
                else:
                    abort(404)
            except Exception as e:
                logger.exception(f"❌ PWA静态文件服务异常: {filename} -> {e}")
                abort(404)
        
        # CHANGE: 添加对旧路径/img/的兼容支持
        @self.app.route('/img/<path:filename>')
        def serve_product_image_old(filename):
            """提供产品图片服务（旧路径兼容）"""
            from flask import redirect
            # 重定向到新的API路径
            return redirect(f'/api/images/{filename}', code=301)
        
        # CHANGE: 添加对 /pwa_cart/static/img/ 路径的兼容支持
        @self.app.route('/pwa_cart/static/img/<path:filename>')
        def serve_product_image_static(filename):
            """提供产品图片服务（静态路径兼容）"""
            from flask import redirect
            # 重定向到新的API路径
            return redirect(f'/api/images/{filename}', code=301)
        
        @self.app.route('/api/images/<path:filename>')
        def serve_product_image(filename):
            """提供产品图片服务 - 仅从可配置目录（port_config.json pwa_cart.product_image_dirs）递归查找"""
            from flask import send_from_directory, jsonify, Response, redirect
            from urllib.parse import unquote, quote
            import os
            import json

            try:
                filename = unquote(str(filename or '')).strip()
                base_filename = os.path.basename(filename)
                base_filename_clean = _normalize_image_filename(base_filename)
                if not base_filename and not base_filename_clean:
                    return Response('', status=404)

                # CHANGE: API 代理层支持「加密文件名映射解析」：
                # 1) telegram_display_code_mapping.json（展示码/加密码 -> pwa_key）
                # 2) message_id_mapping.json（加密码 -> message_id）
                # 结合 msg_{message_id}_*.jpg / telegram_{message_id}.jpg 规则找回真实文件
                def _safe_load_json(file_path):
                    try:
                        if not os.path.isfile(file_path):
                            return {}
                        with open(file_path, 'r', encoding='utf-8') as f:
                            raw = f.read()
                        if not raw.strip():
                            return {}
                        try:
                            data = json.loads(raw)
                        except ValueError as e:
                            if 'Extra data' in str(e):
                                dec = json.JSONDecoder()
                                data, _ = dec.raw_decode(raw)
                            else:
                                return {}
                        return data if isinstance(data, dict) else {}
                    except Exception:
                        return {}

                def _candidate_names_from_mapping(req_name):
                    req = (req_name or '').strip()
                    if not req:
                        return set(), set()
                    req_base = os.path.splitext(_normalize_image_filename(req))[0].strip()
                    req_base_lower = req_base.lower()
                    ext = os.path.splitext(req)[1] or '.jpg'

                    root_json_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
                    display_map_file = os.path.join(root_json_dir, 'telegram_display_code_mapping.json')
                    msg_map_file = os.path.join(root_json_dir, 'message_id_mapping.json')
                    display_map = _safe_load_json(display_map_file)
                    msg_map = _safe_load_json(msg_map_file)

                    name_candidates = set()
                    msg_id_candidates = set()

                    # A) 展示码/加密码 -> pwa_key
                    pwa_key = ''
                    display_map_lc = {str(k).strip().lower(): str(v).strip() for k, v in display_map.items() if k}
                    if req_base in display_map:
                        pwa_key = str(display_map.get(req_base) or '').strip()
                    elif req_base_lower in display_map_lc:
                        pwa_key = display_map_lc.get(req_base_lower, '')
                    if pwa_key:
                        pwa_base = os.path.splitext(pwa_key)[0].strip()
                        if pwa_base:
                            for _e in (ext, '.jpg', '.jpeg', '.png', '.webp'):
                                name_candidates.add(pwa_base + _e)

                    # B) 加密码 -> message_id
                    for code_key in [req_base, req_base_lower, pwa_key, os.path.splitext(pwa_key)[0].strip() if pwa_key else '']:
                        if not code_key:
                            continue
                        entry = msg_map.get(code_key)
                        if not entry and isinstance(msg_map, dict):
                            entry = msg_map.get(str(code_key).lower()) if str(code_key) != str(code_key).lower() else None
                        if isinstance(entry, dict):
                            mid = str(entry.get('message_id') or '').strip()
                            if mid.isdigit():
                                msg_id_candidates.add(mid)

                    for mid in list(msg_id_candidates):
                        for _e in ('.jpg', '.jpeg', '.png', '.webp'):
                            name_candidates.add(f'telegram_{mid}{_e}')
                            # msg_{message_id}_*.ext 将由前缀匹配补全

                    # C) product_mapping.json 兜底（展示码/加密码 -> product_id 等）
                    # 目的：当 telegram_display_code_mapping 只存 key 自身时，仍可通过 product_id 找到 Ya Subio 真实文件
                    try:
                        product_map_file = os.path.join(root_json_dir, 'product_mapping.json')
                        product_map = _safe_load_json(product_map_file)
                        if isinstance(product_map, dict) and product_map:
                            candidate_keys = []
                            for _k in (req_base, req_base_lower, pwa_key, os.path.splitext(pwa_key)[0].strip() if pwa_key else ''):
                                _ks = str(_k or '').strip()
                                if _ks:
                                    candidate_keys.append(_ks)
                            # 再追加小写键匹配，避免大小写差异
                            for _k in list(candidate_keys):
                                _kl = _k.lower()
                                if _kl not in candidate_keys:
                                    candidate_keys.append(_kl)

                            for _ck in candidate_keys:
                                entry = product_map.get(_ck)
                                if not isinstance(entry, dict):
                                    continue
                                for field in ('product_id', 'id_original', 'id_cleaned'):
                                    v = str(entry.get(field) or '').strip()
                                    if not v:
                                        continue
                                    v_base = os.path.splitext(_normalize_image_filename(os.path.basename(v)))[0].strip()
                                    if not v_base:
                                        continue
                                    for _e in (ext, '.jpg', '.jpeg', '.png', '.webp'):
                                        name_candidates.add(v_base + _e)
                    except Exception:
                        pass

                    return name_candidates, msg_id_candidates

                # CHANGE: 不再按 productos_exclude_file_prefixes 强制 404。
                # 需求改为：只要 Ya Subio（含子目录）存在图片就应显示，避免“有图但被屏蔽成空图/404”。
                _excl = getattr(self, 'productos_exclude_file_prefixes', None) or []
                if _excl:
                    _base_no_ext = os.path.splitext(base_filename or base_filename_clean or '')[0].lower()
                    if _base_no_ext and any(_base_no_ext.startswith(p) for p in _excl):
                        logger.info(f"ℹ️ 图片前缀命中排除列表但继续查找实际文件: {base_filename}")

                def _find_file_recursive(root_dir, target_name, max_depth=10, _depth=0, exclude_subdirs=None):
                    if _depth >= max_depth:
                        return None
                    exclude_subdirs = exclude_subdirs or []
                    try:
                        for item in os.listdir(root_dir):
                            item_path = os.path.join(root_dir, item)
                            if os.path.isfile(item_path):
                                if item.lower() == target_name.lower():
                                    return item_path
                                if os.path.splitext(item)[0].lower() == os.path.splitext(target_name)[0].lower():
                                    return item_path
                            elif os.path.isdir(item_path) and item not in exclude_subdirs:
                                r = _find_file_recursive(item_path, target_name, max_depth, _depth + 1, exclude_subdirs)
                                if r:
                                    return r
                    except (PermissionError, OSError, Exception):
                        pass
                    return None

                _mapped_name_candidates, _mapped_msg_ids = _candidate_names_from_mapping(base_filename_clean or base_filename)
                # CHANGE: 直接按请求文件名提取 message_id（如 Importadora_Chinito_31654.jpg -> 31654），
                # 避免缺少 mapping 文件时无法命中 msg_31654_xxx.jpg / telegram_31654.jpg。
                _raw_mid_name = os.path.splitext(_normalize_image_filename(base_filename_clean or base_filename or ''))[0].strip().lower().replace('-', '_')
                _direct_mid = None
                if _raw_mid_name:
                    _parts = _raw_mid_name.split('_')
                    if len(_parts) >= 2 and _parts[0] == 'msg' and _parts[1].isdigit() and len(_parts[1]) >= 2:
                        _direct_mid = _parts[1]
                    else:
                        for _p in reversed(_parts):
                            if _p.isdigit() and len(_p) >= 2:
                                _direct_mid = _p
                                break
                if _direct_mid and str(_direct_mid).isdigit():
                    _mapped_msg_ids.add(str(_direct_mid))

                def _find_file_by_mapped_candidates(root_dir, max_depth=10, exclude_subdirs=None):
                    """优先按映射候选文件名命中；其次按 msg_{message_id}_ 前缀命中。"""
                    if not os.path.isdir(root_dir):
                        return None
                    exclude_subdirs = exclude_subdirs or []
                    try:
                        # 1) 精确候选文件名
                        for cand in _mapped_name_candidates:
                            hit = _find_file_recursive(root_dir, cand, max_depth=max_depth, exclude_subdirs=exclude_subdirs)
                            if hit:
                                return hit
                        # 2) 按 msg_{message_id}_ 前缀兜底（对应 _build_upload_filename 规则）
                        if _mapped_msg_ids:
                            prefixes = [f"msg_{mid}_" for mid in _mapped_msg_ids]
                            stack = [(root_dir, 0)]
                            while stack:
                                cur, d = stack.pop()
                                if d >= max_depth:
                                    continue
                                try:
                                    for item in os.listdir(cur):
                                        p = os.path.join(cur, item)
                                        if os.path.isfile(p):
                                            low = item.lower()
                                            if any(low.startswith(pre) for pre in prefixes):
                                                return p
                                        elif os.path.isdir(p) and os.path.basename(p) not in exclude_subdirs:
                                            stack.append((p, d + 1))
                                except (PermissionError, OSError, Exception):
                                    continue
                    except Exception:
                        pass
                    return None

                # CHANGE: ULTIMO 产品图片固定从 pwa_cart/Ya Subio/Cristy 读取，优先在该目录查找
                if os.path.isdir(ULTIMO_IMAGE_DIR):
                    for try_name in (base_filename, base_filename_clean):
                        if not try_name:
                            continue
                        p = os.path.join(ULTIMO_IMAGE_DIR, try_name)
                        if os.path.isfile(p):
                            return send_from_directory(ULTIMO_IMAGE_DIR, try_name)
                    found_ultimo = _find_file_recursive(ULTIMO_IMAGE_DIR, base_filename)
                    if not found_ultimo and base_filename_clean != base_filename:
                        found_ultimo = _find_file_recursive(ULTIMO_IMAGE_DIR, base_filename_clean)
                    # CHANGE: 映射兜底（加密码/展示码 -> 真实文件名）
                    if not found_ultimo:
                        found_ultimo = _find_file_by_mapped_candidates(ULTIMO_IMAGE_DIR, max_depth=10)
                    if found_ultimo:
                        return send_from_directory(os.path.dirname(found_ultimo), os.path.basename(found_ultimo))
                # CHANGE: 遍历所有 product_image_dirs（含 output_images），使 PRODUCTOS 能提供其他供应商图
                _all_dirs = getattr(self, 'product_image_dirs', None) or [PWA_YA_SUBIO_BASE]
                image_dirs = list(_all_dirs) if _all_dirs else [PWA_YA_SUBIO_BASE]
                for images_dir in image_dirs:
                    if not os.path.isdir(images_dir):
                        continue
                    # 1) 优先在 Cristy 子文件夹内查找，确保 ULTIMO 页只显示 Cristy 内图片
                    cristy_sub = os.path.join(images_dir, 'Cristy')
                    if os.path.isdir(cristy_sub):
                        for try_name in (base_filename, base_filename_clean):
                            if not try_name:
                                continue
                            p = os.path.join(cristy_sub, try_name)
                            if os.path.isfile(p):
                                logger.info(f"✅ 图片（Cristy 子文件夹）: {p}")
                                return send_from_directory(cristy_sub, try_name)
                        found_in_cristy = _find_file_recursive(cristy_sub, base_filename)
                        if not found_in_cristy and base_filename_clean != base_filename:
                            found_in_cristy = _find_file_recursive(cristy_sub, base_filename_clean)
                        if not found_in_cristy:
                            found_in_cristy = _find_file_by_mapped_candidates(cristy_sub, max_depth=10)
                        if found_in_cristy:
                            logger.info(f"✅ 图片（Cristy 子文件夹）: {found_in_cristy}")
                            return send_from_directory(os.path.dirname(found_in_cristy), os.path.basename(found_in_cristy))
                    # 2) 根目录及非 Cristy 子文件夹（排除 Cristy，避免同名时用到根目录图）
                    for try_name in (base_filename, base_filename_clean):
                        if not try_name:
                            continue
                        p = os.path.join(images_dir, try_name)
                        if os.path.isfile(p):
                            logger.info(f"✅ 图片（可配置目录根）: {p}")
                            return send_from_directory(images_dir, try_name)
                    found_path = _find_file_recursive(images_dir, base_filename, exclude_subdirs=['Cristy'])
                    if not found_path and base_filename_clean != base_filename:
                        found_path = _find_file_recursive(images_dir, base_filename_clean, exclude_subdirs=['Cristy'])
                    if not found_path:
                        found_path = _find_file_by_mapped_candidates(images_dir, max_depth=10, exclude_subdirs=['Cristy'])
                    if found_path:
                        found_dir = os.path.dirname(found_path)
                        found_file = os.path.basename(found_path)
                        logger.info(f"✅ 图片（可配置目录子文件夹）: {found_path}")
                        return send_from_directory(found_dir, found_file)

                # CHANGE: 对 *_no_white / *_no_hay_precio 等后缀做兼容回退
                # 例如请求 importadoraWoni_463_no_white.jpg，实际磁盘可能只有 importadoraWoni_463.jpg。
                _req_name = (base_filename_clean or base_filename or '').strip()
                _req_stem, _req_ext = os.path.splitext(_req_name)
                _suffix_variants = [
                    '_no_white_no_hay_precio',
                    '_white_no_hay_precio',
                    '_no_hay_precio',
                    '_no_white',
                    '_white',
                ]
                _matched_suffix = next((s for s in _suffix_variants if _req_stem.lower().endswith(s)), '')
                if _matched_suffix:
                    _alt_stem = _req_stem[:-len(_matched_suffix)]
                    _alt_candidates = [
                        _alt_stem + (_req_ext or '.jpg'),
                        _alt_stem + '.jpg',
                        _alt_stem + '.jpeg',
                        _alt_stem + '.png',
                        _alt_stem + '.webp',
                    ]
                    # 去重保序
                    _seen_alt = set()
                    _alt_candidates = [x for x in _alt_candidates if not (x.lower() in _seen_alt or _seen_alt.add(x.lower()))]

                    # 先在已配置目录中按候选名查找
                    for _dir in image_dirs:
                        if not os.path.isdir(_dir):
                            continue
                        for _cand in _alt_candidates:
                            _hit = _find_file_recursive(_dir, _cand, max_depth=10)
                            if _hit:
                                logger.info(f"✅ 图片后缀回退命中: {_req_name} -> {os.path.basename(_hit)}")
                                return send_from_directory(os.path.dirname(_hit), os.path.basename(_hit))

                        # 再尝试同前缀模糊命中（避免后缀轻微差异）
                        _prefix = _alt_stem.lower() + '_'
                        _stack = [(_dir, 0)]
                        while _stack:
                            _cur, _d = _stack.pop()
                            if _d >= 10:
                                continue
                            try:
                                for _it in os.listdir(_cur):
                                    _p = os.path.join(_cur, _it)
                                    if os.path.isfile(_p):
                                        _bn, _be = os.path.splitext(_it)
                                        if _bn.lower() == _alt_stem.lower() and _be.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
                                            logger.info(f"✅ 图片后缀回退命中(同名不同后缀): {_req_name} -> {_it}")
                                            return send_from_directory(os.path.dirname(_p), os.path.basename(_p))
                                        if _bn.lower().startswith(_prefix) and _be.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
                                            logger.info(f"✅ 图片后缀回退命中(前缀): {_req_name} -> {_it}")
                                            return send_from_directory(os.path.dirname(_p), os.path.basename(_p))
                                    elif os.path.isdir(_p):
                                        _stack.append((_p, _d + 1))
                            except Exception:
                                continue
                else:
                    # CHANGE: 反向兼容：请求的是基础名 xxx.jpg，而磁盘是 xxx_no_white.jpg / xxx_no_hay_precio.jpg
                    _forward_candidates = []
                    for _sfx in _suffix_variants:
                        _forward_candidates.extend([
                            _req_stem + _sfx + (_req_ext or '.jpg'),
                            _req_stem + _sfx + '.jpg',
                            _req_stem + _sfx + '.jpeg',
                            _req_stem + _sfx + '.png',
                            _req_stem + _sfx + '.webp',
                        ])
                    _seen_fw = set()
                    _forward_candidates = [x for x in _forward_candidates if not (x.lower() in _seen_fw or _seen_fw.add(x.lower()))]

                    for _dir in image_dirs:
                        if not os.path.isdir(_dir):
                            continue
                        for _cand in _forward_candidates:
                            _hit = _find_file_recursive(_dir, _cand, max_depth=10)
                            if _hit:
                                logger.info(f"✅ 图片后缀反向回退命中: {_req_name} -> {os.path.basename(_hit)}")
                                return send_from_directory(os.path.dirname(_hit), os.path.basename(_hit))

                    # CHANGE: 兜底前缀匹配（例如请求 Importadora_Chinito_31654.jpg，磁盘存在 Importadora_Chinito_31654_no_white.jpg）
                    _prefix = _req_stem.lower() + '_'
                    for _dir in image_dirs:
                        if not os.path.isdir(_dir):
                            continue
                        _stack = [(_dir, 0)]
                        while _stack:
                            _cur, _d = _stack.pop()
                            if _d >= 10:
                                continue
                            try:
                                for _it in os.listdir(_cur):
                                    _p = os.path.join(_cur, _it)
                                    if os.path.isfile(_p):
                                        _bn, _be = os.path.splitext(_it)
                                        if _bn.lower().startswith(_prefix) and _be.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
                                            logger.info(f"✅ 图片前缀回退命中: {_req_name} -> {_it}")
                                            return send_from_directory(os.path.dirname(_p), os.path.basename(_p))
                                    elif os.path.isdir(_p):
                                        _stack.append((_p, _d + 1))
                            except Exception:
                                continue

                # 未在可配置目录中找到；若配置了 R2_IMAGE_BASE_URL 则重定向到 R2（Render 上无本地 Ya Subio 时用）
                r2_base = getattr(self, 'r2_image_base_url', None) or (os.getenv('R2_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
                if r2_base:
                    redirect_url = f"{r2_base}/{quote(base_filename_clean or base_filename)}"
                    logger.info(f"📷 本地无图，重定向到 R2: {redirect_url}")
                    return redirect(redirect_url, code=302)
                logger.warning(f"❌ 未找到图片: {filename}，可配置目录: {image_dirs}；返回 404")
                print(f"⚠️ [API] 未找到图片: {filename}，返回 404")
                return Response('', status=404)
            except Exception as e:
                logger.exception(f"❌ 图片服务异常: {filename} -> {e}")
                return Response('', status=404)
        
        @self.app.route('/api/info')
        def api_info():
            """API信息"""
            return jsonify({
                "service": "API del carrito PWA",
                "description": "API REST para la página del carrito PWA",
                "version": "1.0.0",
                "endpoints": {
                    "/": "PWA主页",
                    "/api/categories": "获取分类列表 (GET)",
                    "/api/products": "获取产品列表 (GET)",
                    "/api/products/<product_id>": "获取产品详情 (GET)",
                    "/api/cart": "获取购物车 (GET)",
                    "/api/cart/add": "添加商品到购物车 (POST)",
                    "/api/cart/update": "更新购物车商品数量 (POST)",
                    "/api/cart/remove": "从购物车移除商品 (POST)",
                    "/api/cart/clear": "清空购物车 (POST)",
                    "/api/cart/total": "计算购物车总价 (GET)",
                    "/api/checkout": "提交订单 (POST)",
                    "/api/orders": "获取订单列表 (GET)",
                    "/api/orders/<order_id>": "获取订单详情 (GET)",
                    "/api/sync/orders": "云端→本地同步订单 (GET, 需 X-Sync-Token 或 sync_token=SYNC_SECRET)",
                    "/api/payment/bank-info": "获取转账信息 (GET)",
                    "/api/health": "健康检查",
                    "/api/admin/sync-products-to-web": "将 Telegram 产品库同步到网页 (GET/POST)"
                }
            })
        
        @self.app.route('/api/health')
        def health_check():
            """健康检查"""
            return jsonify({
                "status": "healthy",
                "service": "API del carrito PWA",
                "timestamp": datetime.now().isoformat(),
                "database": "connected" if self.db else "disconnected"
            })

        @self.app.route('/api/categories/managed', methods=['GET'])
        def get_managed_categories():
            """管理端分类配置。兼容前端体检与管理页调用。"""
            try:
                if not self.db:
                    return jsonify({"success": True, "data": []})
                cats = self.db.get_categories() if hasattr(self.db, 'get_categories') else []
                out = []
                for c in (cats or []):
                    if isinstance(c, dict):
                        out.append({
                            "name": c.get('name') or c.get('id') or '',
                            "display_name": c.get('display_name') or c.get('name') or c.get('id') or '',
                            "bg": c.get('bg') or c.get('color') or '#206bc4',
                            "count": int(c.get('count') or 0)
                        })
                    else:
                        name = str(c or '').strip()
                        if name:
                            out.append({"name": name, "display_name": name, "bg": '#206bc4', "count": 0})
                return jsonify({"success": True, "data": out})
            except Exception as e:
                logger.error(f"❌ 获取 managed categories 失败: {e}")
                return jsonify({"success": False, "error": str(e), "data": []}), 500

        @self.app.route('/api/config/public', methods=['GET'])
        def get_public_config():
            """公开配置（前端可安全读取）。"""
            try:
                pages_base = getattr(self, 'pages_image_base_url', None) or (os.getenv('PAGES_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
                return jsonify({
                    "success": True,
                    "data": {
                        "pages_image_base_url": pages_base,
                        "api_base_url": "/api",
                        "service": "pwa_cart"
                    }
                })
            except Exception as e:
                logger.error(f"❌ 获取 public config 失败: {e}")
                return jsonify({"success": False, "error": str(e), "data": {}}), 500

        def _require_admin_token_for_request():
            """管理端接口校验 admin token（优先 X-Admin-Token，也兼容 Authorization: Bearer）。"""
            expected = (os.getenv('ADMIN_API_TOKEN') or os.getenv('ADMIN_TOKEN') or '').strip()
            provided = (request.headers.get('X-Admin-Token') or '').strip()
            if not provided:
                auth = (request.headers.get('Authorization') or '').strip()
                if auth.lower().startswith('bearer '):
                    provided = auth[7:].strip()
            if not expected:
                # 未配置时退化为兼容旧前端：要求传入非空 token
                if not provided:
                    return False, (jsonify({"success": False, "error": "Admin token missing"}), 401)
                return True, None
            if provided != expected:
                return False, (jsonify({"success": False, "error": "Invalid admin token"}), 401)
            return True, None

        @self.app.route('/api/admin/rules/auto-unpublish/run', methods=['POST'])
        def admin_run_auto_unpublish_rule():
            """管理端：立即执行自动下架规则；支持 dry_run（只统计不下架）。"""
            ok, err_resp = _require_admin_token_for_request()
            if not ok:
                return err_resp
            try:
                body = request.get_json(silent=True) or {}
                dry_run = str(request.args.get('dry_run', '') or body.get('dry_run', '')).strip().lower() in ('1', 'true', 'yes', 'on')

                pg_cfg = self._get_pg_config()
                if not pg_cfg or not PSYCOPG2_AVAILABLE or psycopg2 is None:
                    return jsonify({"success": False, "error": "PostgreSQL no está disponible"}), 500

                conn = self._pg_connect(pg_cfg)
                if not conn:
                    return jsonify({"success": False, "error": "No se pudo conectar a PostgreSQL"}), 500

                other_unpublished = 0
                cristy_unpublished = 0
                try:
                    cur = conn.cursor()
                    # 其他供应商：创建时间超过45天自动下架
                    if dry_run:
                        cur.execute(
                            """
                            SELECT COUNT(*)
                            FROM products
                            WHERE (esta_activo IS NULL OR esta_activo = TRUE)
                              AND LOWER(COALESCE(codigo_proveedor, '')) <> 'cristy'
                              AND COALESCE(fecha_creacion, NOW() - INTERVAL '100 years') < NOW() - INTERVAL '45 days'
                            """
                        )
                        other_unpublished = int((cur.fetchone() or [0])[0] or 0)
                    else:
                        cur.execute(
                            """
                            UPDATE products
                            SET esta_activo = FALSE
                            WHERE (esta_activo IS NULL OR esta_activo = TRUE)
                              AND LOWER(COALESCE(codigo_proveedor, '')) <> 'cristy'
                              AND COALESCE(fecha_creacion, NOW() - INTERVAL '100 years') < NOW() - INTERVAL '45 days'
                            """
                        )
                        other_unpublished = int(cur.rowcount or 0)

                    # Cristy：库存小于6自动下架
                    if dry_run:
                        cur.execute(
                            """
                            SELECT COUNT(*)
                            FROM products
                            WHERE (esta_activo IS NULL OR esta_activo = TRUE)
                              AND LOWER(COALESCE(codigo_proveedor, '')) = 'cristy'
                              AND COALESCE(inventario, 0) < 6
                            """
                        )
                        cristy_unpublished = int((cur.fetchone() or [0])[0] or 0)
                    else:
                        cur.execute(
                            """
                            UPDATE products
                            SET esta_activo = FALSE
                            WHERE (esta_activo IS NULL OR esta_activo = TRUE)
                              AND LOWER(COALESCE(codigo_proveedor, '')) = 'cristy'
                              AND COALESCE(inventario, 0) < 6
                            """
                        )
                        cristy_unpublished = int(cur.rowcount or 0)

                    if dry_run:
                        conn.rollback()
                    else:
                        conn.commit()
                    cur.close()
                finally:
                    conn.close()

                return jsonify({
                    "success": True,
                    "data": {
                        "dry_run": dry_run,
                        "executed_at": datetime.now().isoformat(),
                        "other_suppliers_older_than_45_days": other_unpublished,
                        "cristy_inventory_below_6": cristy_unpublished,
                        "total_unpublished": int(other_unpublished + cristy_unpublished)
                    }
                })
            except Exception as e:
                logger.error(f"❌ 自动下架执行失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        @_cached_api_response(lambda r: "categories", 60)
        @self.app.route('/api/categories', methods=['GET'])
        def get_categories():
            """获取分类（前端 expects: {success:true,data:[{name,count}...] }）"""
            try:
                # categories 主要用于 products?category=XXX 的列表筛选，因此必须返回与 products 接口一致的 category_id。
                if USE_SQLITE_FOR_PRODUCTS and self.db:
                    products = self.db.get_all_products()
                else:
                    products = self._get_products_dict_from_postgres()

                counts = {}
                for _pid, pinfo in (products or {}).items():
                    if not isinstance(pinfo, dict):
                        continue
                    if not pinfo.get('is_active', 1):
                        continue
                    cat_raw = pinfo.get('category_id', None) or pinfo.get('category', None) or 'default'
                    cats = _split_multi_categories(cat_raw)
                    if not cats:
                        cats = ['default']
                    for cat in cats:
                        counts[cat] = counts.get(cat, 0) + 1

                data = [{"name": k, "count": v} for k, v in counts.items()]
                # 排序规则与前端一致：Cristy 最前，Otros 最后，其余按 count desc
                def _rank(item):
                    n = str(item.get('name', '')).strip()
                    if n == 'Cristy':
                        return 0
                    if n == 'Otros':
                        return 2
                    return 1

                data.sort(key=lambda it: (_rank(it), -int(it.get('count', 0)), str(it.get('name', ''))))
                return jsonify({"success": True, "data": data})
            except Exception as e:
                logger.error(f"❌ 获取分类失败: {e}")
                print(f"❌ [API] 获取分类失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        def _query_suppliers_from_channel_username():
            """统一供应商来源：channel_username（优先 product_channel_messages，失败回退 products）"""
            pg_cfg = self._get_pg_config()
            if not pg_cfg or not PSYCOPG2_AVAILABLE or psycopg2 is None:
                return []

            conn = self._pg_connect(pg_cfg)
            if not conn:
                return []
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                try:
                    cur.execute(
                        """
                        SELECT
                          TRIM(channel_username) AS supplier,
                          COUNT(*) AS count
                        FROM product_channel_messages
                        WHERE channel_username IS NOT NULL
                          AND TRIM(channel_username) <> ''
                        GROUP BY 1
                        ORDER BY count DESC, supplier ASC
                        """
                    )
                    rows = cur.fetchall() or []
                except Exception as e:
                    logger.warning(f"⚠️ product_channel_messages 查询失败，回退 products: {e}")
                    cur.execute(
                        """
                        SELECT
                          TRIM(channel_username) AS supplier,
                          COUNT(*) AS count
                        FROM products
                        WHERE channel_username IS NOT NULL
                          AND TRIM(channel_username) <> ''
                        GROUP BY 1
                        ORDER BY count DESC, supplier ASC
                        """
                    )
                    rows = cur.fetchall() or []
                cur.close()
            finally:
                conn.close()

            data = []
            for r in rows:
                supplier = str((r or {}).get('supplier') or '').strip()
                if not supplier:
                    continue
                data.append({
                    "supplier": supplier,
                    "count": int((r or {}).get('count') or 0)
                })
            return data

        @self.app.route('/api/suppliers', methods=['GET'])
        def get_suppliers_public():
            """公开接口：供应商列表（channel_username）"""
            try:
                return jsonify({"success": True, "data": _query_suppliers_from_channel_username()})
            except Exception as e:
                logger.error(f"❌ 获取公开供应商列表失败: {e}")
                return jsonify({"success": False, "error": str(e), "data": []}), 500

        @self.app.route('/api/admin/suppliers', methods=['GET'])
        def admin_get_suppliers():
            """管理端：获取供应商列表（含商品数量）。按 channel_username 作为唯一来源。"""
            try:
                return jsonify({"success": True, "data": _query_suppliers_from_channel_username()})
            except Exception as e:
                logger.error(f"❌ 获取供应商列表失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        @self.app.route('/api/categories/assign-by-supplier', methods=['PATCH'])
        def assign_category_by_supplier():
            """管理端：按供应商批量修改分类"""
            try:
                data = request.get_json(silent=True) or {}
                supplier = str(data.get('supplier') or '').strip()
                category = _normalize_multi_category_storage(data.get('category'))
                if not supplier:
                    return jsonify({"success": False, "error": "supplier es obligatorio"}), 400

                pg_cfg = self._get_pg_config()
                if not pg_cfg or not PSYCOPG2_AVAILABLE or psycopg2 is None:
                    return jsonify({"success": False, "error": "PostgreSQL no está disponible"}), 500

                conn = self._pg_connect(pg_cfg)
                if not conn:
                    return jsonify({"success": False, "error": "No se pudo conectar a PostgreSQL"}), 500
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE products
                        SET categoria = %s
                        WHERE (esta_activo IS NULL OR esta_activo = TRUE)
                          AND LOWER(COALESCE(channel_username, '')) = LOWER(%s)
                        """,
                        (category, supplier)
                    )
                    updated = int(cur.rowcount or 0)
                    conn.commit()
                    cur.close()
                finally:
                    conn.close()

                return jsonify({
                    "success": True,
                    "data": {"updated": updated, "supplier": supplier, "category": category}
                })
            except Exception as e:
                logger.error(f"❌ 按供应商批量改分类失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        @self.app.route('/api/admin/products/<product_id>', methods=['PATCH', 'DELETE'])
        def admin_update_or_delete_product(product_id):
            """管理端：更新产品字段 / 软删除产品"""
            try:
                pid = str(product_id or '').strip()
                if not pid:
                    return jsonify({"success": False, "error": "product_id inválido"}), 400

                pg_cfg = self._get_pg_config()
                if not pg_cfg or not PSYCOPG2_AVAILABLE or psycopg2 is None:
                    return jsonify({"success": False, "error": "PostgreSQL no está disponible"}), 500

                conn = self._pg_connect(pg_cfg)
                if not conn:
                    return jsonify({"success": False, "error": "No se pudo conectar a PostgreSQL"}), 500

                try:
                    cur = conn.cursor()
                    if request.method == 'DELETE':
                        cur.execute(
                            """
                            UPDATE products
                            SET esta_activo = FALSE
                            WHERE codigo_producto = %s OR id_producto::text = %s
                            """,
                            (pid, pid)
                        )
                        affected = int(cur.rowcount or 0)
                        conn.commit()
                        cur.close()
                        if affected <= 0:
                            return jsonify({"success": False, "error": "Producto no encontrado"}), 404
                        return jsonify({"success": True, "data": {"deleted": affected, "product_id": pid}})

                    payload = request.get_json(silent=True) or {}
                    sets = []
                    params = []
                    updated_fields = {}
                    if 'name' in payload:
                        _name = str(payload.get('name') or '').strip()
                        sets.append('nombre_producto = %s')
                        params.append(_name)
                        updated_fields['name'] = _name
                    if 'category' in payload:
                        _cat = _normalize_multi_category_storage(payload.get('category'))
                        sets.append('categoria = %s')
                        params.append(_cat)
                        updated_fields['category'] = _cat

                    # 支持 code / product_code 两种前端字段名
                    if 'code' in payload or 'product_code' in payload:
                        _pc = str(payload.get('product_code', payload.get('code')) or '').strip()
                        sets.append('codigo_producto = %s')
                        params.append(_pc)
                        updated_fields['product_code'] = _pc

                    # 支持价格字段更新
                    if 'price' in payload:
                        try:
                            _price = float(payload.get('price') or 0)
                        except (TypeError, ValueError):
                            cur.close()
                            return jsonify({"success": False, "error": "price inválido"}), 400
                        sets.append('precio_unidad = %s')
                        params.append(_price)
                        updated_fields['price'] = _price

                    if 'wholesale_price' in payload:
                        try:
                            _w_price = float(payload.get('wholesale_price') or 0)
                        except (TypeError, ValueError):
                            cur.close()
                            return jsonify({"success": False, "error": "wholesale_price inválido"}), 400
                        sets.append('precio_mayor = %s')
                        params.append(_w_price)
                        updated_fields['wholesale_price'] = _w_price

                    if 'bulk_price' in payload:
                        try:
                            _b_price = float(payload.get('bulk_price') or 0)
                        except (TypeError, ValueError):
                            cur.close()
                            return jsonify({"success": False, "error": "bulk_price inválido"}), 400
                        sets.append('precio_bulto = %s')
                        params.append(_b_price)
                        updated_fields['bulk_price'] = _b_price

                    if not sets:
                        cur.close()
                        return jsonify({"success": False, "error": "No hay campos para actualizar"}), 400

                    params.extend([pid, pid])
                    sql = "UPDATE products SET " + ", ".join(sets) + " WHERE codigo_producto = %s OR id_producto::text = %s"
                    cur.execute(sql, tuple(params))
                    affected = int(cur.rowcount or 0)
                    conn.commit()
                    cur.close()

                    if affected <= 0:
                        return jsonify({"success": False, "error": "Producto no encontrado"}), 404
                    return jsonify({
                        "success": True,
                        "data": {
                            "updated": affected,
                            "product_id": pid,
                            "updated_field_names": list(updated_fields.keys()),
                            "updated_fields": updated_fields,
                            "message": f"Producto actualizado: {', '.join(updated_fields.keys())}"
                        }
                    })
                finally:
                    conn.close()
            except Exception as e:
                logger.error(f"❌ 管理端更新/删除产品失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

        @self.app.route('/api/debug-images')
        def debug_images():
            """调试：可配置图片目录状态及前几条产品解析出的 image_path，用于排查图片不显示（仅已处理目录 D:\\Ya Subio）"""
            _all_dirs = getattr(self, 'product_image_dirs', None) or [PWA_YA_SUBIO_BASE]
            image_dirs = [_all_dirs[0]] if _all_dirs else [PWA_YA_SUBIO_BASE]
            out = {
                "product_image_dirs_processed": image_dirs,
                "dirs_status": [],
                "total_image_file_count": 0,
                "first_product_image_paths": [],
                "sample_image_url": None,
            }
            def _list_image_files_recursive(root_dir, max_depth=10, _depth=0):
                if _depth >= max_depth:
                    return []
                out_list = []
                try:
                    for name in os.listdir(root_dir):
                        try:
                            p = os.path.join(root_dir, name)
                            if os.path.isfile(p) and name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                                out_list.append(name)
                            elif os.path.isdir(p):
                                out_list.extend(_list_image_files_recursive(p, max_depth, _depth + 1))
                        except OSError:
                            continue
                except (OSError, Exception):
                    pass
                return out_list
            for _d in image_dirs:
                exists = os.path.isdir(_d)
                count = len(_list_image_files_recursive(_d)) if exists else 0
                out["dirs_status"].append({"path": _d, "exists": exists, "image_count": count})
                out["total_image_file_count"] += count
            files = []
            for _d in image_dirs:
                if os.path.isdir(_d):
                    files.extend(_list_image_files_recursive(_d))
            # 去重保留首次出现（与 get_products 中 _files_ya_subio 一致）
            seen = set()
            files = [f for f in files if f not in seen and not seen.add(f)]
            if self.db or not USE_SQLITE_FOR_PRODUCTS:
                products = self.db.get_all_products() if (USE_SQLITE_FOR_PRODUCTS and self.db) else self._get_products_dict_from_postgres()
                products_to_list = list(products.items())[:5]
                import re
                def _resolve(pid, img_path):
                    if not files:
                        return img_path
                    fname = ''
                    if img_path:
                        raw = (img_path.replace('/api/images/', '').split('?')[0].strip() if img_path.startswith('/api/images/')
                               else os.path.basename(img_path.replace('/', os.sep).replace('\\', os.sep).strip()))
                        fname = _normalize_image_filename(raw)
                    if fname:
                        for fn in files:
                            if fn.lower() == fname.lower():
                                return f'/api/images/{fn}'
                    if fname and fname in files:
                        return f'/api/images/{fname}'
                    if fname:
                        name_no_ext = os.path.splitext(fname)[0]
                        for fn in files:
                            if os.path.splitext(fn)[0].lower() == name_no_ext.lower():
                                return f'/api/images/{fn}'
                    if fname:
                        name_no_ext = os.path.splitext(fname)[0]
                        nums = re.findall(r'\d+', name_no_ext)
                        parts = [p for p in re.split(r'[_\-.\s]+', name_no_ext) if len(p) >= 2 and not p.isdigit()]
                        for n in sorted(nums, key=len, reverse=True):
                            if len(n) >= 3:
                                for fn in files:
                                    if n in fn:
                                        return f'/api/images/{fn}'
                        for p in parts:
                            if len(p) >= 3:
                                for fn in files:
                                    if p.lower() in fn.lower():
                                        return f'/api/images/{fn}'
                    pid_str = str(pid).strip()
                    for ext in ('.jpg', '._AI.jpg', '.jpeg', '.png'):
                        if (pid_str + ext) in files:
                            return f'/api/images/{pid_str}{ext}'
                    for fn in files:
                        if pid_str.lower() in fn.lower():
                            return f'/api/images/{fn}'
                    return img_path if (img_path and img_path.startswith('/api/images/') and (img_path.replace('/api/images/', '').split('?')[0].strip() in files)) else ''
                for pid, pinfo in products_to_list:
                    ip = pinfo.get('image_path', '')
                    if ip and ('D:' in ip or 'C:' in ip or '\\' in ip or '/' in ip):
                        norm = ip.replace('/', os.sep).replace('\\', os.sep).strip()
                        ip = f'/api/images/{os.path.basename(norm)}'
                    elif ip and not ip.startswith('http') and not ip.startswith('/api/images/'):
                        ip = f'/api/images/{ip}'
                    resolved = _resolve(pid, ip)
                    out["first_product_image_paths"].append({
                        "product_id": pid,
                        "name": pinfo.get('name', '')[:50],
                        "db_image_path": pinfo.get('image_path', ''),
                        "image_path": resolved,
                    })
                if out["first_product_image_paths"] and out["first_product_image_paths"][0].get("image_path"):
                    out["sample_image_url"] = request.url_root.rstrip('/') + out["first_product_image_paths"][0]["image_path"]
            out["files_sample"] = files[:30]
            out["hint"] = "若 sample_image_url 在浏览器打开 404，请核对可配置目录内实际文件名是否与 image_path 中的文件名一致（含子文件夹）。"
            return jsonify(out)

        # NOTE: 将 Telegram 产品数据库同步到网页（与 pwa_cart/同步数据库.py 一致）
        @self.app.route('/api/admin/sync-products-to-web', methods=['GET', 'POST'])
        def sync_products_to_web():
            """将 Telegram/主程序 产品库同步到网页文件夹。GET/POST 支持 ?clear_cache=1 或 body {"clear_cache": true} 先清空目标库再全量同步。"""
            clear_cache = False
            if request.args.get('clear_cache') in ('1', 'true', 'yes'):
                clear_cache = True
            _j = request.get_json(silent=True) if request.is_json else None
            if _j and _j.get('clear_cache') is True:
                clear_cache = True
            ok, msg = self._sync_products_to_web(clear_cache=clear_cache)
            if ok:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                source_db = os.path.abspath(os.path.join(base_dir, '..', 'database', 'spanish_product_database.db'))
                target_db = os.path.abspath(os.path.join(base_dir, 'pwa_cart', 'spanish_product_database.db'))
                # 图片目录：同步只复制 DB，不复制图片；网页显示的图片从以下目录读取
                image_dirs = getattr(self, 'product_image_dirs', None) or [PWA_YA_SUBIO_BASE]
                logger.info(f"✅ 产品同步到网页成功: {msg}")
                return jsonify({
                    "success": True,
                    "message": "产品已同步到网页，已重新加载 DB 连接，刷新网页即可看到最新内容",
                    "detail": msg,
                    "clear_cache": clear_cache,
                    "source_db": source_db,
                    "target_db": target_db,
                    "image_dirs": image_dirs,
                    "image_dirs_note": "同步仅复制产品数据库，不复制图片。网页显示的图片从以上 image_dirs 目录读取。ULTIMO 页用子文件夹 Cristy，PRODUCTOS 用其余目录。",
                })
            logger.warning(f"⚠️ 产品同步到网页失败: {msg}")
            return jsonify({"success": False, "error": msg}), 500
        
        # CHANGE: 用户注册和登录API
        @self.app.route('/api/auth/db-status', methods=['GET'])
        def auth_db_status():
            """诊断：检查用户数据库连接状态（PostgreSQL vs SQLite）"""
            pg_ok = self._use_pg_for_users()
            user_count = 0
            if pg_ok:
                try:
                    pg_cfg = self._get_pg_config()
                    if pg_cfg and PSYCOPG2_AVAILABLE:
                        conn = self._pg_connect(pg_cfg)
                        if conn:
                            cur = conn.cursor()
                            cur.execute("SELECT COUNT(*) FROM pwa_users")
                            user_count = cur.fetchone()[0] or 0
                            cur.close()
                            conn.close()
                except Exception as e:
                    logger.warning(f"db-status 查询失败: {e}")
            return jsonify({
                "postgresql_configured": pg_ok,
                "pwa_users_count": user_count,
                "message": "PostgreSQL conectado" if pg_ok else "DATABASE_URL no configurado. Los usuarios se guardan en SQLite (se pierden al reiniciar)."
            }), 200

        @self.app.route('/api/auth/register', methods=['POST'])
        def register():
            """用户注册 - 邮箱注册"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "El cuerpo de la solicitud está vacío"}), 400
                
                email = data.get('email', '').strip().lower()
                # NOTE: 与登录一致，对密码做 strip，避免复制粘贴首尾空格导致注册/登录哈希不一致
                password = (data.get('password') or '').strip()
                name = data.get('name', '').strip()
                
                if not email:
                    return jsonify({"success": False, "error": "El correo no puede estar vacío"}), 400
                if not password or len(password) < 6:
                    return jsonify({"success": False, "error": "La contraseña debe tener al menos 6 caracteres"}), 400
                
                # CHANGE: 云端优先用 PostgreSQL 存储用户
                if self._use_pg_for_users():
                    if not self._get_pg_config():
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                    existing_user = self._pg_get_user_by_email(email)
                    if existing_user:
                        return jsonify({"success": False, "error": "El correo ya está registrado"}), 400
                    password_hash = self._hash_password(password)
                    user_id, error = self._pg_create_user(
                        email=email,
                        password_hash=password_hash,
                        name=name if name else email.split('@')[0],
                        registration_method='email'
                    )
                else:
                    if not self.db:
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                    existing_user = self.db.get_user_by_email(email)
                    if existing_user:
                        return jsonify({"success": False, "error": "El correo ya está registrado"}), 400
                    password_hash = self._hash_password(password)
                    user_id, error = self.db.create_user(
                        email=email,
                        password_hash=password_hash,
                        name=name if name else email.split('@')[0],
                        registration_method='email'
                    )
                
                if error:
                    return jsonify({"success": False, "error": error}), 400
                
                # 生成token
                if not JWT_AVAILABLE:
                    logger.error("❌ JWT库未安装，无法生成token")
                    print("❌ JWT库未安装，无法生成token")  # 控制台输出
                    return jsonify({"success": False, "error": "JWT no instalado. Ejecute: pip install PyJWT"}), 500
                
                try:
                    token = self._generate_token(user_id, email)
                    if not token:
                        logger.error(f"❌ 生成token失败: user_id={user_id}, email={email}, _generate_token返回None")
                        print(f"❌ 生成token失败: user_id={user_id}, email={email}, _generate_token返回None")  # 控制台输出
                        return jsonify({"success": False, "error": "Error al generar el token. Compruebe los logs del servidor"}), 500
                except Exception as token_error:
                    logger.error(f"❌ 生成token时发生异常: {token_error}")
                    import traceback
                    logger.error(traceback.format_exc())
                    print(f"❌ 生成token时发生异常: {token_error}")  # 控制台输出
                    return jsonify({"success": False, "error": f"Error al generar el token: {str(token_error)}"}), 500
                
                # 更新最后登录时间
                if self._use_pg_for_users():
                    self._pg_update_user_last_login(user_id)
                else:
                    self.db.update_user_last_login(user_id)
                
                logger.info(f"✅ 用户注册成功: user_id={user_id}, email={email}")
                
                return jsonify({
                    "success": True,
                    "data": {
                        "user_id": user_id,
                        "email": email,
                        "name": name if name else email.split('@')[0],
                        "token": token
                    },
                    "message": "Registro exitoso"
                })
                
            except Exception as e:
                logger.error(f"❌ 注册失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return jsonify({"success": False, "error": str(e)}), 500
        
        @self.app.route('/api/auth/login', methods=['POST'])
        def login():
            """用户登录 - 邮箱登录"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "El cuerpo de la solicitud está vacío"}), 400
                
                email = data.get('email', '').strip().lower()
                # NOTE: 对密码做 strip，避免复制粘贴首尾空格导致验证失败
                password = (data.get('password') or '').strip()
                
                logger.info(f"🔐 登录尝试: email={email}, password_length={len(password)}")
                print(f"🔐 登录尝试: email={email}, password_length={len(password)}")  # 控制台输出
                
                if not email or not password:
                    return jsonify({"success": False, "error": "El correo y la contraseña no pueden estar vacíos"}), 400
                
                # CHANGE: 云端优先用 PostgreSQL 获取用户
                if self._use_pg_for_users():
                    if not self._get_pg_config():
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                    user = self._pg_get_user_by_email(email)
                else:
                    if not self.db:
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                    user = self.db.get_user_by_email(email)
                logger.info(f"🔍 查询用户结果: user={'存在' if user else '不存在'}, email={email}")
                print(f"🔍 查询用户结果: user={'存在' if user else '不存在'}, email={email}")  # 控制台输出
                
                if not user:
                    logger.warning(f"❌ 用户不存在: email={email}")
                    print(f"❌ 用户不存在: email={email}")  # 控制台输出
                    return jsonify({"success": False, "error": "Correo o contraseña incorrectos"}), 401
                
                # 验证密码
                password_hash_in_db = user.get('password_hash', '')
                password_verify_result = self._verify_password(password, password_hash_in_db)
                logger.info(f"🔑 密码验证: email={email}, password_hash_length={len(password_hash_in_db)}, verify_result={password_verify_result}")
                print(f"🔑 密码验证: email={email}, password_hash_length={len(password_hash_in_db)}, verify_result={password_verify_result}")  # 控制台输出
                
                # CHANGE: 调试密码哈希
                input_password_hash = self._hash_password(password)
                logger.info(f"🔑 输入密码哈希: {input_password_hash[:20]}..., 数据库密码哈希: {password_hash_in_db[:20] if password_hash_in_db else 'None'}...")
                print(f"🔑 输入密码哈希: {input_password_hash[:20]}..., 数据库密码哈希: {password_hash_in_db[:20] if password_hash_in_db else 'None'}...")  # 控制台输出
                
                if not password_verify_result:
                    logger.warning(f"❌ 密码验证失败: email={email}")
                    print(f"❌ 密码验证失败: email={email}")  # 控制台输出
                    return jsonify({"success": False, "error": "Correo o contraseña incorrectos"}), 401
                
                # 检查用户是否激活
                if not user.get('is_active', True):
                    return jsonify({"success": False, "error": "La cuenta está deshabilitada"}), 403
                
                # 生成token
                if not JWT_AVAILABLE:
                    logger.error("❌ JWT库未安装，无法生成token")
                    print("❌ JWT库未安装，无法生成token")  # 控制台输出
                    return jsonify({"success": False, "error": "JWT no instalado. Ejecute: pip install PyJWT"}), 500
                
                logger.info(f"🔑 开始生成token: user_id={user['id']}, email={email}, JWT_AVAILABLE={JWT_AVAILABLE}")
                print(f"🔑 开始生成token: user_id={user['id']}, email={email}, JWT_AVAILABLE={JWT_AVAILABLE}")  # 控制台输出
                
                try:
                    token = self._generate_token(user['id'], email)
                    if not token:
                        logger.error(f"❌ 生成token失败: user_id={user['id']}, email={email}, _generate_token返回None")
                        print(f"❌ 生成token失败: user_id={user['id']}, email={email}, _generate_token返回None")  # 控制台输出
                        return jsonify({"success": False, "error": "Error al generar el token. Compruebe los logs del servidor"}), 500
                    logger.info(f"✅ Token生成成功: user_id={user['id']}, token长度={len(token)}")
                    print(f"✅ Token生成成功: user_id={user['id']}, token长度={len(token)}")  # 控制台输出
                except Exception as token_error:
                    logger.error(f"❌ 生成token时发生异常: {token_error}")
                    import traceback
                    logger.error(traceback.format_exc())
                    print(f"❌ 生成token时发生异常: {token_error}")  # 控制台输出
                    print(traceback.format_exc())  # 控制台输出
                    return jsonify({"success": False, "error": f"Error al generar el token: {str(token_error)}"}), 500
                
                # 更新最后登录时间
                if self._use_pg_for_users():
                    self._pg_update_user_last_login(user['id'])
                else:
                    self.db.update_user_last_login(user['id'])
                
                logger.info(f"✅ 用户登录成功: user_id={user['id']}, email={email}")
                
                return jsonify({
                    "success": True,
                    "data": {
                        "user_id": user['id'],
                        "email": user['email'],
                        "name": user.get('name', ''),
                        "avatar_url": user.get('avatar_url'),
                        "token": token
                    },
                    "message": "登录成功"
                })
                
            except Exception as e:
                logger.error(f"❌ 登录失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return jsonify({"success": False, "error": str(e)}), 500
        
        @self.app.route('/api/auth/verify', methods=['POST'])
        def verify_token():
            """验证token"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "El cuerpo de la solicitud está vacío"}), 400
                
                token = data.get('token')
                if not token:
                    return jsonify({"success": False, "error": "El token no puede estar vacío"}), 400
                
                payload = self._verify_token(token)
                if not payload:
                    return jsonify({"success": False, "error": "Token inválido o expirado"}), 401
                
                # CHANGE: 云端优先从 PostgreSQL 获取用户
                if self._use_pg_for_users():
                    if not self._get_pg_config():
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                    user = self._pg_get_user_by_id(payload.get('user_id'))
                else:
                    if not self.db:
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                    user = self.db.get_user_by_id(payload.get('user_id'))
                if not user:
                    return jsonify({"success": False, "error": "El usuario no existe"}), 404
                
                return jsonify({
                    "success": True,
                    "data": {
                        "user_id": user['id'],
                        "email": user.get('email'),
                        "name": user.get('name'),
                        "avatar_url": user.get('avatar_url')
                    }
                })
                
            except Exception as e:
                logger.error(f"❌ 验证token失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
        
        # CHANGE: 忘记密码 - 请求重置
        @self.app.route('/api/auth/forgot-password', methods=['POST'])
        def forgot_password():
            """发送密码重置链接（LAN 环境：直接返回重置 URL）"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "El cuerpo de la solicitud está vacío"}), 400
                email = data.get('email', '').strip().lower()
                if not email:
                    return jsonify({"success": False, "error": "El correo no puede estar vacío"}), 400
                # CHANGE: 云端优先用 PostgreSQL
                if self._use_pg_for_users():
                    if not self._get_pg_config():
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                else:
                    if not self.db:
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                expires_at = (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
                if self._use_pg_for_users():
                    user_id = self._pg_create_password_reset_token(email, token_hash, expires_at)
                else:
                    user_id = self.db.create_password_reset_token(email, token_hash, expires_at)
                if not user_id:
                    # NOTE: 未发送邮件；链接仅在邮箱已注册时于页面上显示
                    return jsonify({"success": True, "message": "Si el correo está registrado, el enlace de restablecimiento aparecerá en esta página (no se envía por correo)."}), 200
                # CHANGE: 优先用 RESET_LINK_BASE_URL，使链接始终指向固定前端（如 https://ventax.pages.dev/pwa_cart）
                if self.reset_link_base_url:
                    reset_url = f"{self.reset_link_base_url}/#/reset?token={token}"
                else:
                    base_url = request.url_root.rstrip('/')
                    reset_url = f"{base_url}/pwa_cart/#/reset?token={token}"
                # CHANGE: 同时返回 reset_token，供前端直接弹重置表单，无需用户点击链接（避免客户抗拒链接/担心诈骗）
                return jsonify({
                    "success": True,
                    "reset_url": reset_url,
                    "reset_token": token,
                    "message": "Introduce tu nueva contraseña a continuación."
                }), 200
            except Exception as e:
                logger.error(f"❌ 忘记密码失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
        
        # CHANGE: 重置密码
        @self.app.route('/api/auth/reset-password', methods=['POST'])
        def reset_password():
            """使用 token 重置密码"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "El cuerpo de la solicitud está vacío"}), 400
                token = data.get('token', '').strip()
                new_password = data.get('password', '')
                if not token:
                    return jsonify({"success": False, "error": "El token no puede estar vacío"}), 400
                if not new_password or len(new_password) < 6:
                    return jsonify({"success": False, "error": "La contraseña debe tener al menos 6 caracteres"}), 400
                # CHANGE: 云端优先用 PostgreSQL
                if self._use_pg_for_users():
                    if not self._get_pg_config():
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                else:
                    if not self.db:
                        return jsonify({"success": False, "error": "Base de datos no conectada"}), 500
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                if self._use_pg_for_users():
                    user = self._pg_get_user_by_reset_token(token_hash)
                else:
                    user = self.db.get_user_by_reset_token(token_hash)
                if not user:
                    return jsonify({"success": False, "error": "Enlace inválido o expirado"}), 400
                password_hash = self._hash_password(new_password)
                if self._use_pg_for_users():
                    ok = self._pg_update_password_and_clear_reset(user['id'], password_hash)
                else:
                    ok = self.db.update_password_and_clear_reset(user['id'], password_hash)
                if not ok:
                    return jsonify({"success": False, "error": "Error al actualizar la contraseña"}), 500
                return jsonify({"success": True, "message": "Contraseña restablecida correctamente"}), 200
            except Exception as e:
                logger.error(f"❌ 重置密码失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
        
        @self.app.route('/api/products', methods=['GET'])
        @_cached_api_response(
            _products_list_cache_key,
            _API_CACHE_TTL_PRODUCTS
        )
        def get_products():
            """获取产品列表 - 按新到旧排序，只显示激活的产品"""
            category = request.args.get('category', None)
            search = request.args.get('search', None)
            supplier = request.args.get('supplier', None)  # CHANGE: 支持 supplier 参数筛选
            logger.info(f"📥 [API] 收到 /api/products 请求 supplier={supplier!r}, search={search!r}")
            print(f"📥 [API] 收到 /api/products 请求 supplier={supplier!r}, search={search!r}")
            try:
                if not self.db:
                    return jsonify({"error": "Base de datos no conectada"}), 500

                def _dedupe_api_rows(rows):
                    seen = set()
                    out = []
                    for p in (rows or []):
                        code_key = str((p or {}).get('product_code') or '').strip().upper()
                        code_key = re.sub(r'[\s_\-]+', '', code_key)
                        code_key = re.sub(r'\._A[Ii]$', '', code_key)
                        id_key = str((p or {}).get('id') or '').strip().upper()
                        name_key = str((p or {}).get('name') or '').strip().upper()
                        fallback_key = '|'.join([x for x in [name_key, str(float((p or {}).get('price') or 0)), str(float((p or {}).get('wholesale_price') or 0))] if x])
                        k = code_key or id_key or fallback_key
                        if not k or k in seen:
                            continue
                        seen.add(k)
                        out.append(p)
                    return out
                
                # 获取查询参数
                supplier_lower = (supplier or '').strip().lower()  # 统一小写比较，避免 Others/others 等导致走错分支
                page, limit = _products_list_effective_page_limit(request)
                # CHANGE: 强制“完全无过滤全量返回”模式（忽略 supplier/category/search/is_active/image 是否存在）。
                force_unfiltered_mode = True
                # CHANGE: 移除 supplier=others 早期返回空，让 PRODUCTOS 按「DB 为主 + 图片在 D:\Ya Subio 匹配」正常显示
                # 获取所有产品（暂时註销 SQLite 时仅用 PostgreSQL）
                if USE_SQLITE_FOR_PRODUCTS and self.db:
                    products = self.db.get_all_products()
                else:
                    products = self._get_products_dict_from_postgres()
                logger.info(f"📦 [API] 已从 PG 加载产品数: {len(products)}")
                print(f"📦 [API] 已从 PG 加载产品数: {len(products)}")
                
                # CHANGE: 自家产品标识 - 使用 codigo_proveedor = 'Cristy'
                OWN_SUPPLIER_CODE = 'Cristy'
                
                # CHANGE: 已移除 PRODUCTOS 日期过滤（日期应以图片上传之时起计，DB created_at 非图传时间）
                cristy_from_pg = self._get_ultimo_products_from_postgres()
                cristy_products, all_filtered_products, skipped_by_date, skipped_cristy_by_stock = self._filter_products_cristy_and_others(
                    products, cristy_from_pg, None, OWN_SUPPLIER_CODE
                )
                
                # CHANGE: 根据 supplier 参数决定使用哪个产品列表（抽取到 _select_products_by_supplier 降低复杂度）
                logger.info(f"📊 [API] 产品统计: 总产品={len(products)}, PRODUCTOS(其他)={len(all_filtered_products)}, ULTIMO(Cristy/库存>=6)={len(cristy_products)}, Cristy库存下架={skipped_cristy_by_stock}, supplier={supplier}")
                print(f"📊 [API] 产品统计: 总产品={len(products)}, PRODUCTOS(其他)={len(all_filtered_products)}, ULTIMO(Cristy)={len(cristy_products)}, supplier={supplier}")
                if len(all_filtered_products) > 0:
                    sample_providers = [pinfo.get('codigo_proveedor', 'NULL') for _, pinfo in all_filtered_products[:3]]
                    print(f"🔍 [API] 前3个产品的 codigo_proveedor: {sample_providers}")

                # CHANGE: 分类筛选优先在后端执行，避免前端 search/category 叠加导致空结果
                if category:
                    products_to_process = []
                    cat_query = str(category).strip().lower()
                    for pid, pinfo in products.items():
                        if not isinstance(pinfo, dict) or not pinfo.get('is_active', 1):
                            continue
                        # 多分类字段匹配
                        if not _multi_category_contains(pinfo.get('category_id'), cat_query):
                            continue
                        # supplier 再筛
                        if supplier_lower:
                            cp = (pinfo.get('codigo_proveedor') or '').strip().lower()
                            if supplier_lower == OWN_SUPPLIER_CODE.lower() and cp != OWN_SUPPLIER_CODE.lower():
                                continue
                            if supplier_lower == 'others' and cp == OWN_SUPPLIER_CODE.lower():
                                continue
                            if supplier_lower not in (OWN_SUPPLIER_CODE.lower(), 'others') and cp != supplier_lower:
                                continue
                        products_to_process.append((pid, pinfo))
                    products_to_process.sort(key=lambda x: x[1].get('created_at', '') or '', reverse=True)
                    logger.info(f"📦 [API] category={category} 后端筛选结果: {len(products_to_process)}")
                    print(f"📦 [API] category={category} 后端筛选结果: {len(products_to_process)}")
                else:
                    products_to_process = self._select_products_by_supplier(
                        cristy_products, all_filtered_products, products, supplier_lower, search, OWN_SUPPLIER_CODE
                    )

                # CHANGE: 完全无过滤全量返回（忽略 supplier/category/search/is_active/image）。
                full_db_mode = False
                if force_unfiltered_mode:
                    full_db_mode = True
                    # products 字典会同时放 id 键与 product_code 键，按对象身份去重。
                    _seen_obj = set()
                    products_to_process = []
                    for pid, pinfo in products.items():
                        if not isinstance(pinfo, dict):
                            continue
                        _oid = id(pinfo)
                        if _oid in _seen_obj:
                            continue
                        _seen_obj.add(_oid)
                        products_to_process.append((pid, pinfo))
                    logger.info(f"📦 [API] 强制无过滤全量模式: 去重后 {len(products_to_process)} 条")
                    print(f"📦 [API] 强制无过滤全量模式: 去重后 {len(products_to_process)} 条")

                # CHANGE: 指定 supplier（非 Cristy/others）时，按数据库字段精确筛选，
                # 不受 PRODUCTOS 白名单限制（例如 WONI_IMPORT_AND_EXPORT / importadoraWoni / IMP235）
                if (not force_unfiltered_mode) and supplier_lower and supplier_lower not in (OWN_SUPPLIER_CODE.lower(), 'others'):
                    def _norm_supplier_alias(v):
                        s = str(v or '').strip().lower().replace('-', '').replace('_', '').replace(' ', '')
                        if not s:
                            return ''
                        # CHANGE: importadoraWoni / WONI_IMPORT_AND_EXPORT / IMP235 视为同一供应商组，避免图2图3分裂
                        if s in ('importadorawoni', 'woniimportandexport', 'imp235'):
                            return 'woni-group'
                        return s

                    requested_supplier = _norm_supplier_alias(supplier_lower)
                    direct_filtered = []
                    for pid, pinfo in products.items():
                        if not isinstance(pinfo, dict):
                            continue
                        if not pinfo.get('is_active', 1):
                            continue
                        cp = _norm_supplier_alias(pinfo.get('codigo_proveedor'))
                        cu = _norm_supplier_alias(pinfo.get('channel_username'))
                        sp = _norm_supplier_alias(pinfo.get('supplier'))
                        if requested_supplier and requested_supplier in (cp, cu, sp):
                            direct_filtered.append((pid, pinfo))
                    direct_filtered.sort(key=lambda x: x[1].get('created_at', '') or '', reverse=True)
                    products_to_process = direct_filtered
                    logger.info(f"📦 [API] supplier={supplier} 别名归一筛选命中: {len(products_to_process)}")
                    print(f"📦 [API] supplier={supplier} 别名归一筛选命中: {len(products_to_process)}")
                
                # CHANGE: 有搜索关键词时，强制使用 ULTIMO+PRODUCTOS 并集，实现跨两页搜索
                # CHANGE: 按 product_code（规范化：去 ._AI 后缀、小写）去重，避免同一产品多供应商/多渠道重复
                def _norm_code(pid, pinfo):
                    code = (pinfo.get('product_code') or pinfo.get('codigo_producto') or '').strip()
                    raw = code or str(pid or '').strip()
                    if not raw:
                        return raw
                    return re.sub(r'\._A[Ii]\s*$', '', raw, flags=re.IGNORECASE).strip().lower() or raw.lower()
                if (not force_unfiltered_mode) and search and str(search).strip():
                    # CHANGE: 搜索时包含所有产品（绕过日期过滤），确保按产品代码可搜到任意产品
                    seen_search = set()
                    combined_search = []
                    for pid, pinfo in cristy_products:
                        key = _norm_code(pid, pinfo) or str(pid)
                        if key not in seen_search:
                            seen_search.add(key)
                            combined_search.append((pid, pinfo))
                    for pid, pinfo in all_filtered_products:
                        key = _norm_code(pid, pinfo) or str(pid)
                        if key not in seen_search:
                            seen_search.add(key)
                            combined_search.append((pid, pinfo))
                    # NOTE: 补充被日期过滤掉的「其他供应商」产品，使按产品代码搜索能命中任意产品
                    _whitelist = getattr(self, 'other_supplier_codes', None) or ['Importadora_Chinito', 'IMP158', 'Importadorawoni', 'ayacuchoamoreshop', 'ecuarticulos']
                    _excl_search = getattr(self, 'productos_exclude_file_prefixes', None) or []
                    for pid, pinfo in products.items():
                        if not pinfo.get('is_active', 1):
                            continue
                        cp = (pinfo.get('codigo_proveedor') or '').strip().lower()
                        if cp == OWN_SUPPLIER_CODE.lower():
                            continue
                        if not cp or cp not in [c.lower() for c in _whitelist if c]:
                            continue
                        # CHANGE: 排除未完成处理的产品（如图片 importadoraWoni_ 仅OCR未白底）
                        if _excl_search:
                            _img = (pinfo.get('ruta_imagen_raw') or pinfo.get('ruta_imagen') or pinfo.get('image_path') or '')
                            _img_base = os.path.basename(str(_img).replace('\\', '/')).lower() if _img else ''
                            if _img_base and any(_img_base.startswith(p) for p in _excl_search):
                                continue
                        key = _norm_code(pid, pinfo) or str(pid)
                        if key not in seen_search:
                            seen_search.add(key)
                            combined_search.append((pid, pinfo))
                    combined_search.sort(key=lambda x: x[1].get('created_at', '') or '', reverse=True)
                    products_to_process = combined_search
                    logger.info(f"🔍 [API] 搜索模式：使用全量产品并集共 {len(products_to_process)} 个产品进行搜索（含被日期过滤的）")
                    print(f"🔍 [API] 搜索模式：使用全量产品并集共 {len(products_to_process)} 个产品进行搜索")
                
                # CHANGE: 图片文件名从可配置目录（port_config.json pwa_cart.product_image_dirs）递归收集，与 serve_product_image 一致
                # NOTE: re 已在文件顶部 import，此处不再 import 避免 _norm_code 等闭包在 import 前被调用时报错
                def _list_image_files_recursive(root_dir, max_depth=10, _depth=0):
                    """递归收集 root_dir 及其子文件夹内的图片文件名（仅 basename）"""
                    if _depth >= max_depth:
                        return []
                    out = []
                    try:
                        for name in os.listdir(root_dir):
                            try:
                                p = os.path.join(root_dir, name)
                                if os.path.isfile(p):
                                    if name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                                        out.append(name)
                                elif os.path.isdir(p):
                                    out.extend(_list_image_files_recursive(p, max_depth, _depth + 1))
                            except OSError:
                                continue
                    except (OSError, Exception):
                        pass
                    return out
                # CHANGE: PRODUCTOS 用 D:\Ya Subio（排除 Cristy 子文件夹）；ULTIMO 固定从 D:\Ya Subio\Cristy 读取
                _all_dirs = getattr(self, 'product_image_dirs', None) or [PWA_YA_SUBIO_BASE]
                _processed_dir = _all_dirs[0] if _all_dirs else PWA_YA_SUBIO_BASE
                _cristy_subdir = ULTIMO_IMAGE_DIR if os.path.isdir(ULTIMO_IMAGE_DIR) else os.path.join(_processed_dir, 'Cristy')

                def _list_image_files_recursive_exclude(root_dir, exclude_subdirs, max_depth=10, _depth=0):
                    """递归收集图片文件名，跳过 exclude_subdirs 中的子文件夹名"""
                    if _depth >= max_depth:
                        return []
                    out = []
                    try:
                        for name in os.listdir(root_dir):
                            try:
                                p = os.path.join(root_dir, name)
                                if os.path.isfile(p):
                                    if name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                                        out.append(name)
                                elif os.path.isdir(p) and name not in exclude_subdirs:
                                    out.extend(_list_image_files_recursive_exclude(p, exclude_subdirs, max_depth, _depth + 1))
                            except OSError:
                                continue
                    except (OSError, Exception):
                        pass
                    return out

                _files_ya_subio_no_cristy = []
                _files_ya_subio_only = []  # CHANGE: 仅 pwa_cart/Ya Subio（排除 Cristy），供 PRODUCTOS 严格「以 DB 为主 + 图在 Ya Subio 匹配」
                _files_cristy = []
                try:
                    _seen_basenames = set()
                    for _d in _all_dirs:
                        if not os.path.isdir(_d):
                            continue
                        _is_ya_subio = 'Ya Subio' in _d or os.path.basename(_d.rstrip(os.sep)) == 'Ya Subio'
                        if _is_ya_subio:
                            _lst = _list_image_files_recursive_exclude(_d, ['Cristy'])
                            for _f in _lst:
                                if _f not in _seen_basenames:
                                    _seen_basenames.add(_f)
                                    _files_ya_subio_no_cristy.append(_f)
                                    _files_ya_subio_only.append(_f)
                        else:
                            # product_images、output_images 等：只加入 _files_ya_subio_no_cristy（供 serve_product_image 等），不加入 _files_ya_subio_only
                            _lst = _list_image_files_recursive(_d)
                            for _f in _lst:
                                if _f not in _seen_basenames:
                                    _seen_basenames.add(_f)
                                    _files_ya_subio_no_cristy.append(_f)
                    if os.path.isdir(_cristy_subdir):
                        _files_cristy = _list_image_files_recursive(_cristy_subdir)
                except (OSError, Exception):
                    pass
                # CHANGE: ULTIMO 时若 _files_cristy 为空，尝试回退到固定路径 ULTIMO_IMAGE_DIR
                _is_cristy_request = supplier and (supplier == OWN_SUPPLIER_CODE or (isinstance(supplier, str) and supplier.strip().lower() == OWN_SUPPLIER_CODE.lower()))
                if _is_cristy_request and not _files_cristy and os.path.isdir(ULTIMO_IMAGE_DIR):
                    try:
                        _files_cristy = _list_image_files_recursive(ULTIMO_IMAGE_DIR)
                        logger.info(f"📷 [API] ULTIMO 使用回退路径 Cristy: 共 {len(_files_cristy)} 张图")
                        print(f"📷 [API] ULTIMO 使用回退路径 Cristy: 共 {len(_files_cristy)} 张图")
                    except (OSError, Exception):
                        pass
                # 按 supplier 选择图片列表（仅影响日志）；CHANGE: 过滤与解析统一用「D:\Ya Subio + D:\Ya Subio\Cristy」并集，只显示两目录任一有对应图的产品
                _files_ya_subio_merged = _files_ya_subio_no_cristy + [f for f in _files_cristy if f not in _files_ya_subio_no_cristy]
                if _is_cristy_request:
                    logger.info(f"📷 [API] ULTIMO 使用 D:\\Ya Subio\\Cristy: 共 {len(_files_cristy)} 张图")
                    print(f"📷 [API] ULTIMO 使用 Cristy 目录: 共 {len(_files_cristy)} 张图")
                elif supplier == 'others':
                    logger.info(f"📷 [API] PRODUCTOS 使用非Cristy图（Ya Subio+product_images+output_images）: 共 {len(_files_ya_subio_no_cristy)} 张图")
                    print(f"📷 [API] PRODUCTOS 使用非Cristy图: 共 {len(_files_ya_subio_no_cristy)} 张图")
                else:
                    logger.info(f"📷 [API] 图片目录 D:\\Ya Subio 全量: 共 {len(_files_ya_subio_merged)} 张图")
                    print(f"📷 [API] 图片目录: 共 {len(_files_ya_subio_merged)} 张图")
                # 过滤与解析统一用并集：只显示「D:\Ya Subio 或 D:\Ya Subio\Cristy 内有对应图片」的产品
                _files_ya_subio = _files_ya_subio_merged
                # CHANGE: supplier=others 时用 _files_ya_subio_no_cristy（含 Ya Subio + product_images + output_images），使 PRODUCTOS 能显示其他供应商产品图
                _files_for_resolve = _files_ya_subio_no_cristy if supplier_lower == 'others' else _files_ya_subio
                if not _files_ya_subio and _processed_dir:
                    logger.warning(f"⚠️ [API] 可配置图片目录下未扫到任何图片，请检查路径与权限: {_processed_dir}, {_cristy_subdir}")
                    print(f"⚠️ [API] 可配置图片目录下未扫到任何图片，请检查路径与权限: {_processed_dir}, {_cristy_subdir}")
                elif _files_ya_subio:
                    print(f"📷 [API] 图片文件名样本(前15): {_files_ya_subio[:15]}")

                # CHANGE: supplier=Cristy 时以图为准：先遍历图片文件夹，用文件名解析 product_id，再查库填 name/price，保证一图一产品数据不错位
                # CHANGE: 有 search 时强制走 filtered_with_meta 逻辑，确保搜索过滤生效
                _skip_image_first = bool((not force_unfiltered_mode) and search and str(search).strip())
                print(f"📷 [API] Cristy 检查: _is_cristy_request={_is_cristy_request}, len(cristy_products)={len(cristy_products)}, len(_files_cristy)={len(_files_cristy)}, _cristy_subdir={_cristy_subdir!r}, _skip_image_first={_skip_image_first}")
                if (not force_unfiltered_mode) and (not _skip_image_first) and _is_cristy_request and len(cristy_products) > 0 and len(_files_cristy) > 0:
                    _lookup = {}
                    for _pid, _pinfo in cristy_products:
                        _key = (str(_pid).strip().lower() if _pid else '').strip()
                        if not _key:
                            continue
                        _lookup[_key] = (_pid, _pinfo)
                        _lookup[_normalize_base_ai_al(_key)] = (_pid, _pinfo)
                        # 图片可能为 10060.jpg、10060._AI.jpg；用「去掉 ._ai 后缀」作 key 便于匹配
                        _prefix = re.sub(r'[._\-]*(?:ai|al)$', '', _key.strip(), flags=re.IGNORECASE).strip()
                        if _prefix and _prefix != _key:
                            _lookup[_prefix] = (_pid, _pinfo)
                        # 纯数字段（如 10060）也登记，便于 10060.jpg 匹配 10060._AI
                        _nums = re.findall(r'^\d+', _key)
                        if _nums:
                            _lookup[_nums[0]] = (_pid, _pinfo)
                    _image_first_list = []
                    for _f in _files_cristy:
                        _base = os.path.splitext(_f)[0].strip()
                        _base_lower = _base.lower()
                        _base_norm = _normalize_base_ai_al(_base_lower)
                        _pair = (_lookup.get(_base_norm) or _lookup.get(_base_lower) or _lookup.get(_base) or
                                 _lookup.get(re.sub(r'[._\-]*(?:ai|al)$', '', _base_lower, flags=re.IGNORECASE).strip()))
                        if not _pair:
                            _lead_digits = re.findall(r'^\d+', _base_lower)
                            if _lead_digits:
                                _pair = _lookup.get(_lead_digits[0])
                        if _pair:
                            _pid, _pinfo = _pair
                            _created = _pinfo.get('created_at', '')
                            _image_first_list.append((_pid, _pinfo, _created, '/api/images/' + _f, _base))
                        else:
                            # CHANGE: 即使库内无匹配，也按图片文件名显示一卡，避免错用其他产品数据
                            _image_first_list.append((_base, {'name': _base, 'product_code': _base, 'price': 0, 'wholesale_price': 0, 'bulk_price': 0, 'description': '', 'created_at': '', 'category_id': 'default', 'channel_username': '', 'codigo_proveedor': 'Cristy'}, '', '/api/images/' + _f, _base))
                    _image_first_list.sort(key=lambda x: x[2], reverse=True)
                    _total_cristy = len(_image_first_list)
                    _start = (page - 1) * limit
                    _end = _start + limit
                    _slice = _image_first_list[_start:_end]
                    paginated_products = []
                    for _row in _slice:
                        _pid, _pinfo, _created, _img_path = _row[0], _row[1], _row[2], _row[3]
                        _base_from_image = _row[4] if len(_row) > 4 else (os.path.splitext(_img_path.split('/')[-1])[0] if _img_path else '')
                        # CHANGE: 展示用 product_code 以图片文件名为准，保证页上代码与图片一致；id 保持库内 id 便于加购
                        _code = (_pinfo.get('product_code') or _pinfo.get('codigo_producto') or _pid)
                        if hasattr(_code, 'strip'):
                            _code = (_code or '').strip()
                        else:
                            _code = str(_code or '').strip()
                        _display_code = (_base_from_image.strip() if (_base_from_image and getattr(_base_from_image, 'strip')) else _base_from_image) or _code or str(_pid)
                        # CHANGE: 名称优先用 DB，空时才用展示码
                        _db_name = (_pinfo.get('name', '') or '').strip()
                        _display_name = _db_name if _db_name else (_display_code or '')
                        _pu = float(_pinfo.get('price') or 0)
                        _pm = float(_pinfo.get('wholesale_price') or 0)
                        _pb = float(_pinfo.get('bulk_price') or 0)
                        _display_price = _pm if _pm > 0 else (_pu if _pu > 0 else _pb)
                        # CHANGE: price 必须为单价(precio_unidad)，供弹窗/加购按数量 1-2 单价/3-11 批发/12+ 批量 正确计算
                        paginated_products.append({
                            'id': _pid,
                            'product_code': _display_code,
                            'name': _display_name,
                            'price': _pu,
                            'wholesale_price': _pinfo.get('wholesale_price', 0),
                            'bulk_price': _pinfo.get('bulk_price', 0),
                            'description': _pinfo.get('description', ''),
                            'image_path': _img_path,
                            'category': _normalize_multi_category_storage(_pinfo.get('category_id', 'default')),
                            'created_at': _created,
                            'channel_username': _pinfo.get('channel_username', ''),
                            'codigo_proveedor': _pinfo.get('codigo_proveedor', '')
                        })
                    paginated_products = _dedupe_api_rows(paginated_products)
                    for i, p in enumerate(paginated_products[:3]):
                        print(f"   [Cristy图为准] 产品[{i}] id={p.get('id')} name={p.get('name')[:40] if p.get('name') else ''} price={p.get('price')} image={p.get('image_path', '')[:60]}")
                    total_filtered = _total_cristy
                    resp = jsonify({
                        "success": True,
                        "data": paginated_products,
                        "total": total_filtered,
                        "pagination": {
                            "page": page,
                            "limit": limit,
                            "total": total_filtered,
                            "total_pages": (total_filtered + limit - 1) // limit if total_filtered else 1
                        }
                    })
                    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
                    resp.headers['Pragma'] = 'no-cache'
                    resp.headers['X-Image-Logic'] = 'cristy-image-first'
                    return resp
                # CHANGE: PRODUCTOS(supplier=others) 按「产品图片名称」查找映射：用全库 products 的 ruta_imagen 建 文件名->产品
                # 使用 _files_ya_subio_no_cristy（含 Ya Subio + product_images + output_images），使新上传产品图能显示
                # CHANGE: 有 search 时跳过「以图为准」分支，强制走 filtered_with_meta 确保搜索过滤
                if (not force_unfiltered_mode) and (not _skip_image_first) and supplier_lower == 'others' and len(_files_ya_subio_no_cristy) > 0:
                    # CHANGE: 合并 PostgreSQL 非Cristy 产品，避免仅存 PG 的产品（如 id_producto 1677/1678）无法映射
                    _pg_others = self._get_others_products_from_postgres()
                    for _pid, _pinfo in _pg_others:
                        if _pid is None:
                            continue
                        products[_pid] = _pinfo
                        _code = (_pinfo.get('product_code') or '').strip() or str(_pid)
                        if _code:
                            products[_code] = _pinfo
                    # 用全库 products（含 PG 合并）按图片文件名建映射（仅按产品图片名称查找）
                    # CHANGE: 多条产品指向同一图时「不覆盖」，保留第一个，避免名称错位漏洞
                    _image_to_product = {}
                    for _pid, _pinfo in products.items():
                        _img = (_pinfo.get('image_path') or _pinfo.get('ruta_imagen') or '')
                        if not _img:
                            continue
                        _img = (_img if isinstance(_img, str) else str(_img)).strip()
                        if _img and (os.path.sep in _img or '/' in _img or '\\' in _img):
                            _bn_raw = os.path.basename(_img.replace('/', os.path.sep).replace('\\', os.path.sep))
                        else:
                            _bn_raw = _img
                        if not _bn_raw or not _bn_raw.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                            continue
                        _bn_norm = _normalize_image_filename(_bn_raw)
                        _key_norm = _bn_norm.lower()
                        _key_raw = _bn_raw.lower()
                        if _key_norm not in _image_to_product:
                            _image_to_product[_key_norm] = (_pid, _pinfo)
                        if _key_raw not in _image_to_product:
                            _image_to_product[_key_raw] = (_pid, _pinfo)
                    _image_first_others = []
                    _excl_prefixes = getattr(self, 'productos_exclude_file_prefixes', None) or []
                    for _f in _files_ya_subio_no_cristy:
                        _base = os.path.splitext(_f)[0].strip()
                        # CHANGE: 跳过未完成处理的产品（如 importadoraWoni 仅OCR未白底），避免 404 影响旧产品
                        if _excl_prefixes and any(_base.lower().startswith(p) for p in _excl_prefixes):
                            continue
                        _fn_norm = _normalize_image_filename(_f)
                        _pair = None
                        if _fn_norm or _f:
                            _pair = _image_to_product.get((_fn_norm or _f).lower()) or _image_to_product.get(_f.lower())
                        if _pair:
                            _pid, _pinfo = _pair
                            if (_pinfo.get('codigo_proveedor') or '').strip().lower() == 'cristy':
                                _pair = None
                        if _pair:
                            _pid, _pinfo = _pair
                            _created = _pinfo.get('created_at', '')
                            _image_first_others.append((_pid, _pinfo, _created, '/api/images/' + _f, _base))
                        else:
                            _image_first_others.append((_base, {'name': _base, 'product_code': _base, 'price': 0, 'wholesale_price': 0, 'bulk_price': 0, 'description': '', 'created_at': '', 'category_id': 'default', 'channel_username': '', 'codigo_proveedor': ''}, '', '/api/images/' + _f, _base))
                    _image_first_others.sort(key=lambda x: x[2], reverse=True)
                    _total_others = len(_image_first_others)
                    _start = (page - 1) * limit
                    _end = _start + limit
                    _slice = _image_first_others[_start:_end]
                    paginated_products = []
                    for _row in _slice:
                        _pid, _pinfo, _created, _img_path = _row[0], _row[1], _row[2], _row[3]
                        _base_from_image = _row[4] if len(_row) > 4 else (os.path.splitext(_img_path.split('/')[-1])[0] if _img_path else '')
                        _code = (_pinfo.get('product_code') or _pinfo.get('codigo_producto') or _pid)
                        if hasattr(_code, 'strip'):
                            _code = (_code or '').strip()
                        else:
                            _code = str(_code or '').strip()
                        # CHANGE: 其他供应商展示用 codigo_producto（加密产品代码），不用图片文件名
                        _display_code = _code or str(_pid)
                        # CHANGE: 名称必须用 DB 的 nombre_producto/name，禁止用图片文件名
                        _db_name = (_pinfo.get('name', '') or _pinfo.get('nombre_producto', '') or '').strip()
                        _display_name = _db_name if _db_name else (_display_code or '')
                        _pu = float(_pinfo.get('price') or 0)
                        _pm = float(_pinfo.get('wholesale_price') or 0)
                        _pb = float(_pinfo.get('bulk_price') or 0)
                        paginated_products.append({
                            'id': _pid,
                            'product_code': _display_code,
                            'name': _display_name,
                            'price': _pu,
                            'wholesale_price': _pinfo.get('wholesale_price', 0),
                            'bulk_price': _pinfo.get('bulk_price', 0),
                            'description': _pinfo.get('description', ''),
                            'image_path': _img_path,
                            'category': _normalize_multi_category_storage(_pinfo.get('category_id', 'default')),
                            'created_at': _created,
                            'channel_username': _pinfo.get('channel_username', ''),
                            'codigo_proveedor': _pinfo.get('codigo_proveedor', '')
                        })
                    paginated_products = _dedupe_api_rows(paginated_products)
                    logger.info(f"📦 [API] PRODUCTOS 以图为准: 共 {_total_others} 个，本页 {len(paginated_products)} 个，DB图关联数={len(_image_to_product)}")
                    print(f"📦 [API] PRODUCTOS 以图为准: 共 {_total_others} 个，本页 {len(paginated_products)} 个，DB图关联数={len(_image_to_product)}")
                    return finalize_products_payload(_image_first_others, total_hint=_total_others, page_hint=page, limit_hint=limit)
                else:
                    paginated_products = None  # 走下方原有逻辑

                def _file_base_matches_product(base_f, pid):
                    """CHANGE: 仅当图片文件名（base）与产品标识一致时才算匹配，通过图片名字匹配不可能出错。"""
                    if not pid:
                        return False
                    pid_str = (str(pid).strip().lower()).strip()
                    base_s = (base_f if isinstance(base_f, str) else str(base_f)).strip().lower()
                    if not pid_str or not base_s:
                        return False
                    norm_pid = _normalize_base_ai_al(pid_str)
                    norm_base = _normalize_base_ai_al(base_s)
                    return (norm_base == norm_pid or norm_pid in norm_base or norm_base in norm_pid)

                def _message_id_from_name(name):
                    """从 DB 文件名或 product_id 提取 message_id 数字，如 Importadora_Chinito_26820 -> 26820"""
                    if not name:
                        return None
                    s = (name if isinstance(name, str) else str(name)).replace('-', '_').strip().lower()
                    parts = s.split('_')
                    # 原图片文件名格式 msg_{message_id}_{codigo}：取第二段为 message_id
                    if len(parts) >= 2 and parts[0] == 'msg' and parts[1].isdigit() and len(parts[1]) >= 2:
                        return parts[1]
                    for p in reversed(parts):
                        if p.isdigit() and len(p) >= 2:
                            return p
                    return None

                def resolve_image_for_product(pid, img_path):
                    """CHANGE: 严格模式，产品接口阶段只返回能在当前图片索引中确认存在的图片。"""
                    files = _files_for_resolve
                    if not img_path:
                        return ''

                    # 1) 若已是云端 URL，直接返回
                    if isinstance(img_path, str) and (img_path.startswith('http://') or img_path.startswith('https://')):
                        return img_path

                    # 2) 标准化为 /api/images/<filename>
                    if img_path.startswith('/api/images/'):
                        fname = _normalize_image_filename(img_path.replace('/api/images/', '').split('?')[0].strip())
                    else:
                        fname = _normalize_image_filename(os.path.basename(img_path.replace('/', os.sep).replace('\\', os.sep).strip()))

                    if not fname:
                        return ''

                    # 3) 仅做「同文件名 / 同主名不同扩展」确认；找不到就当作无图，直接过滤掉
                    if files:
                        fname_lower = fname.lower()
                        for f in files:
                            if f.lower() == fname_lower:
                                return f'/api/images/{f}'

                        req_base = os.path.splitext(fname)[0].lower()
                        for f in files:
                            if os.path.splitext(f)[0].lower() == req_base:
                                return f'/api/images/{f}'

                    return ''

                def finalize_products_payload(rows, total_hint=None, page_hint=None, limit_hint=None):
                    """统一收口：只保留 Ya Subio 文件夹内确实存在的图片，重算 total 与分页。"""
                    def _is_strict_yasubio_image(path_value):
                        if not path_value:
                            return False
                        if not isinstance(path_value, str):
                            path_value = str(path_value)
                        if path_value.startswith('http://') or path_value.startswith('https://'):
                            return False
                        img_name = ''
                        if '/api/images/' in path_value:
                            img_name = path_value.replace('/api/images/', '').split('?')[0].strip()
                        else:
                            try:
                                img_name = os.path.basename(path_value.replace('/', os.sep).replace('\\', os.sep).strip())
                            except Exception:
                                img_name = ''
                        if not img_name:
                            return False
                        strict_files = [_normalize_image_filename(f).lower() for f in (_files_ya_subio_only or []) if f]
                        img_norm = _normalize_image_filename(img_name).lower()
                        if img_norm in strict_files:
                            return True
                        base_norm = os.path.splitext(img_norm)[0]
                        return any(os.path.splitext(f)[0].lower() == base_norm for f in strict_files)

                    filtered_rows = []
                    seen = set()
                    for row in (rows or []):
                        if not isinstance(row, tuple) or len(row) < 4:
                            continue
                        pid, pinfo, created_at, image_path = row[0], row[1], row[2], row[3]
                        if not isinstance(pinfo, dict):
                            continue
                        key = str(pinfo.get('product_code') or pinfo.get('codigo_producto') or pid or '').strip().lower()
                        if not key:
                            key = str(pid or '').strip().lower()
                        if key and key in seen:
                            continue
                        if key:
                            seen.add(key)
                        if not _is_strict_yasubio_image(image_path):
                            continue
                        strict_img = image_path
                        if isinstance(strict_img, str) and '/api/images/' in strict_img:
                            strict_img = '/api/images/' + _normalize_image_filename(strict_img.replace('/api/images/', '').split('?')[0].strip())
                        filtered_rows.append((pid, pinfo, created_at, strict_img))
                    total_final = len(filtered_rows)
                    page_final = int(page_hint or 1)
                    limit_final = int(limit_hint or len(filtered_rows) or 1)
                    total_pages = (len(filtered_rows) + limit_final - 1) // limit_final if filtered_rows else 1
                    start = max(0, (page_final - 1) * limit_final)
                    end = start + limit_final
                    page_rows = filtered_rows[start:end]
                    product_list = []
                    for product_id, product_info, created_at, image_path in page_rows:
                        _img_basename = ''
                        if image_path and ('/api/images/' in image_path or image_path.startswith('/api/images/')):
                            _img_basename = (image_path.replace('/api/images/', '').split('?')[0].strip() or '')
                        _display_code = (product_info.get('product_code') or product_info.get('codigo_producto') or product_info.get('id') or product_id)
                        if hasattr(_display_code, 'strip'):
                            _display_code = (_display_code or '').strip()
                        else:
                            _display_code = str(_display_code or '').strip()
                        if not _display_code and _img_basename:
                            _display_code = os.path.splitext(_img_basename)[0].strip() or str(product_id)
                        _db_name = (product_info.get('name', '') or product_info.get('nombre_producto', '') or '').strip()
                        _display_name = _db_name if _db_name else (_display_code or str(product_id))
                        _pu = float(product_info.get('price') or 0)
                        product_list.append({
                            'id': product_id,
                            'product_code': _display_code or str(product_id),
                            'name': _display_name,
                            'price': _pu,
                            'wholesale_price': product_info.get('wholesale_price', 0),
                            'bulk_price': product_info.get('bulk_price', 0),
                            'description': product_info.get('description', ''),
                            'image_path': image_path,
                            'category': _normalize_multi_category_storage(product_info.get('category_id', 'default')),
                            'created_at': created_at,
                            'channel_username': product_info.get('channel_username', ''),
                            'codigo_proveedor': product_info.get('codigo_proveedor', '')
                        })
                    product_list = _dedupe_api_rows(product_list)
                    resp = jsonify({
                        "success": True,
                        "data": product_list,
                        "total": len(product_list),
                        "pagination": {
                            "page": page_final,
                            "limit": limit_final,
                            "total": len(product_list),
                            "total_pages": total_pages
                        }
                    })
                    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
                    resp.headers['Pragma'] = 'no-cache'
                    resp.headers['X-Image-Logic'] = 'finalized-products-payload'
                    return resp
                
                # CHANGE: 方案 A - 只显示「在 D:\Ya Subio 有图」的产品；要显示更多产品就把更多已处理图放入 D:\Ya Subio（且文件名能被现有匹配规则识别）
                filtered_with_meta = []
                for product_id, product_info in products_to_process:
                    # category 已在上游优先后端筛过，这里仅作防御性保留
                    if (not force_unfiltered_mode) and category and not _multi_category_contains(product_info.get('category_id'), category):
                        continue
                    # CHANGE: 只搜索 nombre_producto、descripcion、product_code，大小写不敏感，模糊匹配收紧
                    if (not force_unfiltered_mode) and search:
                        q_raw = str(search).strip().lower()
                        keywords = [k.strip() for k in q_raw.split() if k.strip()]
                        if not keywords:
                            continue
                        # 大小写不敏感：统一转小写；CHANGE: 增加 product_code、codigo、product_id 支持产品代码搜索
                        name_s = (product_info.get('name') or product_info.get('nombre_producto') or '').lower()
                        desc_s = (product_info.get('description') or product_info.get('descripcion') or '').lower()
                        code_s = (product_info.get('product_code') or product_info.get('codigo_producto') or product_info.get('codigo') or product_info.get('id') or product_id)
                        code_s = (str(code_s) if code_s is not None else '').strip().lower()
                        pid_s = (str(product_id) if product_id is not None else '').strip().lower()
                        # NOTE: 产品代码精确匹配（忽略大小写）- 搜索词与 product_code 或 product_id 完全一致时直接命中
                        if q_raw == code_s or q_raw == pid_s:
                            all_match = True
                        else:
                            searchable_parts = [name_s, desc_s, code_s, pid_s]
                            searchable_text = ' '.join(p for p in searchable_parts if p)
                            all_match = True
                            for kw in keywords:
                                if kw in searchable_text:
                                    continue
                                # 模糊匹配收紧：相似度 >= 0.85，避免 RADIO 匹配到 ROSADO 等无关词
                                fuzzy_ok = False
                                for part in searchable_parts:
                                    if not part:
                                        continue
                                    for word in re.split(r'[\s\-_.,;:]+', part):
                                        if len(word) < 2:
                                            continue
                                        if difflib.SequenceMatcher(None, kw, word).ratio() >= 0.85:
                                            fuzzy_ok = True
                                            break
                                    if fuzzy_ok:
                                        break
                                if not fuzzy_ok:
                                    all_match = False
                                    break
                        if not all_match:
                            continue
                    created_at = product_info.get('created_at', '')
                    filtered_with_meta.append((product_id, product_info, created_at))
                # 以 DB 产品为主解析图片（与 Telegram 同步方案一致）：只显示「图片在 D:\Ya Subio 内存在」的产品，不按文件夹文件生成占位
                # ULTIMO=Cristy 产品+解析 Cristy 目录图；PRODUCTOS=其他供应商+解析非 Cristy 目录图
                filtered_with_image = []
                for product_id, product_info, created_at in filtered_with_meta:
                    image_path = product_info.get('image_path', '')
                    # CHANGE: 已是云端 URL（PAGES_IMAGE_BASE_URL/R2）时直接使用，不覆盖为 /api/images/，也不走本地 resolve
                    if image_path and (image_path.startswith('http://') or image_path.startswith('https://')):
                        filtered_with_image.append((product_id, product_info, created_at, image_path))
                        continue
                    if image_path:
                        if image_path.startswith('/api/images/'):
                            fname = image_path.replace('/api/images/', '').split('?')[0].strip()
                            image_path = f'/api/images/{_normalize_image_filename(fname)}'
                        elif '/pwa_cart/static/img/' in image_path or image_path.startswith('/pwa_cart/static/img/'):
                            filename = _normalize_image_filename(os.path.basename(image_path))
                            image_path = f'/api/images/{filename}'
                        elif image_path.startswith('/img/') or '/img/' in image_path:
                            filename = _normalize_image_filename(os.path.basename(image_path))
                            image_path = f'/api/images/{filename}'
                    elif os.path.isabs(image_path) or (image_path and ('D:' in image_path or 'C:' in image_path)):
                        normalized_path = image_path.replace('/', os.sep).replace('\\', os.sep)
                        filename = _normalize_image_filename(os.path.basename(normalized_path))
                        image_path = f'/api/images/{filename}'
                    elif image_path and ('\\' in image_path or '/' in image_path):
                        normalized_path = image_path.replace('/', os.sep).replace('\\', os.sep)
                        filename = _normalize_image_filename(os.path.basename(normalized_path))
                        image_path = f'/api/images/{filename}'
                    elif image_path and not image_path.startswith('http'):
                        image_path = f'/api/images/{_normalize_image_filename(image_path)}'
                    pages_base = getattr(self, 'pages_image_base_url', None) or (os.getenv('PAGES_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
                    if pages_base:
                        _img = product_info.get('ruta_imagen_raw') or product_info.get('image_path') or product_info.get('ruta_imagen') or image_path or ''
                        if _img and isinstance(_img, str):
                            _norm = _img.replace('\\', '/').strip()
                            if _img.startswith('/api/images/'):
                                _rel = _normalize_image_filename(_img.replace('/api/images/', '').split('?')[0].strip())
                            elif 'output_images' in _norm.lower() or 'product_images' in _norm.lower():
                                # 保留相对路径，如 .../output_images/Importadora_Chinito/xxx.jpg -> Importadora_Chinito/xxx.jpg
                                _lower = _norm.lower()
                                for _key in ('output_images/', 'product_images/'):
                                    if _key in _lower:
                                        _rel = _norm[_lower.index(_key) + len(_key):].replace(' ', '%20')
                                        _rel = _normalize_image_filename(_rel)
                                        break
                                else:
                                    _rel = _normalize_image_filename(os.path.basename(_norm))
                            else:
                                _rel = _normalize_image_filename(os.path.basename(_norm))
                            if _rel and _rel.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                                _sub = 'Cristy/' if (product_info.get('codigo_proveedor') or '').strip().lower() == 'cristy' else ''
                                resolved_pages = pages_base + '/Ya%20Subio/' + _sub + _rel
                                filtered_with_image.append((product_id, product_info, created_at, resolved_pages))
                                continue
                    resolved = resolve_image_for_product(product_id, image_path)
                    # CHANGE: 全量模式下不过滤图片存在性，确保前台返回全部 DB 记录。
                    # 非全量模式保持原逻辑（必须在 Ya Subio 找到图才返回）。
                    if not resolved and not full_db_mode:
                        continue  # 图片不在 D:\Ya Subio 内（含子目录），不显示该产品
                    final_image = resolved or image_path or ''
                    filtered_with_image.append((product_id, product_info, created_at, final_image))
                filtered_with_image.sort(key=lambda x: x[2], reverse=True)
                total_filtered = len(filtered_with_image)
                start = (page - 1) * limit
                end = start + limit
                page_slice = filtered_with_image[start:end]
                product_list = []
                for product_id, product_info, created_at, image_path in page_slice:
                    # CHANGE: 始终优先用 product_code/codigo_producto，搜索时禁止用图片文件名作为展示码或名称
                    _img_basename = ''
                    if image_path and ('/api/images/' in image_path or image_path.startswith('/api/images/')):
                        _img_basename = (image_path.replace('/api/images/', '').split('?')[0].strip() or '')
                    _display_code = (product_info.get('product_code') or product_info.get('codigo_producto') or product_info.get('id') or product_id)
                    if hasattr(_display_code, 'strip'):
                        _display_code = (_display_code or '').strip()
                    else:
                        _display_code = str(_display_code or '').strip()
                    # 仅当 product_code 为空时（如 Cristy 以图名为准）才用图片文件名
                    if not _display_code and _img_basename:
                        _display_code = os.path.splitext(_img_basename)[0].strip() or str(product_id)
                    # CHANGE: 名称必须用 DB 的 nombre_producto/name，禁止用图片文件名（避免搜索显示 Importadora_Chinito 等）
                    _db_name = (product_info.get('name', '') or product_info.get('nombre_producto', '') or '').strip()
                    _display_name = _db_name if _db_name else (_display_code or str(product_id))
                    # CHANGE: price 必须为单价(precio_unidad)，供弹窗/加购按数量 1-2 单价/3-11 批发/12+ 批量 正确计算；目录展示价由前端用 price/wholesale/bulk 计算
                    _pu = float(product_info.get('price') or 0)
                    _pm = float(product_info.get('wholesale_price') or 0)
                    _pb = float(product_info.get('bulk_price') or 0)
                    product_list.append({
                        'id': product_id,
                        'product_code': _display_code or str(product_id),
                        'name': _display_name,
                        'price': _pu,
                        'wholesale_price': product_info.get('wholesale_price', 0),
                        'bulk_price': product_info.get('bulk_price', 0),
                        'description': product_info.get('description', ''),
                        'image_path': image_path,
                        'category': _normalize_multi_category_storage(product_info.get('category_id', 'default')),
                        'created_at': created_at,
                        'channel_username': product_info.get('channel_username', ''),
                        'codigo_proveedor': product_info.get('codigo_proveedor', '')
                    })
                
                # product_list 已是当前页，total 用 total_filtered
                paginated_products = _dedupe_api_rows(product_list)
                # CHANGE: 调试图片不显示 - 打印前几条的 image_path
                with_img = sum(1 for p in paginated_products if p.get('image_path'))
                logger.info(f"📦 [API] 本页有图产品数: {with_img}/{len(paginated_products)}")
                print(f"📦 [API] 本页有图产品数: {with_img}/{len(paginated_products)}")
                for i, p in enumerate(paginated_products[:5]):
                    ip = p.get('image_path', '')
                    nm = (p.get('name') or '')[:50]
                    logger.info(f"  产品[{i}] id={p.get('id')} name={nm} price={p.get('price')} image_path={ip[:80] if ip else '(empty)'}")
                    print(f"  产品[{i}] id={p.get('id')} name={nm} price={p.get('price')} image_path={ip[:80] if ip else '(empty)'}")
                
                logger.info(f"📦 [API] 最终返回: {len(paginated_products)} 个产品（第 {page} 页，共 {total_filtered} 个）")
                print(f"📦 [API] 最终返回: {len(paginated_products)} 个产品（第 {page} 页，共 {total_filtered} 个）")
                if search and total_filtered == 0:
                    logger.info(f"🔍 [API] 搜索无结果: 关键词={search!r}, 扫描产品={len(products_to_process)}, 文本匹配={len(filtered_with_meta)}, 有图产品=0")
                    print(f"🔍 [API] 搜索无结果: 关键词={search!r}, 扫描产品={len(products_to_process)}, 文本匹配={len(filtered_with_meta)}, 有图产品=0")
                
                return finalize_products_payload(filtered_with_image, total_hint=total_filtered, page_hint=page, limit_hint=limit)
                
            except Exception as e:
                logger.error(f"❌ 获取产品列表失败: {e}")
                print(f"❌ [API] 获取产品列表失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/products/<product_id>', methods=['GET'])
        def get_product(product_id):
            """获取产品详情（SQLite + PostgreSQL Cristy 回退）。CHANGE: 支持 10060_Al/10060_A 等 URL 与 DB 10060/10060._AI 多候选匹配；支持 Telegram 展示码 18bf4405 通过映射解析。"""
            try:
                if not self.db and USE_SQLITE_FOR_PRODUCTS:
                    return jsonify({"error": "Base de datos no conectada"}), 500
                # CHANGE: 保留 URL 中的 id，响应时返回此值以便前端 #/product/18bf4405 能匹配卡片
                requested_id = product_id
                mapping = _load_display_code_mapping()
                if mapping.get(requested_id):
                    product_id = mapping[requested_id]
                
                # 暂时註销 SQLite 时产品仅从 PG 取
                if USE_SQLITE_FOR_PRODUCTS and self.db:
                    products = self.db.get_all_products()
                else:
                    products = self._get_products_dict_from_postgres()
                product = products.get(product_id)
                resolved_id = product_id
                # CHANGE: Cristy 产品可能在 PostgreSQL，列表有但详情仅查了 SQLite，此处回退到 PG 查询
                if not product:
                    product = self._get_single_product_from_postgres(product_id)
                # CHANGE: URL 可能为 10060_Al/10060_A，DB 存 10060 或 10060._AI，用候选 key 再查
                if not product:
                    for cand in _product_id_candidates(product_id):
                        if cand == product_id:
                            continue
                        product = products.get(cand)
                        if product:
                            resolved_id = cand
                            break
                        if not product:
                            product = self._get_single_product_from_postgres(cand)
                            if product:
                                resolved_id = cand
                                break
                # CHANGE: 仍未找到则从 PostgreSQL 按 id/codigo 查任意供应商（含 1677/1678 等仅存 PG 的产品）
                if not product:
                    product = self._get_single_product_from_postgres_any(product_id)
                    if product:
                        resolved_id = product_id
                    if not product:
                        for cand in _product_id_candidates(product_id):
                            product = self._get_single_product_from_postgres_any(cand)
                            if product:
                                resolved_id = cand
                                break
                # CHANGE: 购物车商品可能仅存于 SQLite（如 24AE0289/XE868/XEO3），PG 无则回退 SQLite 供购物车页补全
                if not product and self.db:
                    try:
                        sqlite_products = self.db.get_all_products()
                        product = sqlite_products.get(requested_id) or sqlite_products.get(product_id)
                        if not product and requested_id:
                            for k, v in sqlite_products.items():
                                if str(k) == str(requested_id) or str(k) == str(product_id):
                                    product = v
                                    break
                        if product:
                            resolved_id = requested_id
                            # 转为与 PG 一致的结构（id/name/price/image_path 等）
                            _img = product.get('image_path') or product.get('ruta_imagen') or ''
                            if _img and (_img.startswith('D:') or '\\' in _img or '/' in _img):
                                _img = '/api/images/' + os.path.basename(str(_img).replace('/', os.sep))
                            product = {
                                'id': product.get('id', requested_id),
                                'name': product.get('name', f'Producto {requested_id}'),
                                'price': float(product.get('price', 0)),
                                'wholesale_price': float(product.get('wholesale_price', 0)),
                                'bulk_price': float(product.get('bulk_price', 0)),
                                'description': product.get('description', ''),
                                'category_id': product.get('category_id', 'default'),
                                'image_path': _img,
                                'product_code': product.get('id', requested_id),
                                'codigo_proveedor': product.get('codigo_proveedor', ''),
                            }
                    except Exception as e:
                        logger.debug(f"SQLite 产品回退失败: {requested_id}, {e}")
                if not product:
                    return jsonify({"error": "El producto no existe"}), 404
                
                # CHANGE: 转换图片路径为URL - 处理所有可能的路径格式；统一去掉文件名方括号与 D:\Ya Subio 实际文件名一致
                image_path = product.get('image_path', '')
                # 已是云端 URL 时直接使用，不再转为 /api/images/
                if image_path and (image_path.startswith('http://') or image_path.startswith('https://')):
                    pass  # 保持 image_path 不变，跳过下方本地路径逻辑
                elif image_path:
                    if image_path.startswith('/api/images/'):
                        fname = image_path.replace('/api/images/', '').split('?')[0].strip()
                        image_path = f'/api/images/{_normalize_image_filename(fname)}'
                    elif '/pwa_cart/static/img/' in image_path or image_path.startswith('/pwa_cart/static/img/'):
                        filename = _normalize_image_filename(os.path.basename(image_path))
                        image_path = f'/api/images/{filename}'
                    elif image_path.startswith('/img/') or '/img/' in image_path:
                        filename = _normalize_image_filename(os.path.basename(image_path))
                        image_path = f'/api/images/{filename}'
                    elif os.path.isabs(image_path):
                        filename = _normalize_image_filename(os.path.basename(image_path))
                        image_path = f'/api/images/{filename}'
                    elif '\\' in image_path or '/' in image_path:
                        filename = _normalize_image_filename(os.path.basename(image_path))
                        image_path = f'/api/images/{filename}'
                    elif image_path and not image_path.startswith('http'):
                        image_path = f'/api/images/{_normalize_image_filename(image_path)}'
                # CHANGE: 若配置 PAGES_IMAGE_BASE_URL，则按 DB 路径直接拼 Pages 图片 URL（避免 Telegram 直达缺图）
                pages_base = getattr(self, 'pages_image_base_url', None) or (os.getenv('PAGES_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
                if pages_base and not (image_path and (image_path.startswith('http://') or image_path.startswith('https://'))):
                    _img = (product.get('ruta_imagen_raw') or product.get('ruta_imagen') or product.get('image_path') or image_path or '')
                    if _img and isinstance(_img, str):
                        _norm = _img.replace('\\', '/').strip()
                        if _img.startswith('/api/images/'):
                            _rel = _normalize_image_filename(_img.replace('/api/images/', '').split('?')[0].strip())
                        elif 'output_images' in _norm.lower() or 'product_images' in _norm.lower():
                            _lower = _norm.lower()
                            for _key in ('output_images/', 'product_images/'):
                                if _key in _lower:
                                    _rel = _norm[_lower.index(_key) + len(_key):].replace(' ', '%20')
                                    _rel = _normalize_image_filename(_rel)
                                    break
                            else:
                                _rel = _normalize_image_filename(os.path.basename(_norm))
                        else:
                            _rel = _normalize_image_filename(os.path.basename(_norm))
                        if _rel and _rel.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                            _sub = 'Cristy/' if (product.get('codigo_proveedor') or '').strip().lower() == 'cristy' else ''
                            image_path = pages_base + '/Ya%20Subio/' + _sub + _rel
                # CHANGE: 与列表一致，单一逻辑「根据图片名称查找」+ product_id；已是 http(s) 时不走本地目录
                _ya = PWA_YA_SUBIO_BASE
                if not (image_path and (image_path.startswith('http://') or image_path.startswith('https://'))) and os.path.isdir(_ya):
                    try:
                        import re
                        files = []
                        for _f in os.listdir(_ya):
                            try:
                                if os.path.isfile(os.path.join(_ya, _f)):
                                    files.append(_f)
                            except OSError:
                                continue
                        fname = (image_path.replace('/api/images/', '').split('?')[0].strip() if (image_path and image_path.startswith('/api/images/'))
                                else (os.path.basename(image_path.replace('/', os.sep).replace('\\', os.sep).strip()) if image_path else ''))
                        # 1) 精确文件名 2) 同主名不同扩展名
                        if fname and fname in files:
                            image_path = f'/api/images/{fname}'
                        elif fname:
                            name_no_ext = os.path.splitext(fname)[0]
                            for f in files:
                                if os.path.splitext(f)[0].lower() == name_no_ext.lower():
                                    image_path = f'/api/images/{f}'
                                    break
                        # 3) 按图片名称：数字/关键词在目录中匹配
                        if fname and not (image_path and image_path.startswith('/api/images/') and os.path.isfile(os.path.join(_ya, image_path.replace('/api/images/', '').split('?')[0].strip()))):
                            name_no_ext = os.path.splitext(fname)[0]
                            nums = re.findall(r'\d+', name_no_ext)
                            parts = [p for p in re.split(r'[_\-.\s]+', name_no_ext) if len(p) >= 2 and not p.isdigit()]
                            for n in sorted(nums, key=len, reverse=True):
                                if len(n) >= 3:
                                    for f in files:
                                        if n in f:
                                            image_path = f'/api/images/{f}'
                                            break
                                    if image_path and image_path.startswith('/api/images/'):
                                        break
                            if not (image_path and image_path.startswith('/api/images/')):
                                for p in parts:
                                    if len(p) >= 3:
                                        for f in files:
                                            if p.lower() in f.lower():
                                                image_path = f'/api/images/{f}'
                                                break
                                        if image_path and image_path.startswith('/api/images/'):
                                            break
                        # 4) 按 product_id（使用 resolved_id 与 DB/codigo 一致，便于匹配 10060._AI.jpg）
                        if not (image_path and image_path.startswith('/api/images/') and os.path.isfile(os.path.join(_ya, image_path.replace('/api/images/', '').split('?')[0].strip()))):
                            pid_str = str(resolved_id).strip()
                            for ext in ('.jpg', '._AI.jpg', '.jpeg', '.png'):
                                if (pid_str + ext) in files:
                                    image_path = f'/api/images/{pid_str}{ext}'
                                    break
                            else:
                                for f in files:
                                    base, _ = os.path.splitext(f)
                                    if base == pid_str or base.startswith(pid_str + '_') or base.startswith(pid_str + '.'):
                                        image_path = f'/api/images/{f}'
                                        break
                                else:
                                    for f in files:
                                        if pid_str.lower() in f.lower():
                                            image_path = f'/api/images/{f}'
                                            break
                    except Exception:
                        pass
                
                # CHANGE: 返回 requested_id 与 codigo_proveedor，供前端判断是否供应商（显示在 PRODUCTOS 而非 ULTIMO）
                return jsonify({
                    "success": True,
                    "data": {
                        'id': requested_id,
                        'name': product.get('name', ''),
                        'price': product.get('price', 0),
                        'wholesale_price': product.get('wholesale_price', 0),
                        'bulk_price': product.get('bulk_price', 0),
                        'description': product.get('description', ''),
                        'image_path': image_path,
                        'category': _normalize_multi_category_storage(product.get('category_id', 'default')),
                        'product_code': product.get('product_code', ''),
                        'codigo_proveedor': (product.get('codigo_proveedor') or '').strip()
                    }
                })
            
            except Exception as e:
                logger.error(f"❌ 获取产品详情失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart', methods=['GET'])
        def get_cart():
            """获取购物车。仅信任JWT中的user_id，未登录返回空购物车，避免多用户串车。"""
            try:
                # CHANGE: 仅从认证token获取user_id，不接受 query 中的 user_id（防止未登录或伪造看到他人购物车）
                user_id = None
                if hasattr(request, 'user_id') and getattr(request, 'user_id', None):
                    user_id = getattr(request, 'user_id', None)
                    logger.info(f"📥 API获取购物车请求: 从token获取user_id={user_id}")
                # CHANGE: 允许 guest_id（负整数），仅 user_id 为 None 或 0 时返回空购物车
                if user_id is None or user_id == 0:
                    logger.info("📥 API获取购物车请求: 无user_id/session_id，返回空购物车")
                    return jsonify({
                        "success": True,
                        "data": []
                    })
                
                if not self.cart_manager:
                    logger.error("❌ 购物车管理器未可用")
                    return jsonify({"error": "Gestor del carrito no disponible"}), 500
                
                logger.info(f"📥 API获取购物车请求: user_id={user_id}")
                logger.info(f"📥 CartManager实例: {self.cart_manager}")
                logger.info(f"📥 CartManager.db实例: {self.cart_manager.db if self.cart_manager else 'N/A'}")
                logger.info(f"📥 DatabaseManager实例: {self.db}")
                if self.db:
                    logger.info(f"📥 DatabaseManager数据库路径: {self.db.db_path}")
                    logger.info(f"📥 数据库文件存在: {os.path.exists(self.db.db_path)}")
                if self.cart_manager and self.cart_manager.db:
                    logger.info(f"📥 CartManager.db数据库路径: {self.cart_manager.db.db_path}")
                    logger.info(f"📥 是否是同一个实例: {self.db is self.cart_manager.db}")
                
                # 直接查询数据库验证（在调用get_user_cart之前）
                if self.db:
                    import sqlite3
                    try:
                        conn = sqlite3.connect(self.db.db_path)
                        cursor = conn.cursor()
                        cursor.execute('SELECT COUNT(*) FROM user_carts WHERE user_id = ?', (user_id,))
                        db_count_before = cursor.fetchone()[0]
                        logger.info(f"📊 调用get_user_cart之前，数据库记录数: {db_count_before}")
                        if db_count_before > 0:
                            cursor.execute('SELECT product_id, quantity FROM user_carts WHERE user_id = ?', (user_id,))
                            db_rows_before = cursor.fetchall()
                            logger.info(f"📊 数据库记录: {db_rows_before}")
                        conn.close()
                    except Exception as e:
                        logger.error(f"❌ 数据库验证失败: {e}")
                
                cart = self.cart_manager.get_user_cart(user_id)
                logger.info(f"🛒 获取购物车: user_id={user_id}, 商品数={len(cart)}")
                # CHANGE: 用 Neon（PG）补全其他供应商的 name/code/price，与 sync/orders、checkout 一致；云端 SQLite 无产品时必走此处
                def _is_placeholder_name(n):
                    if not n or not str(n).strip():
                        return True
                    u = (str(n).strip()).upper()
                    if u in ('NAN', 'NONE', 'NULL') or u == 'PRODUCTO' or u == 'PRODUCTO NUEVO':
                        return True
                    if u.startswith('PRODUCTO ') and len(u) > 9:
                        return True
                    return False
                if cart:
                    # NOTE: 此日志用于确认 Render 已部署到含 Neon 补全的版本；若无此条则仍在跑旧代码
                    logger.info("📋 [GET /api/cart] 购物车有 %d 项，开始用 Neon(PG) 补全 name/code/price", len(cart))
                    try:
                        pg_ok = self._get_pg_config() is not None
                        logger.info("📋 [GET /api/cart] DATABASE_URL=%s", "已配置" if pg_ok else "未配置")
                        if not pg_ok:
                            logger.warning("⚠️ [GET /api/cart] DATABASE_URL 未配置，无法从 Neon 补全 name/code，请到 Render 环境变量设置 DATABASE_URL（Neon 连接串）")
                        filled = 0
                        for it in cart:
                            pid = str(it.get('product_id') or it.get('code') or '').strip()
                            if not pid:
                                continue
                            name = str(it.get('name') or '').strip()
                            code = str(it.get('code') or pid).strip()
                            if not _is_placeholder_name(name) and code != pid:
                                continue
                            pg_prod = self._get_single_product_from_postgres_any(pid)
                            if not pg_prod:
                                continue
                            pg_name = (pg_prod.get('name') or '').strip()
                            pg_code = (pg_prod.get('product_code') or pg_prod.get('id') or '').strip()
                            if pg_name:
                                it['name'] = pg_name
                            if pg_code:
                                it['code'] = pg_code
                            # CHANGE: 同时补全 price，否则云端 SQLite 无产品时 GET /api/cart 一直返回 price:0.0
                            qty = float(it.get('quantity') or 0)
                            if qty <= 0:
                                qty = 1.0
                            pu = float(pg_prod.get('price') or pg_prod.get('precio_unidad') or 0)
                            pm = float(pg_prod.get('wholesale_price') or pg_prod.get('precio_mayor') or 0)
                            pb = float(pg_prod.get('bulk_price') or pg_prod.get('precio_bulto') or 0)
                            if pu <= 0:
                                pu = pm if pm > 0 else pb
                            if pm <= 0:
                                pm = pu
                            if pb <= 0:
                                pb = pm
                            if qty >= 12 and pb > 0:
                                it['price'] = pb
                            elif qty >= 3 and pm > 0:
                                it['price'] = pm
                            elif pu > 0:
                                it['price'] = pu
                            if pg_name or pg_code:
                                filled += 1
                                logger.info("📋 [GET /api/cart] Neon 补全: product_id=%s -> code=%s, name=%s, price=%s", pid, pg_code or pid, (pg_name or "")[:50], it.get('price'))
                        if filled:
                            logger.info("📋 [GET /api/cart] 共 %d 项已用 Neon(PG) 补全 name/code", filled)
                    except Exception as e:
                        logger.warning("⚠️ [GET /api/cart] 用 PG 补全 name/code 失败: %s", e)
                    logger.info(f"🛒 购物车内容: {[item.get('product_id') for item in cart]}")
                else:
                    logger.warning(f"⚠️ 购物车为空: user_id={user_id}")
                    # 直接查询数据库验证（在调用get_user_cart之后）
                    if self.db:
                        import sqlite3
                        try:
                            conn = sqlite3.connect(self.db.db_path)
                            cursor = conn.cursor()
                            cursor.execute('SELECT COUNT(*) FROM user_carts WHERE user_id = ?', (user_id,))
                            db_count_after = cursor.fetchone()[0]
                            logger.warning(f"⚠️ 数据库验证: user_carts表中有 {db_count_after} 条记录，但API返回空数组！")
                            if db_count_after > 0:
                                cursor.execute('SELECT product_id, quantity FROM user_carts WHERE user_id = ?', (user_id,))
                                db_rows_after = cursor.fetchall()
                                logger.warning(f"⚠️ 数据库记录: {db_rows_after}")
                            conn.close()
                        except Exception as e:
                            logger.error(f"❌ 数据库验证失败: {e}")
                
                return jsonify({
                    "success": True,
                    "data": cart
                })
                
            except Exception as e:
                logger.error(f"❌ 获取购物车失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/add', methods=['POST'])
        def add_to_cart():
            """添加商品到购物车"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "El cuerpo de la solicitud está vacío"}), 400
                
                # CHANGE: 支持 guest_id（session_id 转为负整数），无 session 时禁止操作
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                if user_id is None or user_id == 0:
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                
                product_id = data.get('product_id')
                quantity = data.get('quantity', 1)
                # CHANGE: 接受前端传入的单价（购物车页已按数量层级算好），后端直接采用不重算，保证“其他位置只读结果就一致”
                unit_price = data.get('price')
                if unit_price is not None:
                    try:
                        unit_price = float(unit_price)
                    except (ValueError, TypeError):
                        unit_price = None
                if unit_price is not None and unit_price > 0:
                    logger.info(f"🛒 API使用前端传入单价: {unit_price}")
                
                if not product_id:
                    return jsonify({"error": "Faltan parámetros obligatorios"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "Gestor del carrito no disponible"}), 500
                
                logger.info(f"🛒 API添加产品到购物车: user_id={user_id}, product_id={product_id}, quantity={quantity}, unit_price={unit_price}")
                logger.info(f"🛒 CartManager实例: {self.cart_manager}")
                logger.info(f"🛒 CartManager类型: {type(self.cart_manager)}")
                
                success = self.cart_manager.add_to_cart(user_id, product_id, quantity, unit_price=unit_price)
                logger.info(f"🛒 add_to_cart返回结果: {success}")
                
                if success:
                    # 返回更新后的购物车数据
                    cart = self.cart_manager.get_user_cart(user_id)
                    cart_count = sum(item.get('quantity', 0) for item in cart)
                    logger.info(f"✅ 成功添加，购物车现在有 {len(cart)} 个商品，总数量: {cart_count}")
                    if cart:
                        logger.info(f"✅ 购物车内容: {[item.get('product_id') for item in cart]}")
                    else:
                        logger.warning(f"⚠️ 购物车为空，但add_to_cart返回成功！")
                    return jsonify({
                        "success": True,
                        "message": "商品已添加到购物车",
                        "cart_count": cart_count,
                        "cart_items": len(cart)
                    })
                else:
                    logger.error(f"❌ 添加失败: user_id={user_id}, product_id={product_id}")
                    return jsonify({"error": "Error al añadir. Compruebe que el ID del producto sea correcto"}), 500
                
            except Exception as e:
                logger.error(f"❌ 添加到购物车失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/update', methods=['POST'])
        def update_cart():
            """更新购物车商品数量。仅信任JWT。"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "El cuerpo de la solicitud está vacío"}), 400
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                if user_id is None or user_id == 0:
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                product_id = data.get('product_id')
                quantity = data.get('quantity')
                unit_price = data.get('price')
                if unit_price is not None:
                    try:
                        unit_price = float(unit_price)
                    except (ValueError, TypeError):
                        unit_price = None
                
                if not product_id or quantity is None:
                    return jsonify({"error": "Faltan parámetros obligatorios"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "Gestor del carrito no disponible"}), 500
                
                success = self.cart_manager.update_quantity(user_id, product_id, quantity, unit_price=unit_price)
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": "Carrito actualizado"
                    })
                else:
                    return jsonify({"error": "Error al actualizar"}), 500
                
            except Exception as e:
                logger.error(f"❌ 更新购物车失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/remove', methods=['POST'])
        def remove_from_cart():
            """从购物车移除商品。仅信任JWT。"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "El cuerpo de la solicitud está vacío"}), 400
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                if user_id is None or user_id == 0:
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                product_id = data.get('product_id')
                if not product_id:
                    return jsonify({"error": "Faltan parámetros obligatorios"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "Gestor del carrito no disponible"}), 500
                
                success = self.cart_manager.remove_from_cart(user_id, product_id)
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": "商品已从购物车移除"
                    })
                else:
                    return jsonify({"error": "Error al eliminar"}), 500
                
            except Exception as e:
                logger.error(f"❌ 从购物车移除失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/clear', methods=['POST'])
        def clear_cart():
            """清空购物车。仅信任JWT。"""
            try:
                data = request.get_json() or {}
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                if user_id is None or user_id == 0:
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "Gestor del carrito no disponible"}), 500
                
                # 保存空购物车
                self.cart_manager.save_user_cart(user_id, [])
                
                return jsonify({
                    "success": True,
                    "message": "购物车已清空"
                })
                
            except Exception as e:
                logger.error(f"❌ 清空购物车失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/total', methods=['GET'])
        def get_cart_total():
            """计算购物车总价。仅信任JWT。"""
            try:
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                if user_id is None or user_id == 0:
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "Gestor del carrito no disponible"}), 500
                
                total = self.cart_manager.get_cart_total(user_id)
                
                return jsonify({
                    "success": True,
                    "data": {
                        "total": total
                    }
                })
                
            except Exception as e:
                logger.error(f"❌ 计算购物车总价失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/checkout', methods=['POST'])
        def checkout():
            """提交订单"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "El cuerpo de la solicitud está vacío"}), 400
                # CHANGE: 便于确认前端是否发送 subtotal/total（PEDIDOS=CARRITO）
                logger.info(f"📦 [checkout] 请求体含 subtotal={data.get('subtotal')}, total={data.get('total')}")
                print(f"📦 [checkout] 请求体含 subtotal={data.get('subtotal')}, total={data.get('total')}")
                
                # CHANGE: 仅从认证token获取user_id，未登录禁止下单
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                customer_info = data.get('customer_info', {})  # CHANGE: 获取客户信息
                logger.info(f"📦 收到订单提交请求: user_id={user_id}, type={type(user_id)}")
                logger.info(f"👤 客户信息: {json.dumps(customer_info, ensure_ascii=False) if customer_info else '无'}")
                if user_id is None or user_id == 0:
                    logger.error("❌ 未登录无法提交订单")
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                # CHANGE: 验证客户信息
                if not customer_info:
                    logger.error("❌ 缺少客户信息")
                    return jsonify({"error": "Faltan datos del cliente"}), 400
                
                required_fields = ['cedula', 'nombres', 'direccion', 'provincia', 'ciudad', 'whatsapp']
                for field in required_fields:
                    if not customer_info.get(field):
                        logger.error(f"❌ 客户信息缺少必填字段: {field}")
                        return jsonify({"error": f"Datos del cliente: falta el campo obligatorio: {field}"}), 400
                
                # 确保user_id是整数类型
                try:
                    user_id = int(user_id)
                except (ValueError, TypeError) as e:
                    logger.error(f"❌ user_id类型转换失败: {user_id}, error={e}")
                    return jsonify({"error": f"user_id debe ser un número entero: {user_id}"}), 400
                
                if not self.cart_manager or not self.db:
                    logger.error("❌ 服务未可用: cart_manager={}, db={}".format(
                        self.cart_manager is not None, self.db is not None))
                    return jsonify({"error": "Servicio no disponible"}), 500
                
                # 获取购物车
                cart = self.cart_manager.get_user_cart(user_id)
                logger.info(f"🛒 获取购物车: user_id={user_id}, 商品数={len(cart) if cart else 0}")
                
                if not cart or len(cart) == 0:
                    logger.warning(f"⚠️ 购物车是空的: user_id={user_id}")
                    return jsonify({
                        "success": False,
                        "error": "El carrito está vacío. Añada productos primero",
                        "error_type": "EmptyCart"
                    }), 400
                
                # CHANGE: 验证购物车数据格式，确保数据完整性和正确性
                logger.info(f"📋 购物车数据: {json.dumps(cart, ensure_ascii=False, indent=2)}")
                validated_cart = []
                for idx, item in enumerate(cart):
                    # CHANGE: 验证必需字段
                    if not isinstance(item, dict):
                        logger.error(f"❌ 购物车商品 {idx} 不是字典类型: {type(item)}")
                        return jsonify({
                            "success": False,
                            "error": f"购物车商品格式错误: 商品 {idx + 1} 不是有效的数据格式",
                            "error_type": "InvalidCartItem"
                        }), 400
                    
                    # CHANGE: 检查必需字段
                    required_fields = ['product_id', 'price', 'quantity']
                    missing_fields = [field for field in required_fields if field not in item]
                    if missing_fields:
                        logger.error(f"❌ 购物车商品 {idx} 缺少必需字段: {missing_fields}, 商品={item}")
                        return jsonify({
                            "success": False,
                            "error": f"购物车商品格式错误: 商品 {idx + 1} 缺少必需字段: {', '.join(missing_fields)}",
                            "error_type": "MissingFields"
                        }), 400
                    
                    # CHANGE: 验证数据类型和值
                    try:
                        product_id = str(item['product_id']).strip()
                        if not product_id:
                            raise ValueError("product_id不能为空")
                        
                        quantity = float(item['quantity'])
                        if quantity <= 0:
                            raise ValueError(f"quantity必须大于0: {quantity}")
                        
                        price = float(item['price'])
                        if price < 0:
                            raise ValueError(f"price不能为负数: {price}")
                        
                        # CHANGE: 获取产品名称（如果没有则使用product_id）
                        product_name = item.get('name', '').strip()
                        if not product_name:
                            product_name = product_id
                        
                        # CHANGE: 构建验证后的商品数据；保留 code（展示用产品代码，如 Y99）以便订单 JSON 与 Sistema Factura 一致
                        validated_item = {
                            'product_id': product_id,
                            'name': product_name,
                            'quantity': quantity,
                            'price': price
                        }
                        if item.get('code'):
                            validated_item['code'] = str(item.get('code', '')).strip()
                        validated_cart.append(validated_item)
                        logger.debug(f"  ✅ 商品 {idx + 1} 验证通过: {product_id}, quantity={quantity}, price={price}")
                        
                    except (ValueError, TypeError) as e:
                        logger.error(f"❌ 购物车商品 {idx} 数据验证失败: {e}, 商品={item}")
                        return jsonify({
                            "success": False,
                            "error": f"购物车商品数据错误: 商品 {idx + 1} - {str(e)}",
                            "error_type": "InvalidData"
                        }), 400
                
                # CHANGE: 使用验证后的购物车数据
                cart = validated_cart
                logger.info(f"✅ 购物车验证通过: {len(cart)} 个商品")
                
                # CHANGE: 用与前端一致的数据源补全 code/name——前端 ULTIMO 来自 PostgreSQL，订单保存若只用 SQLite 会得到过期的「Producto nuevo」
                try:
                    pg_list = self._get_ultimo_products_from_postgres()
                    if pg_list:
                        pg_map = {}
                        for pid, pinfo in pg_list:
                            k = str(pid)
                            code = (pinfo.get('product_code') or pinfo.get('id') or k).strip()
                            name = (pinfo.get('name') or '').strip()
                            if code or name:
                                pg_map[k] = {'code': code or k, 'name': name or code or k}
                        if pg_map:
                            for item in cart:
                                pid = str(item.get('product_id', '')).strip()
                                res = pg_map.get(pid)
                                if not res and pid:
                                    nums = re.findall(r'\d+', pid)
                                    for n in reversed(nums):
                                        if pg_map.get(n):
                                            res = pg_map[n]
                                            break
                                if res:
                                    item['code'] = res['code']
                                    item['name'] = res['name']
                                    logger.debug(f"  📦 订单商品补全自 PG: product_id={pid} -> code={res['code']}, name={res['name'][:40]}")
                except Exception as e:
                    logger.warning(f"⚠️ 用 PG 补全订单商品名失败（继续用现有数据）: {e}")
                
                # CHANGE: PRODUCTOS 页商品可能不在 ULTIMO 列表中，用 PG(any) 按 product_id 补全 name/code/price，避免显示「PRODUCTO XXX」和 0.00
                def _is_placeholder_name(n):
                    if not n or not (n or '').strip():
                        return True
                    u = (n or '').strip().upper()
                    if u.startswith('PRODUCTO ') and len(n) > 9:
                        return True
                    return False
                try:
                    for item in cart:
                        pid = str(item.get('product_id', '')).strip()
                        if not pid:
                            continue
                        need_fill = _is_placeholder_name(item.get('name') or '') or float(item.get('price') or 0) <= 0
                        if not need_fill:
                            continue
                        pg_prod = self._get_single_product_from_postgres_any(pid)
                        if not pg_prod:
                            continue
                        name = (pg_prod.get('name') or '').strip()
                        code = (pg_prod.get('product_code') or pg_prod.get('id') or pid).strip()
                        if name:
                            item['name'] = name
                        if code:
                            item['code'] = code
                        qty = float(item.get('quantity') or 0)
                        p_u = float(pg_prod.get('price') or pg_prod.get('precio_unidad') or 0)
                        p_m = float(pg_prod.get('wholesale_price') or pg_prod.get('precio_mayor') or 0)
                        p_b = float(pg_prod.get('bulk_price') or pg_prod.get('precio_bulto') or 0)
                        if qty >= 12 and p_b > 0:
                            item['price'] = p_b
                        elif qty >= 3 and p_m > 0:
                            item['price'] = p_m
                        elif p_u > 0:
                            item['price'] = p_u
                        if name or code or item.get('price', 0) > 0:
                            logger.debug(f"  📦 PRODUCTOS 补全自 PG(any): product_id={pid} -> name={item.get('name', '')[:40]}, price={item.get('price')}")
                except Exception as e:
                    logger.warning(f"⚠️ 用 PG(any) 补全 PRODUCTOS 商品失败（继续用现有数据）: {e}")
                
                # CHANGE: 优先使用前端 CARRITO 发送的小计，保证 PEDIDOS 与 CARRITO 一致
                subtotal_from_client = data.get('subtotal')
                try:
                    subtotal_float = float(subtotal_from_client) if subtotal_from_client is not None else None
                except (TypeError, ValueError):
                    subtotal_float = None
                used_client_subtotal = False
                if subtotal_float is not None and subtotal_float >= 0:
                    total = subtotal_float
                    used_client_subtotal = True
                    logger.info(f"💰 使用前端 CARRITO 小计: {total} (保证 PEDIDOS 与 CARRITO 一致)")
                    print(f"💰 [checkout] 使用前端 CARRITO 小计: {total}")
                else:
                    total = self.cart_manager.get_cart_total(user_id)
                    logger.info(f"💰 购物车商品小计(后端计算): {total} (不包含运费)")
                
                # CHANGE: 验证总价
                if total <= 0:
                    logger.error(f"❌ 购物车总价无效: {total}")
                    return jsonify({
                        "success": False,
                        "error": "El total del carrito no es válido. Compruebe los datos de los productos",
                        "error_type": "InvalidTotal"
                    }), 400
                
                # CHANGE: 创建订单（传入客户信息）
                logger.info(f"📝 开始创建订单: user_id={user_id}, total={total}, cart_items={len(cart)}")
                logger.info(f"📝 购物车数据摘要: {len(cart)} 个商品，总价={total}")
                logger.info(f"👤 客户信息: {json.dumps(customer_info, ensure_ascii=False, indent=2)}")
                
                order_id = None
                try:
                    # CHANGE: 传入客户信息和验证后的购物车数据
                    logger.info(f"📝 调用create_order: user_id={user_id}, total={total}, cart_items={len(cart)}")
                    print(f"📝 [API] 调用create_order: user_id={user_id}, total={total}, cart_items={len(cart)}")  # 控制台输出
                    order_id = self.db.create_order(user_id, cart, total, customer_info=customer_info)
                    
                    # CHANGE: 验证订单ID
                    if not order_id:
                        raise RuntimeError("create_order返回None，但没有抛出异常")
                    
                    if not isinstance(order_id, str) or not order_id.strip():
                        raise RuntimeError(f"订单ID无效: {order_id} (type={type(order_id)})")
                    
                    # CHANGE: 验证订单ID格式，确保使用新格式（4部分：ORD_user_id_YYYYMMDD_HHMMSS）
                    parts = order_id.split('_')
                    if len(parts) != 4:
                        logger.warning(f"⚠️ 订单ID格式可能不正确: {order_id} (部分数: {len(parts)}, 应该是4部分: ORD_user_id_YYYYMMDD_HHMMSS)")
                        print(f"⚠️ [API] 订单ID格式可能不正确: {order_id} (部分数: {len(parts)}, 应该是4部分)")  # 控制台输出
                        logger.warning(f"⚠️ 这是旧格式（3部分），但会尝试保存到unified_orders表")
                        print(f"⚠️ [API] 这是旧格式（3部分），但会尝试保存到unified_orders表")  # 控制台输出
                        # 不抛出异常，允许旧格式继续处理（_save_to_unified_orders会处理）
                    else:
                        logger.info(f"✅ 订单ID格式正确: {order_id} (新格式: ORD_user_id_YYYYMMDD_HHMMSS)")
                        print(f"✅ [API] 订单ID格式正确: {order_id} (新格式)")  # 控制台输出
                    
                    logger.info(f"✅ 订单创建成功: order_id={order_id}")
                    print(f"✅ [API] 订单创建成功: order_id={order_id}")  # 控制台输出
                    
                except Exception as create_error:
                    error_msg = str(create_error)
                    error_type = type(create_error).__name__
                    logger.error(f"❌ create_order失败: {error_msg}")
                    logger.error(f"❌ 错误类型: {error_type}")
                    import traceback
                    error_traceback = traceback.format_exc()
                    logger.error(f"❌ 完整错误堆栈:\n{error_traceback}")
                    
                    # CHANGE: 返回更友好的错误信息
                    return jsonify({
                        "success": False,
                        "error": f"创建订单失败: {error_msg}",
                        "error_type": error_type,
                        "details": error_traceback if self.debug else None  # 只在调试模式下返回详细堆栈
                    }), 500
                
                # CHANGE: 双重验证订单ID
                if not order_id:
                    logger.error(f"❌ create_order返回None: user_id={user_id}, cart_items={len(cart)}")
                    return jsonify({
                        "success": False,
                        "error": "Error al crear el pedido: el ID del pedido está vacío",
                        "error_type": "OrderCreationFailed",
                        "user_id": user_id,
                        "cart_items_count": len(cart)
                    }), 500
                
                # CHANGE: 清空购物车（订单创建成功后）
                try:
                    self.cart_manager.save_user_cart(user_id, [])
                    logger.info(f"✅ 购物车已清空: user_id={user_id}")
                except Exception as clear_error:
                    logger.warning(f"⚠️ 清空购物车失败: {clear_error}，但不影响订单创建")
                    # 不清空购物车不影响订单创建成功
                
                # CHANGE: 计算包含运费的最终总价
                SHIPPING_COST = 8.00
                final_total = total + SHIPPING_COST
                
                # CHANGE: 返回更详细的订单信息；used_client_subtotal 供验证 PEDIDOS=CARRITO 逻辑是否生效
                return jsonify({
                    "success": True,
                    "data": {
                        "order_id": order_id,
                        "subtotal": total,  # CHANGE: 商品小计
                        "shipping": SHIPPING_COST,  # CHANGE: 运费
                        "total": final_total,  # CHANGE: 最终总价（小计+运费）
                        "used_client_subtotal": used_client_subtotal
                    },
                    "message": "订单提交成功"
                })
                
            except Exception as e:
                error_msg = str(e)
                import traceback
                error_traceback = traceback.format_exc()
                logger.error(f"❌ 提交订单失败: {error_msg}")
                logger.error(f"❌ 错误类型: {type(e).__name__}")
                logger.error(f"❌ 完整错误堆栈:\n{error_traceback}")
                # 打印到控制台，确保能看到错误
                print(f"\n{'='*60}")
                print(f"❌ 提交订单失败: {error_msg}")
                print(f"❌ 错误类型: {type(e).__name__}")
                print(f"❌ 完整错误堆栈:\n{error_traceback}")
                print(f"{'='*60}\n")
                # CHANGE: 确保错误信息被正确返回为JSON格式
                try:
                    return jsonify({
                        "success": False,
                        "error": f"创建订单失败: {error_msg}",
                        "error_type": type(e).__name__,
                        "details": error_traceback if self.debug else None
                    }), 500
                except Exception as json_error:
                    # 如果jsonify也失败，返回最简单的JSON响应
                    logger.error(f"❌ 无法创建JSON响应: {json_error}")
                    from flask import Response
                    return Response(
                        json.dumps({
                            "success": False,
                            "error": f"创建订单失败: {error_msg}",
                            "error_type": type(e).__name__
                        }, ensure_ascii=False),
                        status=500,
                        mimetype='application/json'
                    )
        
        @self.app.route('/api/orders', methods=['GET'])
        def get_orders():
            """获取订单列表。优先JWT；管理端可用 X-Admin-Token 查看（体检/运维）。"""
            try:
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                if user_id is None or user_id == 0:
                    # CHANGE: 兼容管理端健康检查：允许携带 admin token 查询全部订单摘要
                    ok, _ = _require_admin_token_for_request()
                    if ok:
                        if not self.db:
                            return jsonify({"error": "Base de datos no conectada"}), 500
                        all_orders = self.db.get_orders_for_sync() if hasattr(self.db, 'get_orders_for_sync') else []
                        return jsonify({"success": True, "data": all_orders, "mode": "admin"})
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                
                if not self.db:
                    return jsonify({"error": "Base de datos no conectada"}), 500
                
                orders = self.db.get_user_orders(user_id)
                logger.info(f"📋 获取订单列表: user_id={user_id}, 订单数={len(orders)}")
                # CHANGE: 记录每个订单的总价，用于调试
                for order in orders:
                    logger.info(f"📋 订单 {order.get('id')}: total_amount={order.get('total_amount')}")
                
                return jsonify({
                    "success": True,
                    "data": orders
                })
                
            except Exception as e:
                logger.error(f"❌ 获取订单列表失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/orders/<order_id>', methods=['GET'])
        def get_order_detail(order_id):
            """获取订单详情。仅信任JWT。"""
            try:
                user_id = getattr(request, 'user_id', None) if hasattr(request, 'user_id') else None
                if user_id is None or user_id == 0:
                    return jsonify({"error": "Recargue la página e intente de nuevo"}), 400
                if not self.db:
                    return jsonify({"error": "Base de datos no conectada"}), 500
                
                order_detail = self.db.get_order_detail(order_id, user_id)

                # CHANGE: 某些历史数据里 user_id 可能有格式差异（如字符串/前导0），
                # 列表能查到但详情按 user_id 精确匹配会 404；这里做一次仅按 order_id 的回退查询
                if not order_detail and user_id is not None:
                    logger.warning(f"⚠️ 订单详情按 user_id 未命中，回退仅按 order_id 查询: order_id={order_id}, user_id={user_id}")
                    order_detail = self.db.get_order_detail(order_id, None)

                if not order_detail:
                    return jsonify({"error": "El pedido no existe o no tiene permiso para acceder"}), 404

                logger.info(f"📋 获取订单详情: order_id={order_id}, user_id={user_id}")

                return jsonify({
                    "success": True,
                    "data": order_detail
                })
                
            except Exception as e:
                logger.error(f"❌ 获取订单详情失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/sync/orders', methods=['GET'])
        def sync_orders():
            """云端→本地同步：返回所有订单（unified_orders 格式），需 X-Sync-Token 或 sync_token 与 SYNC_SECRET 一致。
            CHANGE: 返回前用 Neon（PostgreSQL）补全 cart_items 的 code/name，与 checkout 一致，避免其他供应商产品只显示 product_id/PRODUCTO NUEVO。"""
            try:
                sync_secret = os.environ.get('SYNC_SECRET', '').strip()
                token = (request.headers.get('X-Sync-Token') or request.args.get('sync_token') or '').strip()
                if not sync_secret:
                    logger.warning("⚠️ [sync/orders] 未配置 SYNC_SECRET 环境变量")
                    return jsonify({"error": "Sincronización no configurada (configure SYNC_SECRET)"}), 503
                if token != sync_secret:
                    return jsonify({"error": "Token de sincronización inválido"}), 401
                if not self.db:
                    return jsonify({"error": "Base de datos no conectada"}), 500
                orders = self.db.get_orders_for_sync()
                # CHANGE: 用 Neon（PostgreSQL）补全其他供应商产品的 codigo_producto / nombre_producto，与 Neon Console Tablas 一致
                def _is_placeholder(n):
                    if not n or not str(n).strip():
                        return True
                    u = (str(n).strip()).upper()
                    if u in ('NAN', 'NONE', 'NULL') or u == 'PRODUCTO' or u == 'PRODUCTO NUEVO':
                        return True
                    if u.startswith('PRODUCTO ') and len(u) > 9:
                        return True
                    return False
                try:
                    for order_data in orders:
                        items = order_data.get('cart_items') or []
                        for it in items:
                            pid = str(it.get('product_id') or it.get('code') or '').strip()
                            if not pid:
                                continue
                            name = str(it.get('name') or '').strip()
                            code = str(it.get('code') or pid).strip()
                            if not _is_placeholder(name) and code != pid:
                                continue
                            pg_prod = self._get_single_product_from_postgres_any(pid)
                            if not pg_prod:
                                continue
                            pg_name = (pg_prod.get('name') or '').strip()
                            pg_code = (pg_prod.get('product_code') or pg_prod.get('id') or '').strip()
                            if pg_name:
                                it['name'] = pg_name
                            if pg_code:
                                it['code'] = pg_code
                            if pg_name or pg_code:
                                logger.debug(f"  📋 [sync/orders] 补全 cart_item: pid={pid} -> code={pg_code}, name={pg_name[:40] if pg_name else ''}")
                except Exception as e:
                    logger.warning("⚠️ [sync/orders] 用 PG 补全 cart_items 失败（继续返回）: %s", e)
                logger.info(f"📋 [sync/orders] 返回 {len(orders)} 条订单")
                return jsonify({"success": True, "data": orders})
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                logger.error(f"❌ [sync/orders] 失败: {e}\n{tb}")
                return jsonify({"error": str(e), "detail": tb.splitlines()[-2] if tb else ""}), 500
        
        @self.app.route('/api/payment/bank-info', methods=['GET'])
        def get_bank_info():
            """获取转账信息"""
            # CHANGE: 银行信息缓存 5 分钟，减少重复请求
            cache_key = 'bank_info'
            if cache_key in _API_CACHE:
                exp, data = _API_CACHE[cache_key]
                if exp > time.time():
                    return jsonify(data)
            try:
                # CHANGE: 使用全局常量，确保链接正确
                TELEGRAM_LINK = TELEGRAM_CUSTOMER_SERVICE_LINK
                logger.info(f"🔧 [API] 准备返回银行信息，Telegram链接: {TELEGRAM_LINK}")
                print(f"🔧 [API] 准备返回银行信息，Telegram链接: {TELEGRAM_LINK}")
                print(f"🔧 [API] 全局常量值: {TELEGRAM_CUSTOMER_SERVICE_LINK}")
                
                # 参考 ventax_customer_bot.pyw 中的银行信息
                bank_info = {
                    "banks": [
                        {
                            "name": "Banco Pichincha",
                            "type": "CUENTA AHORRO",
                            "number": "2211303833",
                            "account_name": "HONG LUO HUAXING ANGELA",
                            "id_number": "0924844061"
                        },
                        {
                            "name": "Banco del Pacífico",
                            "type": "CUENTA AHORRO",
                            "number": "1063789067",
                            "account_name": "HONG LIANG JINCHAO",
                            "id_number": "0924668502"
                        },
                        {
                            "name": "Banco Guayaquil",
                            "type": "CUENTA CORRIENTE",
                            "number": "30827031",
                            "account_name": "HONG LIANG JINCHAO",
                            "id_number": "0924668502"
                        },
                        {
                            "name": "Produbanco (Grupo Promerica)",
                            "type": "CUENTA AHORRO",
                            "number": "12040601159",
                            "account_name": "HONG LIANG JINCHAO",
                            "id_number": "0924668502"
                        }
                    ],
                    "message": "Por favor, realice la transferencia y envíe el comprobante de transferencia. Una vez confirmado el pago, iniciaremos el proceso de envío inmediatamente.",
                    "customer_service": {
                        "whatsapp": "https://wa.me/593939962405",
                        "telegram": TELEGRAM_LINK  # CHANGE: 强制使用正确的频道链接
                    }
                }
                
                # CHANGE: 强制验证并记录Telegram链接
                telegram_link = bank_info['customer_service']['telegram']
                # 双重保险：确保链接正确
                if telegram_link != TELEGRAM_LINK:
                    logger.error(f"❌ Telegram链接不匹配！当前: {telegram_link}，强制修正为: {TELEGRAM_LINK}")
                    bank_info['customer_service']['telegram'] = TELEGRAM_LINK
                    telegram_link = TELEGRAM_LINK
                
                logger.info(f"📱 [API] 最终返回Telegram链接: {telegram_link}")
                print(f"📱 [API] 最终返回Telegram链接: {telegram_link}")  # 同时输出到控制台
                
                # CHANGE: 在返回前强制覆盖，确保链接正确
                final_data = {
                    "success": True,
                    "data": bank_info
                }
                # 强制覆盖，不进行条件判断
                final_data['data']['customer_service']['telegram'] = TELEGRAM_LINK
                logger.info(f"🔒 [API] 强制设置Telegram链接为: {TELEGRAM_LINK}")
                print(f"🔒 [API] 强制设置Telegram链接为: {TELEGRAM_LINK}")
                
                # 最终验证
                final_telegram = final_data['data']['customer_service']['telegram']
                if final_telegram != TELEGRAM_LINK:
                    logger.error(f"❌❌❌ 严重错误：最终Telegram链接仍然不正确！{final_telegram}")
                    print(f"❌❌❌ 严重错误：最终Telegram链接仍然不正确！{final_telegram}")
                else:
                    logger.info(f"✅✅✅ 最终验证通过：Telegram链接 = {final_telegram}")
                    print(f"✅✅✅ 最终验证通过：Telegram链接 = {final_telegram}")
                
                response = jsonify(final_data)
                # 在响应头中添加验证信息
                response.headers['X-Telegram-Link'] = TELEGRAM_LINK
                _API_CACHE[cache_key] = (time.time() + _API_CACHE_TTL_BANK, final_data)
                return response
                
            except Exception as e:
                logger.error(f"❌ 获取转账信息失败: {e}")
                return jsonify({"error": str(e)}), 500
    
    def cleanup(self):
        """清理资源"""
        logger.info("🧹 正在清理资源...")
        try:
            if hasattr(self, 'db') and self.db:
                # 关闭数据库连接（如果支持）
                try:
                    if hasattr(self.db, 'close'):
                        self.db.close()  # type: ignore
                        logger.info("✅ 数据库连接已关闭")
                except AttributeError:
                    # DatabaseManager可能没有close方法，忽略
                    pass
        except Exception as e:
            logger.warning(f"⚠️ 清理资源时出错: {e}")
    
    def run(self):
        """运行API服务器 - 增强版本"""
        if not self.app:
            logger.error("❌ Flask应用未初始化，无法启动服务器")
            return
        
        try:
            # 设置信号处理
            import signal
            def signal_handler(signum, frame):
                logger.info("🛑 收到停止信号，正在清理资源...")
                self.cleanup()
                sys.exit(0)
            
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            
            # 使用端口管理器（如果可用）
            try:
                import sys
                parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if parent_dir not in sys.path:
                    sys.path.append(parent_dir)
                from port_manager import get_port_manager  # type: ignore
                
                port_mgr = get_port_manager()
                api_port = port_mgr.get_bot_port("pwa_cart_api")
                
                if api_port:
                    logger.info(f"✅ 使用端口管理器分配的端口: {api_port}")
                    self.port = api_port
                    # 保留端口给当前进程
                    port_mgr.reserve_port("pwa_cart_api", os.getpid())
                else:
                    logger.info(f"📌 使用配置的端口: {self.port}")
                    
            except Exception as e:
                logger.debug(f"端口管理器不可用，使用默认端口: {e}")

            # CHANGE: 启动前自动清除占用端口的旧进程
            _clear_port_occupation(self.port)

            # 启动信息
            logger.info(f"🚀 启动PWA购物车API服务器: http://{self.host}:{self.port}")
            # NOTE: 局域网访问时 host=0.0.0.0，打印本机IP方便移动设备访问
            if self.host == '0.0.0.0':
                try:
                    import socket
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.settimeout(0)
                    s.connect(('8.8.8.8', 1))
                    lan_ip = s.getsockname()[0]
                    s.close()
                    logger.info(f"📱 局域网访问: http://{lan_ip}:{self.port} （同WiFi下手机/平板可打开）")
                except Exception:
                    logger.info("📱 局域网访问: 使用本机IP替换 0.0.0.0 即可")
            logger.info(f"📁 当前工作目录: {os.getcwd()}")
            logger.info(f"📁 数据库路径: {self.db.db_path if self.db else 'N/A'}")
            logger.info(f"📁 静态文件目录: {self.app.static_folder if self.app else 'N/A'}")
            
            # CHANGE: 用 WSGI 中间件在进入 Flask 前重写 /pwa_cart/api -> /api，避免被 /pwa_cart/<path:filename> 当静态文件匹配导致 404
            flask_app = self.app
            def pwa_cart_api_rewrite_middleware(environ, start_response):
                path = environ.get('PATH_INFO', '') or ''
                if path.startswith('/pwa_cart/api'):
                    new_path = '/api' + path[len('/pwa_cart/api'):]
                    environ['PATH_INFO'] = new_path
                    logger.debug(f"📌 [WSGI] 重写: {path} -> {new_path}")
                return flask_app.wsgi_app(environ, start_response)
            # 使用threaded=True支持多请求，use_reloader=False避免重复启动
            from werkzeug.serving import run_simple
            run_simple(self.host, self.port, pwa_cart_api_rewrite_middleware, threaded=True, use_reloader=False)
                
        except KeyboardInterrupt:
            logger.info("🛑 用户中断，正在清理资源...")
            self.cleanup()
        except Exception as e:
            logger.error(f"❌ API服务器启动失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.cleanup()
            raise
        finally:
            logger.info("👋 API服务器已停止")


def main():
    """主函数 - 支持环境变量配置"""
    import argparse
    import sys
    
    # CHANGE: 在启动时显示Python环境和JWT状态
    print("=" * 60)
    print("🚀 PWA购物车API服务器启动信息")
    print("=" * 60)
    print(f"📌 Python解释器: {sys.executable}")
    print(f"📌 Python版本: {sys.version}")
    print(f"📌 工作目录: {os.getcwd()}")
    print(f"📌 JWT_AVAILABLE: {JWT_AVAILABLE}")
    if JWT_AVAILABLE:
        try:
            import jwt
            print(f"✅ JWT库状态: 可用，版本 {jwt.__version__}")
        except Exception as e:
            print(f"❌ JWT库状态: 标记为可用但导入失败: {e}")
    else:
        print("❌ JWT库状态: 不可用")
        print("💡 解决方案: 运行 'pip install PyJWT' 安装JWT库")
    print("=" * 60)
    print()
    
    # 🚀 支持环境变量：从环境变量读取配置
    default_host = os.getenv('PWA_API_HOST', '127.0.0.1')
    default_port = int(os.getenv('PWA_API_PORT', '5000'))
    default_debug = os.getenv('PWA_API_DEBUG', '0').lower() in {'1', 'true', 'on'}
    
    parser = argparse.ArgumentParser(description='PWA购物车API服务器')
    parser.add_argument('--host', default=default_host, help=f'服务器地址 (默认: {default_host})')
    parser.add_argument('--port', type=int, default=default_port, help=f'服务器端口 (默认: {default_port})')
    parser.add_argument('--debug', action='store_true', default=default_debug, help='调试模式')
    
    args = parser.parse_args()
    
    # 显示启动信息
    if os.getenv('PWA_API_HOST') or os.getenv('PWA_API_PORT') or os.getenv('PWA_API_DEBUG'):
        logger.info("🚀 检测到环境变量配置，使用自定义配置")
        if os.getenv('PWA_API_HOST'):
            logger.info(f"  主机: {args.host}")
        if os.getenv('PWA_API_PORT'):
            logger.info(f"  端口: {args.port}")
        if os.getenv('PWA_API_DEBUG'):
            logger.info(f"  调试模式: {args.debug}")
    else:
        logger.info("📱 使用默认配置")
    
    # CHANGE: 再次检查JWT状态并警告
    if not JWT_AVAILABLE:
        logger.error("=" * 60)
        logger.error("⚠️ 警告: JWT库未安装，登录/注册功能将无法使用！")
        logger.error("💡 请运行以下命令安装JWT库:")
        logger.error(f"   {sys.executable} -m pip install PyJWT")
        logger.error("=" * 60)
    
    server = PWACartAPIServer(host=args.host, port=args.port, debug=args.debug)
    server.run()


if __name__ == '__main__':
    main()
else:
    # Gunicorn WSGI entry point（Render 方案 A 部署用）
    # 用法: gunicorn --bind 0.0.0.0:$PORT pwa_cart_api_server:app
    _srv_for_wsgi = PWACartAPIServer(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
    app = _srv_for_wsgi.app

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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

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
    from flask import Flask, jsonify, request
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
        except Exception as e:
            logger.warning(f"⚠️ 读取 port_config.json 图片路径失败: {e}")
        if not self.product_image_dirs and os.getenv('PWA_PRODUCT_IMAGE_DIRS'):
            self.product_image_dirs = [os.path.normpath(p.strip()) for p in os.getenv('PWA_PRODUCT_IMAGE_DIRS', '').split(',') if p.strip()]
        # CHANGE: 方案 A 云端部署时用 R2 或 Cloudflare Pages；图片 URL 指向云端，不需本机 /api/images/
        self.r2_image_base_url = (os.getenv('R2_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
        self.pages_image_base_url = (os.getenv('PAGES_IMAGE_BASE_URL', '') or '').strip().rstrip('/')
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
            # CHANGE: 明确允许云端页 ventax.pages.dev 与本机，避免部署到 Render 后 CORS 拦截
            CORS(self.app, origins=[
                "https://ventax.pages.dev", "http://localhost:5000", "http://127.0.0.1:5000",
                "http://localhost", "http://127.0.0.1"
            ], supports_credentials=True)
            
            # CHANGE: /pwa_cart/api/* 重写为 /api/*，便于前端在 /pwa_cart/ 页时统一用 /pwa_cart/api 避免 404（如反向代理只转发 /pwa_cart 时）
            @self.app.before_request
            def rewrite_pwa_cart_api():
                if request.path.startswith('/pwa_cart/api'):
                    new_path = '/api' + request.path[len('/pwa_cart/api'):]
                    request.environ['PATH_INFO'] = new_path
                    logger.debug(f"📌 重写 PATH_INFO: {request.path} -> {new_path}")

            # CHANGE: 添加认证中间件
            @self.app.before_request
            def authenticate_request():
                """从请求头中提取token并验证用户"""
                # 跳过认证相关的路由
                if request.path.startswith('/api/auth/'):
                    return
                
                # 尝试从请求头获取token
                auth_header = request.headers.get('Authorization')
                if auth_header and auth_header.startswith('Bearer '):
                    token = auth_header[7:]  # 移除 'Bearer ' 前缀
                    payload = self._verify_token(token)
                    if payload:
                        # 将用户信息添加到request对象（使用setattr避免类型检查错误）
                        setattr(request, 'user_id', payload.get('user_id'))
                        setattr(request, 'user_email', payload.get('email'))
                    else:
                        setattr(request, 'user_id', None)
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
                    "error": f"服务器内部错误: {error_msg}",
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

    def _get_pg_config(self) -> Optional[Dict[str, Any]]:
        """从 database_config.json 或 DATABASE_URL 环境变量读取 PostgreSQL 配置（ULTIMO 产品数据源）。
        CHANGE: 方案 A 云端部署时优先用 DATABASE_URL（Neon 等托管 PG 提供）。"""
        db_url = os.getenv('DATABASE_URL', '').strip()
        if db_url:
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
        basename = _normalize_image_filename(os.path.basename(ruta.replace('/', os.path.sep)))
        if not basename:
            return ''
        # Cloudflare Pages：一键同步上传了 pwa_cart/Ya Subio（含 Cristy），URL 为 base/Ya%20Subio/Cristy/xxx 或 base/Ya%20Subio/xxx
        if getattr(self, 'pages_image_base_url', None):
            seg = quote('Ya Subio', safe='/')
            sub = 'Cristy/' if (supplier or '').strip() == 'Cristy' else ''
            return f"{self.pages_image_base_url}/{seg}/{sub}{quote(basename, safe='/')}"
        if self.r2_image_base_url:
            return f"{self.r2_image_base_url}/{basename}"
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
            cur.execute(
                """
                SELECT id_producto, codigo_producto, nombre_producto, descripcion,
                       precio_unidad, precio_mayor, precio_bulto, categoria, ruta_imagen,
                       codigo_proveedor, fecha_creacion, esta_activo
                FROM products
                WHERE (codigo_proveedor IS NULL OR codigo_proveedor != 'Cristy')
                  AND (esta_activo IS NULL OR esta_activo = TRUE)
                  AND codigo_proveedor IS NOT NULL AND codigo_proveedor != ''
                ORDER BY fecha_creacion DESC NULLS LAST
                """
            )
            rows = cur.fetchall()
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
                    'codigo_proveedor': (_r.get('codigo_proveedor') or '').strip(),
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
        """从 PostgreSQL 按 id_producto/codigo_producto 查询单条产品（不限制 Cristy），供详情页回退。"""
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
            cur.execute(
                """
                SELECT id_producto, codigo_producto, nombre_producto, descripcion,
                       precio_unidad, precio_mayor, precio_bulto, categoria, ruta_imagen,
                       inventario, codigo_proveedor, fecha_creacion, esta_activo
                FROM products
                WHERE (codigo_producto = %s OR id_producto::text = %s)
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
            ruta = self._format_image_path(str(_r.get('ruta_imagen') or ''), (_r.get('codigo_proveedor') or '').strip())
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
        one_month_ago: datetime,
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
            if not cp or cp not in [c.lower() for c in whitelist if c]:
                continue
            should_skip = False
            ca = pinfo.get('created_at', '')
            if ca:
                try:
                    dt = None
                    if 'T' in ca:
                        s = ca.replace('Z', '').split('+')[0]
                        try:
                            dt = datetime.fromisoformat(s)
                        except (ValueError, AttributeError):
                            s2 = s.split('.')[0] if '.' in s else s
                            dt = datetime.strptime(s2, '%Y-%m-%dT%H:%M:%S')
                    else:
                        dt = datetime.strptime(ca, '%Y-%m-%d %H:%M:%S')
                    if dt and dt < one_month_ago:
                        skipped_by_date += 1
                        should_skip = True
                except Exception:
                    pass
            if should_skip:
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
        
        @self.app.route('/')
        def home():
            """主页 - 重定向到PWA"""
            from flask import redirect
            return redirect('/pwa_cart/')
        
        @self.app.route('/pwa_cart/')
        def pwa_home():
            """PWA主页"""
            from flask import send_from_directory
            if not self.app or not self.app.static_folder:
                from flask import abort
                abort(500)
            return send_from_directory(self.app.static_folder, 'index.html')
        
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
                from flask import abort
                abort(500)
            
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
            logger.debug(f"📁 PWA静态文件请求: {filename}, static_folder: {self.app.static_folder if self.app else 'N/A'}")
            if not self.app or not self.app.static_folder:
                logger.error("❌ PWA静态文件: app或static_folder未设置")
                abort(500)
            file_path = os.path.join(self.app.static_folder, filename)
            logger.debug(f"📁 文件路径: {file_path}, 存在: {os.path.exists(file_path)}")
            if os.path.exists(file_path) and os.path.isfile(file_path):
                # 确保 SVG 文件返回正确的 Content-Type
                if filename.endswith('.svg'):
                    response = send_from_directory(self.app.static_folder, filename)
                    response.headers['Content-Type'] = 'image/svg+xml; charset=utf-8'
                    return response
                return send_from_directory(self.app.static_folder, filename)
            else:
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
            from flask import send_from_directory, jsonify
            from urllib.parse import unquote
            
            filename = unquote(filename)
            base_filename = os.path.basename(filename)
            base_filename_clean = _normalize_image_filename(base_filename)
            
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
                if found_path:
                    found_dir = os.path.dirname(found_path)
                    found_file = os.path.basename(found_path)
                    logger.info(f"✅ 图片（可配置目录子文件夹）: {found_path}")
                    return send_from_directory(found_dir, found_file)
            
            # 未在可配置目录中找到；改 port_config.json 中 pwa_cart.product_image_dirs 指向正确文件夹即可
            logger.warning(f"❌ 未找到图片: {filename}，可配置目录: {image_dirs}")
            print(f"❌ [API] 未找到图片: {filename}，请检查 port_config.json 中 pwa_cart.product_image_dirs")
            resp = jsonify({
                "error": f"图片未找到: {filename}",
                "hint": "请将同名文件放入以下任一目录（根或子文件夹均可）: " + ", ".join(image_dirs)
            })
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
            return resp, 404
        
        @self.app.route('/api/info')
        def api_info():
            """API信息"""
            return jsonify({
                "service": "PWA购物车API服务器",
                "description": "为PWA购物车网页提供RESTful API接口",
                "version": "1.0.0",
                "endpoints": {
                    "/": "PWA主页",
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
                "service": "PWA购物车API",
                "timestamp": datetime.now().isoformat(),
                "database": "connected" if self.db else "disconnected"
            })

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
        @self.app.route('/api/auth/register', methods=['POST'])
        def register():
            """用户注册 - 邮箱注册"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "请求体为空"}), 400
                
                email = data.get('email', '').strip().lower()
                password = data.get('password', '')
                name = data.get('name', '').strip()
                
                if not email:
                    return jsonify({"success": False, "error": "邮箱不能为空"}), 400
                if not password or len(password) < 6:
                    return jsonify({"success": False, "error": "密码至少需要6个字符"}), 400
                
                if not self.db:
                    return jsonify({"success": False, "error": "数据库未连接"}), 500
                
                # 检查邮箱是否已存在
                existing_user = self.db.get_user_by_email(email)
                if existing_user:
                    return jsonify({"success": False, "error": "邮箱已被注册"}), 400
                
                # 创建用户
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
                    return jsonify({"success": False, "error": "JWT库未安装，请运行: pip install PyJWT"}), 500
                
                try:
                    token = self._generate_token(user_id, email)
                    if not token:
                        logger.error(f"❌ 生成token失败: user_id={user_id}, email={email}, _generate_token返回None")
                        print(f"❌ 生成token失败: user_id={user_id}, email={email}, _generate_token返回None")  # 控制台输出
                        return jsonify({"success": False, "error": "生成token失败，请检查服务器日志"}), 500
                except Exception as token_error:
                    logger.error(f"❌ 生成token时发生异常: {token_error}")
                    import traceback
                    logger.error(traceback.format_exc())
                    print(f"❌ 生成token时发生异常: {token_error}")  # 控制台输出
                    return jsonify({"success": False, "error": f"生成token失败: {str(token_error)}"}), 500
                
                # 更新最后登录时间
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
                    "message": "注册成功"
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
                    return jsonify({"success": False, "error": "请求体为空"}), 400
                
                email = data.get('email', '').strip().lower()
                password = data.get('password', '')
                
                logger.info(f"🔐 登录尝试: email={email}, password_length={len(password)}")
                print(f"🔐 登录尝试: email={email}, password_length={len(password)}")  # 控制台输出
                
                if not email or not password:
                    return jsonify({"success": False, "error": "邮箱和密码不能为空"}), 400
                
                if not self.db:
                    return jsonify({"success": False, "error": "数据库未连接"}), 500
                
                # 获取用户
                user = self.db.get_user_by_email(email)
                logger.info(f"🔍 查询用户结果: user={'存在' if user else '不存在'}, email={email}")
                print(f"🔍 查询用户结果: user={'存在' if user else '不存在'}, email={email}")  # 控制台输出
                
                if not user:
                    logger.warning(f"❌ 用户不存在: email={email}")
                    print(f"❌ 用户不存在: email={email}")  # 控制台输出
                    return jsonify({"success": False, "error": "邮箱或密码错误"}), 401
                
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
                    return jsonify({"success": False, "error": "邮箱或密码错误"}), 401
                
                # 检查用户是否激活
                if not user.get('is_active', True):
                    return jsonify({"success": False, "error": "账户已被禁用"}), 403
                
                # 生成token
                if not JWT_AVAILABLE:
                    logger.error("❌ JWT库未安装，无法生成token")
                    print("❌ JWT库未安装，无法生成token")  # 控制台输出
                    return jsonify({"success": False, "error": "JWT库未安装，请运行: pip install PyJWT"}), 500
                
                logger.info(f"🔑 开始生成token: user_id={user['id']}, email={email}, JWT_AVAILABLE={JWT_AVAILABLE}")
                print(f"🔑 开始生成token: user_id={user['id']}, email={email}, JWT_AVAILABLE={JWT_AVAILABLE}")  # 控制台输出
                
                try:
                    token = self._generate_token(user['id'], email)
                    if not token:
                        logger.error(f"❌ 生成token失败: user_id={user['id']}, email={email}, _generate_token返回None")
                        print(f"❌ 生成token失败: user_id={user['id']}, email={email}, _generate_token返回None")  # 控制台输出
                        return jsonify({"success": False, "error": "生成token失败，请检查服务器日志"}), 500
                    logger.info(f"✅ Token生成成功: user_id={user['id']}, token长度={len(token)}")
                    print(f"✅ Token生成成功: user_id={user['id']}, token长度={len(token)}")  # 控制台输出
                except Exception as token_error:
                    logger.error(f"❌ 生成token时发生异常: {token_error}")
                    import traceback
                    logger.error(traceback.format_exc())
                    print(f"❌ 生成token时发生异常: {token_error}")  # 控制台输出
                    print(traceback.format_exc())  # 控制台输出
                    return jsonify({"success": False, "error": f"生成token失败: {str(token_error)}"}), 500
                
                # 更新最后登录时间
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
                    return jsonify({"success": False, "error": "请求体为空"}), 400
                
                token = data.get('token')
                if not token:
                    return jsonify({"success": False, "error": "token不能为空"}), 400
                
                payload = self._verify_token(token)
                if not payload:
                    return jsonify({"success": False, "error": "token无效或已过期"}), 401
                
                # 获取用户信息
                if not self.db:
                    return jsonify({"success": False, "error": "数据库未连接"}), 500
                
                user = self.db.get_user_by_id(payload.get('user_id'))
                if not user:
                    return jsonify({"success": False, "error": "用户不存在"}), 404
                
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
                    return jsonify({"success": False, "error": "请求体为空"}), 400
                email = data.get('email', '').strip().lower()
                if not email:
                    return jsonify({"success": False, "error": "邮箱不能为空"}), 400
                if not self.db:
                    return jsonify({"success": False, "error": "数据库未连接"}), 500
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                expires_at = (datetime.utcnow() + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
                user_id = self.db.create_password_reset_token(email, token_hash, expires_at)
                if not user_id:
                    return jsonify({"success": True, "message": "若该邮箱已注册，将收到重置链接"}), 200
                base_url = request.url_root.rstrip('/')
                reset_url = f"{base_url}/pwa_cart/#/reset?token={token}"
                return jsonify({
                    "success": True,
                    "reset_url": reset_url,
                    "message": "Abre el siguiente enlace para restablecer tu contraseña (válido 24 horas)"
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
                    return jsonify({"success": False, "error": "请求体为空"}), 400
                token = data.get('token', '').strip()
                new_password = data.get('password', '')
                if not token:
                    return jsonify({"success": False, "error": "Token 不能为空"}), 400
                if not new_password or len(new_password) < 6:
                    return jsonify({"success": False, "error": "密码至少需要6个字符"}), 400
                if not self.db:
                    return jsonify({"success": False, "error": "数据库未连接"}), 500
                token_hash = hashlib.sha256(token.encode()).hexdigest()
                user = self.db.get_user_by_reset_token(token_hash)
                if not user:
                    return jsonify({"success": False, "error": "Enlace inválido o expirado"}), 400
                password_hash = self._hash_password(new_password)
                if not self.db.update_password_and_clear_reset(user['id'], password_hash):
                    return jsonify({"success": False, "error": "更新密码失败"}), 500
                return jsonify({"success": True, "message": "Contraseña restablecida correctamente"}), 200
            except Exception as e:
                logger.error(f"❌ 重置密码失败: {e}")
                return jsonify({"success": False, "error": str(e)}), 500
        
        @self.app.route('/api/products', methods=['GET'])
        def get_products():
            """获取产品列表 - 按新到旧排序，只显示激活的产品"""
            try:
                if not self.db:
                    return jsonify({"error": "数据库未连接"}), 500
                
                # 获取查询参数
                category = request.args.get('category', None)
                search = request.args.get('search', None)
                supplier = request.args.get('supplier', None)  # CHANGE: 支持 supplier 参数筛选
                supplier_lower = (supplier or '').strip().lower()  # 统一小写比较，避免 Others/others 等导致走错分支
                page = int(request.args.get('page', 1))
                limit = int(request.args.get('limit', 30))  # 默认返回30个产品
                # CHANGE: 移除 supplier=others 早期返回空，让 PRODUCTOS 按「DB 为主 + 图片在 D:\Ya Subio 匹配」正常显示
                # 获取所有产品（暂时註销 SQLite 时仅用 PostgreSQL）
                if USE_SQLITE_FOR_PRODUCTS and self.db:
                    products = self.db.get_all_products()
                else:
                    products = self._get_products_dict_from_postgres()
                
                # CHANGE: 自家产品标识 - 使用 codigo_proveedor = 'Cristy'
                OWN_SUPPLIER_CODE = 'Cristy'
                
                # CHANGE: 计算一个月前的日期（用于过滤旧产品）
                one_month_ago = datetime.now() - timedelta(days=30)
                logger.info(f"📅 [API] 一个月前日期: {one_month_ago}, 当前日期: {datetime.now()}")
                print(f"📅 [API] 一个月前日期: {one_month_ago}, 当前日期: {datetime.now()}")
                
                # CHANGE: 抽取到 _filter_products_cristy_and_others 降低 get_products 复杂度
                cristy_from_pg = self._get_ultimo_products_from_postgres()
                cristy_products, all_filtered_products, skipped_by_date, skipped_cristy_by_stock = self._filter_products_cristy_and_others(
                    products, cristy_from_pg, one_month_ago, OWN_SUPPLIER_CODE
                )
                
                # CHANGE: 根据 supplier 参数决定使用哪个产品列表（抽取到 _select_products_by_supplier 降低复杂度）
                logger.info(f"📊 [API] 产品统计: 总产品={len(products)}, PRODUCTOS(其他/1月)={len(all_filtered_products)}, 被日期过滤={skipped_by_date}, ULTIMO(Cristy/库存>=6)={len(cristy_products)}, Cristy库存下架={skipped_cristy_by_stock}, supplier={supplier}")
                print(f"📊 [API] 产品统计: 总产品={len(products)}, PRODUCTOS(其他/1月)={len(all_filtered_products)}, 被日期过滤={skipped_by_date}, ULTIMO(Cristy/库存>=6)={len(cristy_products)}, Cristy库存下架={skipped_cristy_by_stock}, supplier={supplier}")
                if len(all_filtered_products) > 0:
                    sample_providers = [pinfo.get('codigo_proveedor', 'NULL') for _, pinfo in all_filtered_products[:3]]
                    print(f"🔍 [API] 前3个产品的 codigo_proveedor: {sample_providers}")
                
                products_to_process = self._select_products_by_supplier(
                    cristy_products, all_filtered_products, products, supplier_lower, search, OWN_SUPPLIER_CODE
                )
                
                # CHANGE: 有搜索关键词时，强制使用 ULTIMO+PRODUCTOS 并集，实现跨两页搜索
                if search and str(search).strip():
                    seen_search = set()
                    combined_search = []
                    for pid, pinfo in cristy_products:
                        seen_search.add(pid)
                        combined_search.append((pid, pinfo))
                    for pid, pinfo in all_filtered_products:
                        if pid not in seen_search:
                            seen_search.add(pid)
                            combined_search.append((pid, pinfo))
                    combined_search.sort(key=lambda x: x[1].get('created_at', '') or '', reverse=True)
                    products_to_process = combined_search
                    logger.info(f"🔍 [API] 搜索模式：使用 ULTIMO+PRODUCTOS 并集共 {len(products_to_process)} 个产品进行搜索")
                    print(f"🔍 [API] 搜索模式：使用 ULTIMO+PRODUCTOS 并集共 {len(products_to_process)} 个产品进行搜索")
                
                # CHANGE: 图片文件名从可配置目录（port_config.json pwa_cart.product_image_dirs）递归收集，与 serve_product_image 一致
                import re
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
                    logger.info(f"📷 [API] PRODUCTOS 仅 D:\\Ya Subio（排除 Cristy）: 共 {len(_files_ya_subio_only)} 张图（不含 product_images/output_images）")
                    print(f"📷 [API] PRODUCTOS 仅 D:\\Ya Subio（排除 Cristy）: 共 {len(_files_ya_subio_only)} 张图")
                else:
                    logger.info(f"📷 [API] 图片目录 D:\\Ya Subio 全量: 共 {len(_files_ya_subio_merged)} 张图")
                    print(f"📷 [API] 图片目录: 共 {len(_files_ya_subio_merged)} 张图")
                # 过滤与解析统一用并集：只显示「D:\Ya Subio 或 D:\Ya Subio\Cristy 内有对应图片」的产品
                _files_ya_subio = _files_ya_subio_merged
                # CHANGE: supplier=others 时仅用 D:\Ya Subio（排除 Cristy）的图匹配，严格「以 DB 为主 + 图在 D:\Ya Subio」
                _files_for_resolve = _files_ya_subio_only if supplier_lower == 'others' else _files_ya_subio
                if not _files_ya_subio and _processed_dir:
                    logger.warning(f"⚠️ [API] 可配置图片目录下未扫到任何图片，请检查路径与权限: {_processed_dir}, {_cristy_subdir}")
                    print(f"⚠️ [API] 可配置图片目录下未扫到任何图片，请检查路径与权限: {_processed_dir}, {_cristy_subdir}")
                elif _files_ya_subio:
                    print(f"📷 [API] 图片文件名样本(前15): {_files_ya_subio[:15]}")

                # CHANGE: supplier=Cristy 时以图为准：先遍历图片文件夹，用文件名解析 product_id，再查库填 name/price，保证一图一产品数据不错位
                # CHANGE: 有 search 时强制走 filtered_with_meta 逻辑，确保搜索过滤生效
                _skip_image_first = bool(search and str(search).strip())
                print(f"📷 [API] Cristy 检查: _is_cristy_request={_is_cristy_request}, len(cristy_products)={len(cristy_products)}, len(_files_cristy)={len(_files_cristy)}, _cristy_subdir={_cristy_subdir!r}, _skip_image_first={_skip_image_first}")
                if not _skip_image_first and _is_cristy_request and len(cristy_products) > 0 and len(_files_cristy) > 0:
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
                            'category': _pinfo.get('category_id', 'default'),
                            'created_at': _created,
                            'channel_username': _pinfo.get('channel_username', ''),
                            'codigo_proveedor': _pinfo.get('codigo_proveedor', '')
                        })
                    for i, p in enumerate(paginated_products[:3]):
                        print(f"   [Cristy图为准] 产品[{i}] id={p.get('id')} name={p.get('name')[:40] if p.get('name') else ''} price={p.get('price')} image={p.get('image_path', '')[:60]}")
                    total_filtered = _total_cristy
                    resp = jsonify({
                        "success": True,
                        "data": paginated_products,
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
                # CHANGE: PRODUCTOS(supplier=others) 仅按「产品图片名称」查找映射：用全库 products 的 ruta_imagen 建 文件名->产品，保证每张图对应 DB 中该图名的产品数据
                # 仅用 D:\Ya Subio（排除 Cristy）：按 三脚本图片保存读取路径一览.md，上传 Telegram 后处理图已移至 D:\Ya Subio
                # CHANGE: 有 search 时跳过「以图为准」分支，强制走 filtered_with_meta 确保搜索过滤
                if not _skip_image_first and supplier_lower == 'others' and len(_files_ya_subio_only) > 0:
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
                    for _f in _files_ya_subio_only:
                        _base = os.path.splitext(_f)[0].strip()
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
                            'category': _pinfo.get('category_id', 'default'),
                            'created_at': _created,
                            'channel_username': _pinfo.get('channel_username', ''),
                            'codigo_proveedor': _pinfo.get('codigo_proveedor', '')
                        })
                    logger.info(f"📦 [API] PRODUCTOS 以图为准: 共 {_total_others} 个，本页 {len(paginated_products)} 个，DB图关联数={len(_image_to_product)}")
                    print(f"📦 [API] PRODUCTOS 以图为准: 共 {_total_others} 个，本页 {len(paginated_products)} 个，DB图关联数={len(_image_to_product)}")
                    resp = jsonify({
                        "success": True,
                        "data": paginated_products,
                        "pagination": {
                            "page": page,
                            "limit": limit,
                            "total": _total_others,
                            "total_pages": (_total_others + limit - 1) // limit if _total_others else 1
                        }
                    })
                    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
                    resp.headers['Pragma'] = 'no-cache'
                    resp.headers['X-Image-Logic'] = 'productos-image-first'
                    return resp
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
                    """CHANGE: 只通过图片名字与 product_id 匹配才返回，通过图片名字进行匹配不可能出错。supplier=others 时用 _files_for_resolve（非Cristy图）"""
                    files = _files_for_resolve
                    if not files:
                        return ''
                    mid = None  # 仅按图片名匹配，不再用 message_id 等
                    # 1) 有 DB 路径：先精确匹配文件名，再尝试「从文件名抽 message_id -> telegram_<id>.jpg」
                    if img_path:
                        if img_path.startswith('/api/images/'):
                            fname = _normalize_image_filename(img_path.replace('/api/images/', '').split('?')[0].strip())
                        else:
                            fname = _normalize_image_filename(os.path.basename(img_path.replace('/', os.sep).replace('\\', os.sep).strip()))
                        if fname:
                            base_db = os.path.splitext(fname)[0]
                            for f in files:
                                base_f = os.path.splitext(f)[0]
                                # 匹配：product_id 与文件名一致，或 product 的 image_path 文件名与 file 一致（PRODUCTOS 产品用）
                                if f.lower() == fname.lower():
                                    if _file_base_matches_product(base_f, pid):
                                        return f'/api/images/{f}'
                                    if base_f.lower() == base_db.lower():
                                        return f'/api/images/{f}'
                            for f in files:
                                base_f = os.path.splitext(f)[0]
                                if (base_f.lower() == base_db.lower() or _normalize_base_ai_al(base_f) == _normalize_base_ai_al(base_db)) and _file_base_matches_product(base_f, pid):
                                    return f'/api/images/{f}'
                            for f in files:
                                base_f = os.path.splitext(f)[0]
                                if base_f.lower() == base_db.lower():
                                    return f'/api/images/{f}'
                    # 2) 只通过图片名字与 product_id 匹配：遍历文件，仅当文件名与产品一致才返回（不可能错配）
                    if not mid and img_path:
                        fname = _normalize_image_filename(os.path.basename(img_path.replace('/', os.path.sep).replace('\\', os.sep).strip())) if img_path.startswith('/api/images/') else _normalize_image_filename(os.path.basename(img_path.replace('/', os.path.sep).replace('\\', os.sep).strip()))
                        base_db = os.path.splitext(fname)[0] if fname else ''
                        mid = _message_id_from_name(base_db)
                    if mid:
                        tg_name = 'telegram_' + mid
                        for f in files:
                            base_f = os.path.splitext(f)[0]
                            if base_f.lower() == tg_name and _file_base_matches_product(base_f, pid):
                                return f'/api/images/{f}'
                        for f in files:
                            base_f = os.path.splitext(f)[0]
                            if base_f.lower() == mid and _file_base_matches_product(base_f, pid):
                                return f'/api/images/{f}'
                        msg_prefix = 'msg_' + mid + '_'
                        for f in files:
                            base_f = os.path.splitext(f)[0]
                            if base_f.lower().startswith(msg_prefix) and _file_base_matches_product(base_f, pid):
                                return f'/api/images/{f}'
                    # 3) D:\Ya Subio 内多为 importadoraWoni_115_no_white.jpg：用「数字段」匹配（文件名分段含该数字），优先前缀一致
                    base_db = ''
                    if img_path:
                        fname = _normalize_image_filename(img_path.replace('/api/images/', '').split('?')[0].strip()) if img_path.startswith('/api/images/') else _normalize_image_filename(os.path.basename(img_path.replace('/', os.path.sep).replace('\\', os.sep).strip()))
                        base_db = os.path.splitext(fname)[0] if fname else ''
                    if not base_db and pid:
                        base_db = str(pid).strip()
                    num_to_try = mid
                    if not num_to_try and base_db:
                        parts_db = re.split(r'[_\-.\s]+', base_db.lower())
                        for p in reversed(parts_db):
                            if p.isdigit() and len(p) >= 2:
                                num_to_try = p
                                break
                    if num_to_try:
                        candidates = [f for f in files if num_to_try in re.split(r'[_\-.\s]+', os.path.splitext(f)[0].lower())]
                        if candidates:
                            if base_db:
                                for f in candidates:
                                    base_f = os.path.splitext(f)[0]
                                    if (base_db.lower() in base_f.lower() or base_f.lower() in base_db.lower() or _normalize_base_ai_al(base_f) == _normalize_base_ai_al(base_db)) and _file_base_matches_product(base_f, pid):
                                        return f'/api/images/{f}'
                            for f in candidates:
                                base_f = os.path.splitext(f)[0]
                                if _file_base_matches_product(base_f, pid):
                                    return f'/api/images/{f}'
                    # 仅当图片文件名与 product_id 一致才返回（通过图片名字匹配不可能出错）
                    for f in files:
                        base_f = os.path.splitext(f)[0]
                        if _file_base_matches_product(base_f, pid):
                            return f'/api/images/{f}'
                    return ''
                
                # CHANGE: 方案 A - 只显示「在 D:\Ya Subio 有图」的产品；要显示更多产品就把更多已处理图放入 D:\Ya Subio（且文件名能被现有匹配规则识别）
                filtered_with_meta = []
                for product_id, product_info in products_to_process:
                    if category and product_info.get('category_id') != category:
                        continue
                    # CHANGE: 只搜索 nombre_producto 与 descripcion，大小写不敏感，模糊匹配收紧
                    if search:
                        q_raw = str(search).strip().lower()
                        keywords = [k.strip() for k in q_raw.split() if k.strip()]
                        if not keywords:
                            continue
                        # 大小写不敏感：统一转小写
                        name_s = (product_info.get('name') or product_info.get('nombre_producto') or '').lower()
                        desc_s = (product_info.get('description') or product_info.get('descripcion') or '').lower()
                        searchable_parts = [name_s, desc_s]
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
                    resolved = resolve_image_for_product(product_id, image_path)
                    if not resolved:
                        continue  # 图片不在 D:\Ya Subio 内，不显示该产品
                    filtered_with_image.append((product_id, product_info, created_at, resolved))
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
                        'category': product_info.get('category_id', 'default'),
                        'created_at': created_at,
                        'channel_username': product_info.get('channel_username', ''),
                        'codigo_proveedor': product_info.get('codigo_proveedor', '')
                    })
                
                # product_list 已是当前页，total 用 total_filtered
                paginated_products = product_list
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
                
                resp = jsonify({
                    "success": True,
                    "data": paginated_products,
                    "pagination": {
                        "page": page,
                        "limit": limit,
                        "total": total_filtered,
                        "total_pages": (total_filtered + limit - 1) // limit if total_filtered else 1
                    }
                })
                # NOTE: 同步后刷新网页需拿到最新产品列表，禁止缓存
                resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
                resp.headers['Pragma'] = 'no-cache'
                # CHANGE: 方案 A - 仅显示图片在 D:\Ya Subio 内存在的产品
                resp.headers['X-Image-Logic'] = 'only-products-with-image-in-dir'
                resp.headers['X-Image-Match-Count'] = str(sum(1 for p in paginated_products if p.get('image_path')))
                resp.headers['X-Image-File-Count'] = str(len(_files_ya_subio))
                return resp
                
            except Exception as e:
                logger.error(f"❌ 获取产品列表失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/products/<product_id>', methods=['GET'])
        def get_product(product_id):
            """获取产品详情（SQLite + PostgreSQL Cristy 回退）。CHANGE: 支持 10060_Al/10060_A 等 URL 与 DB 10060/10060._AI 多候选匹配；支持 Telegram 展示码 18bf4405 通过映射解析。"""
            try:
                if not self.db and USE_SQLITE_FOR_PRODUCTS:
                    return jsonify({"error": "数据库未连接"}), 500
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
                    return jsonify({"error": "产品不存在"}), 404
                
                # CHANGE: 转换图片路径为URL - 处理所有可能的路径格式；统一去掉文件名方括号与 D:\Ya Subio 实际文件名一致
                image_path = product.get('image_path', '')
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
                    elif os.path.isabs(image_path):
                        filename = _normalize_image_filename(os.path.basename(image_path))
                        image_path = f'/api/images/{filename}'
                    elif '\\' in image_path or '/' in image_path:
                        filename = _normalize_image_filename(os.path.basename(image_path))
                        image_path = f'/api/images/{filename}'
                    elif image_path and not image_path.startswith('http'):
                        image_path = f'/api/images/{_normalize_image_filename(image_path)}'
                # CHANGE: 与列表一致，单一逻辑「根据图片名称查找」+ product_id
                _ya = PWA_YA_SUBIO_BASE
                if os.path.isdir(_ya):
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
                        'category': product.get('category_id', 'default'),
                        'product_code': product.get('product_code', ''),
                        'codigo_proveedor': (product.get('codigo_proveedor') or '').strip()
                    }
                })
            
            except Exception as e:
                logger.error(f"❌ 获取产品详情失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart', methods=['GET'])
        def get_cart():
            """获取购物车"""
            try:
                # CHANGE: 优先从认证token获取user_id，如果没有则从参数获取
                user_id = None
                if hasattr(request, 'user_id') and getattr(request, 'user_id', None):
                    user_id = getattr(request, 'user_id', None)
                    logger.info(f"📥 API获取购物车请求: 从token获取user_id={user_id}")
                else:
                    user_id_str = request.args.get('user_id')
                    if user_id_str:
                        try:
                            user_id = int(user_id_str)
                        except (ValueError, TypeError):
                            logger.warning(f"⚠️ user_id格式错误: {user_id_str}，返回空购物车")
                            return jsonify({
                                "success": True,
                                "data": []
                            })
                
                if not user_id or user_id <= 0:
                    logger.info("📥 API获取购物车请求: 无user_id，返回空购物车")
                    return jsonify({
                        "success": True,
                        "data": []
                    })
                
                if not self.cart_manager:
                    logger.error("❌ 购物车管理器未可用")
                    return jsonify({"error": "购物车管理器未可用"}), 500
                
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
                if cart:
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
                    return jsonify({"error": "请求体为空"}), 400
                
                # CHANGE: 优先从认证token获取user_id
                user_id = None
                if hasattr(request, 'user_id') and getattr(request, 'user_id', None):
                    user_id = getattr(request, 'user_id', None)
                else:
                    user_id = data.get('user_id')
                
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
                
                if not user_id or not product_id:
                    return jsonify({"error": "缺少必要参数"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "购物车管理器未可用"}), 500
                
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
                    return jsonify({"error": "添加失败，请检查产品ID是否正确"}), 500
                
            except Exception as e:
                logger.error(f"❌ 添加到购物车失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/update', methods=['POST'])
        def update_cart():
            """更新购物车商品数量"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "请求体为空"}), 400
                user_id = data.get('user_id')
                product_id = data.get('product_id')
                quantity = data.get('quantity')
                unit_price = data.get('price')
                if unit_price is not None:
                    try:
                        unit_price = float(unit_price)
                    except (ValueError, TypeError):
                        unit_price = None
                
                if not user_id or not product_id or quantity is None:
                    return jsonify({"error": "缺少必要参数"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "购物车管理器未可用"}), 500
                
                success = self.cart_manager.update_quantity(user_id, product_id, quantity, unit_price=unit_price)
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": "购物车已更新"
                    })
                else:
                    return jsonify({"error": "更新失败"}), 500
                
            except Exception as e:
                logger.error(f"❌ 更新购物车失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/remove', methods=['POST'])
        def remove_from_cart():
            """从购物车移除商品"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "请求体为空"}), 400
                user_id = data.get('user_id')
                product_id = data.get('product_id')
                
                if not user_id or not product_id:
                    return jsonify({"error": "缺少必要参数"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "购物车管理器未可用"}), 500
                
                success = self.cart_manager.remove_from_cart(user_id, product_id)
                
                if success:
                    return jsonify({
                        "success": True,
                        "message": "商品已从购物车移除"
                    })
                else:
                    return jsonify({"error": "移除失败"}), 500
                
            except Exception as e:
                logger.error(f"❌ 从购物车移除失败: {e}")
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/cart/clear', methods=['POST'])
        def clear_cart():
            """清空购物车"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"error": "请求体为空"}), 400
                user_id = data.get('user_id')
                
                if not user_id:
                    return jsonify({"error": "缺少user_id参数"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "购物车管理器未可用"}), 500
                
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
            """计算购物车总价"""
            try:
                user_id = request.args.get('user_id', type=int)
                if not user_id:
                    return jsonify({"error": "缺少user_id参数"}), 400
                
                if not self.cart_manager:
                    return jsonify({"error": "购物车管理器未可用"}), 500
                
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
                    return jsonify({"error": "请求体为空"}), 400
                # CHANGE: 便于确认前端是否发送 subtotal/total（PEDIDOS=CARRITO）
                logger.info(f"📦 [checkout] 请求体含 subtotal={data.get('subtotal')}, total={data.get('total')}")
                print(f"📦 [checkout] 请求体含 subtotal={data.get('subtotal')}, total={data.get('total')}")
                
                # CHANGE: 优先从认证token获取user_id
                user_id = None
                if hasattr(request, 'user_id') and getattr(request, 'user_id', None):
                    user_id = getattr(request, 'user_id', None)
                    logger.info(f"📦 从token获取user_id: {user_id}")
                else:
                    user_id = data.get('user_id')
                    logger.info(f"📦 从请求体获取user_id: {user_id}")
                
                customer_info = data.get('customer_info', {})  # CHANGE: 获取客户信息
                
                logger.info(f"📦 收到订单提交请求: user_id={user_id}, type={type(user_id)}")
                logger.info(f"👤 客户信息: {json.dumps(customer_info, ensure_ascii=False) if customer_info else '无'}")
                
                if not user_id:
                    logger.error("❌ 缺少user_id参数")
                    return jsonify({"error": "缺少user_id参数，请先登录"}), 400
                
                # CHANGE: 验证客户信息
                if not customer_info:
                    logger.error("❌ 缺少客户信息")
                    return jsonify({"error": "缺少客户信息"}), 400
                
                required_fields = ['cedula', 'nombres', 'direccion', 'provincia', 'ciudad', 'whatsapp']
                for field in required_fields:
                    if not customer_info.get(field):
                        logger.error(f"❌ 客户信息缺少必填字段: {field}")
                        return jsonify({"error": f"客户信息缺少必填字段: {field}"}), 400
                
                # 确保user_id是整数类型
                try:
                    user_id = int(user_id)
                except (ValueError, TypeError) as e:
                    logger.error(f"❌ user_id类型转换失败: {user_id}, error={e}")
                    return jsonify({"error": f"user_id必须是整数: {user_id}"}), 400
                
                if not self.cart_manager or not self.db:
                    logger.error("❌ 服务未可用: cart_manager={}, db={}".format(
                        self.cart_manager is not None, self.db is not None))
                    return jsonify({"error": "服务未可用"}), 500
                
                # 获取购物车
                cart = self.cart_manager.get_user_cart(user_id)
                logger.info(f"🛒 获取购物车: user_id={user_id}, 商品数={len(cart) if cart else 0}")
                
                if not cart or len(cart) == 0:
                    logger.warning(f"⚠️ 购物车是空的: user_id={user_id}")
                    return jsonify({
                        "success": False,
                        "error": "购物车是空的，请先添加商品",
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
                        "error": "购物车总价无效，请检查商品数据",
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
                        "error": "创建订单失败: 订单ID为空",
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
            """获取订单列表"""
            try:
                # CHANGE: 优先从认证token获取user_id
                user_id = None
                if hasattr(request, 'user_id') and getattr(request, 'user_id', None):
                    user_id = getattr(request, 'user_id', None)
                else:
                    user_id_str = request.args.get('user_id')
                    if not user_id_str:
                        return jsonify({"error": "缺少user_id参数，请先登录"}), 400
                    try:
                        user_id = int(user_id_str)
                    except (ValueError, TypeError):
                        return jsonify({"error": f"user_id必须是整数: {user_id_str}"}), 400
                
                if not user_id or user_id <= 0:
                    return jsonify({"error": f"user_id必须是正整数: {user_id}"}), 400
                
                if not self.db:
                    return jsonify({"error": "数据库未连接"}), 500
                
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
            """获取订单详情"""
            try:
                # CHANGE: 优先从认证token获取user_id
                user_id = None
                if hasattr(request, 'user_id') and getattr(request, 'user_id', None):
                    user_id = getattr(request, 'user_id', None)
                else:
                    user_id_str = request.args.get('user_id')
                    if not user_id_str:
                        return jsonify({"error": "缺少user_id参数，请先登录"}), 400
                    try:
                        user_id = int(user_id_str)
                    except (ValueError, TypeError):
                        return jsonify({"error": f"user_id必须是整数: {user_id_str}"}), 400
                
                if not self.db:
                    return jsonify({"error": "数据库未连接"}), 500
                
                order_detail = self.db.get_order_detail(order_id, user_id)
                
                if not order_detail:
                    return jsonify({"error": "订单不存在或无权限访问"}), 404
                
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
        
        @self.app.route('/api/payment/bank-info', methods=['GET'])
        def get_bank_info():
            """获取转账信息"""
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

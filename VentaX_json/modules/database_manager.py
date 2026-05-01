import sqlite3
import json
import os
import re
import logging
from datetime import datetime

# CHANGE: 先初始化logger，避免在导入时使用未定义的logger
logger = logging.getLogger(__name__)

# CHANGE: 导入统一的订单ID生成函数
try:
    from utils import generate_unified_order_id
    # CHANGE: 验证导入的函数是否正确
    import inspect
    if hasattr(generate_unified_order_id, '__code__'):
        # 检查函数签名
        sig = inspect.signature(generate_unified_order_id)
        logger.info(f"✅ generate_unified_order_id导入成功，参数: {sig}")
        print(f"✅ generate_unified_order_id导入成功，参数: {sig}")
    else:
        raise ImportError("generate_unified_order_id不是有效函数")
except ImportError as e:
    # CHANGE: 如果导入失败，记录详细错误并使用本地函数（向后兼容）
    logger.error(f"❌ 导入generate_unified_order_id失败: {e}")
    print(f"❌ 导入generate_unified_order_id失败: {e}")
    import traceback
    logger.error(f"❌ 导入错误堆栈:\n{traceback.format_exc()}")
    print(f"❌ 导入错误堆栈:\n{traceback.format_exc()}")
    # 如果导入失败，使用本地函数（向后兼容）
    def generate_unified_order_id(source_prefix, user_id):
        invoice_num = f"{str(user_id)[-6:]:0>9}"
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"{source_prefix}_{invoice_num}_{timestamp}"
    logger.warning("⚠️ 使用本地fallback函数生成订单ID")
    print("⚠️ 使用本地fallback函数生成订单ID")

class DatabaseManager:
    """数据库管理类"""
    
    def __init__(self):
        self.logger = logger
        # 使用绝对路径，避免路径问题
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # CHANGE: 优先从网页文件夹(pwa_cart)读取数据库，然后从database文件夹读取
        # 1. 优先检查网页文件夹中的数据库（同步副本）
        pwa_cart_db_path = os.path.join(base_dir, 'pwa_cart', 'spanish_product_database.db')
        pwa_cart_db_path = os.path.abspath(pwa_cart_db_path)
        
        # 2. 检查database文件夹中的数据库（原始数据库）
        spanish_db_path = os.path.join(base_dir, '..', 'database', 'spanish_product_database.db')
        spanish_db_path = os.path.abspath(spanish_db_path)
        
        # 3. 备用数据库
        enhanced_db_path = os.path.join(base_dir, '..', 'database', 'enhanced_product_database.db')
        enhanced_db_path = os.path.abspath(enhanced_db_path)
        
        # 优先使用网页文件夹中的数据库，然后是database文件夹，最后是备用数据库
        if os.path.exists(pwa_cart_db_path):
            self.db_path = pwa_cart_db_path
            self.use_spanish_db = True
            self.logger.info(f"📁 DatabaseManager初始化: 使用网页文件夹中的spanish_product_database.db（同步副本）")
        elif os.path.exists(spanish_db_path):
            self.db_path = spanish_db_path
            self.use_spanish_db = True
            self.logger.info(f"📁 DatabaseManager初始化: 使用database文件夹中的spanish_product_database.db（ventaX_unified_system97的数据库）")
        else:
            self.db_path = enhanced_db_path
            self.use_spanish_db = False
            self.logger.info(f"📁 DatabaseManager初始化: 使用enhanced_product_database.db（备用数据库）")
        
        self.db_path = os.path.abspath(self.db_path)  # 转换为绝对路径
        self.logger.info(f"📁 数据库路径={self.db_path}")
        self._init_database()
        
    def _init_database(self):
        """初始化数据库"""
        try:
            # 确保数据库目录存在
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 创建产品表（匹配主程序的表结构）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_code TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    category TEXT DEFAULT '其他',
                    description TEXT,
                    price_unidad REAL DEFAULT 0.0,
                    price_mayor REAL DEFAULT 0.0,
                    price_bulto REAL DEFAULT 0.0,
                    image_path TEXT,
                    original_filename TEXT,
                    original_text TEXT,
                    processed_text TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    stock INTEGER DEFAULT 999,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    original_price_unidad REAL DEFAULT 0.0,
                    original_price_mayor REAL DEFAULT 0.0,
                    original_price_bulto REAL DEFAULT 0.0,
                    all_original_prices TEXT DEFAULT '[]',
                    all_processed_prices TEXT DEFAULT '[]',
                    price_increase_rate REAL DEFAULT 1.20,
                    price_rounding_applied BOOLEAN DEFAULT 0,
                    price_groups_count INTEGER DEFAULT 1,
                    default_price_group TEXT DEFAULT 'Producto 1'
                )
            ''')
            
            # 创建分类表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # CHANGE: 创建用户表（支持邮箱和谷歌OAuth注册）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE,
                    password_hash TEXT,
                    google_id TEXT UNIQUE,
                    name TEXT,
                    avatar_url TEXT,
                    registration_method TEXT DEFAULT 'email',
                    email_verified BOOLEAN DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            ''')
            
            # CHANGE: 如果表已存在但没有新字段，添加这些字段
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN google_id TEXT UNIQUE")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN registration_method TEXT DEFAULT 'email'")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")
            except sqlite3.OperationalError:
                pass
            # CHANGE: 忘记密码 - 重置 token 及过期时间
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN password_reset_token TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN password_reset_expires TIMESTAMP")
            except sqlite3.OperationalError:
                pass
            
            # 创建用户购物车表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_carts (
                    user_id INTEGER,
                    product_id TEXT,
                    quantity INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, product_id)
                )
            ''')
            
            # 创建订单表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    total_amount REAL NOT NULL,
                    status TEXT DEFAULT 'pending',
                    customer_info TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # CHANGE: 如果表已存在但没有customer_info字段，添加该字段
            try:
                cursor.execute("ALTER TABLE orders ADD COLUMN customer_info TEXT")
                self.logger.info("✅ 已添加customer_info字段到orders表")
            except sqlite3.OperationalError:
                # 字段已存在，忽略错误
                pass
            
            # 创建订单详情表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS order_items (
                    order_id TEXT,
                    product_id TEXT,
                    quantity INTEGER,
                    price REAL,
                    PRIMARY KEY (order_id, product_id),
                    FOREIGN KEY (order_id) REFERENCES orders (id)
                )
            ''')
            
            conn.commit()
            conn.close()
            
            self.logger.info("✅ 数据库初始化成功")
            
        except Exception as e:
            self.logger.error(f"❌ 数据库初始化失败: {e}")
    
    
    def get_all_products(self):
        """获取所有产品 - 支持多规格价格"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # CHANGE: 根据数据库类型选择不同的SQL查询
            if self.use_spanish_db:
                # spanish_product_database.db 使用西班牙语字段名
                # CHANGE: 添加 codigo_proveedor 和 fecha_creacion 字段到查询中
                # CHANGE: 添加 inventario 用于 ULTIMO 栏按库存自行下架
                cursor.execute("""
                    SELECT codigo_producto, nombre_producto, precio_unidad, precio_mayor, precio_bulto,
                           precio_original_unidad, precio_original_mayor, precio_original_bulto,
                           todos_precios_procesados, cantidad_grupos_precios, grupo_precio_defecto,
                           ruta_imagen, texto_original, texto_procesado, channel_username, codigo_proveedor,
                           fecha_creacion, inventario
                    FROM products 
                    WHERE codigo_producto IS NOT NULL AND codigo_producto != ''
                      AND esta_activo = 1
                    ORDER BY fecha_creacion DESC
                """)
            else:
                # enhanced_product_database.db 使用英语字段名
                cursor.execute("""
                    SELECT product_code, product_name, price_unidad, price_mayor, price_bulto,
                           original_price_unidad, original_price_mayor, original_price_bulto,
                           all_processed_prices, price_groups_count, default_price_group,
                           image_path, original_text, processed_text, NULL as channel_username
                    FROM products 
                    WHERE product_code IS NOT NULL AND product_code != ''
                """)
            
            rows = cursor.fetchall()
            
            products = {}
            for row in rows:
                if self.use_spanish_db:
                    product_code = row[0]  # codigo_producto
                    product_name = row[1]  # nombre_producto
                    price_unidad = row[2]  # precio_unidad
                    price_mayor = row[3]  # precio_mayor
                    price_bulto = row[4]  # precio_bulto
                    original_price_unidad = row[5]  # precio_original_unidad
                    original_price_mayor = row[6]  # precio_original_mayor
                    original_price_bulto = row[7]  # precio_original_bulto
                    all_processed_prices_str = row[8]  # todos_precios_procesados
                    price_groups_count = row[9]  # cantidad_grupos_precios
                    default_price_group = row[10]  # grupo_precio_defecto
                    image_path = row[11]  # ruta_imagen
                    original_text = row[12]  # texto_original
                    processed_text = row[13]  # texto_procesado
                    channel_username = row[14]  # channel_username
                    codigo_proveedor = row[15] if len(row) > 15 else None  # CHANGE: codigo_proveedor
                    fecha_creacion = row[16] if len(row) > 16 else None  # CHANGE: fecha_creacion
                    inventario = row[17] if len(row) > 17 else 0  # CHANGE: inventario（库存，Cristy 按此下架）
                else:
                    product_code = row[0]  # product_code
                    product_name = row[1]  # product_name
                    price_unidad = row[2]  # price_unidad
                    price_mayor = row[3]  # price_mayor
                    price_bulto = row[4]  # price_bulto
                    original_price_unidad = row[5]  # original_price_unidad
                    original_price_mayor = row[6]  # original_price_mayor
                    original_price_bulto = row[7]  # original_price_bulto
                    all_processed_prices_str = row[8]  # all_processed_prices
                    price_groups_count = row[9]  # price_groups_count
                    default_price_group = row[10]  # default_price_group
                    image_path = row[11]  # image_path
                    original_text = row[12]  # original_text
                    processed_text = row[13]  # processed_text
                    channel_username = row[14]  # channel_username (可能为NULL)
                    codigo_proveedor = None  # CHANGE: 非西班牙语数据库可能没有此字段
                    inventario = 999  # 英语库默认有库存
                
                # 解析多价格组数据
                try:
                    all_processed_prices = json.loads(all_processed_prices_str or '[]')
                except:
                    all_processed_prices = []
                
                price_groups_count = price_groups_count or 1
                default_price_group = default_price_group or 'Producto 1'
                
                # 价格：price 固定为单价(precio_unidad)，供 PWA 等按数量 1-2 单价/3-11 批发/12+ 批量 正确取价；无单价时再回退链
                _unit = (price_unidad if (price_unidad is not None and price_unidad > 0) else None) or (price_mayor if (price_mayor is not None and price_mayor > 0) else None) or (price_bulto if (price_bulto is not None and price_bulto > 0) else None)
                _wholesale = price_mayor if (price_mayor is not None and price_mayor > 0) else (1.00)
                _bulk = price_bulto if (price_bulto is not None and price_bulto > 0) else (0.80)
                # CHANGE: price 必须为单价，避免旧逻辑“默认批发价”导致 1-2 件仍显示批发价
                _price_unidad_only = price_unidad if (price_unidad is not None and price_unidad > 0) else None
                product_info = {
                    'id': product_code,
                    'name': product_name or f'Producto {product_code}',
                    'price': _price_unidad_only if _price_unidad_only is not None else (_unit if _unit else 1.20),
                    'wholesale_price': _wholesale,
                    'bulk_price': _bulk,
                    'original_price_unidad': original_price_unidad or 0.0,
                    'original_price_mayor': original_price_mayor or 0.0,
                    'original_price_bulto': original_price_bulto or 0.0,
                    'all_processed_prices': all_processed_prices,
                    'price_groups_count': price_groups_count,
                    'default_price_group': default_price_group,
                    'description': processed_text or original_text or f'产品代码: {product_code}',
                    'category_id': 'default',
                    'image_path': image_path,
                    'created_at': fecha_creacion if self.use_spanish_db else '2025-09-21',  # CHANGE: 使用真实的创建日期
                    'channel_username': channel_username,
                    'codigo_proveedor': codigo_proveedor if self.use_spanish_db else None,  # CHANGE: 添加供应商代码
                    'stock': inventario if self.use_spanish_db else 999  # CHANGE: 库存，Cristy 按此下架；英语库默认 999
                }
                
                products[product_code] = product_info
            
            # CHANGE: 同时以数字 id 为 key 映射到同一产品，便于购物车用 id（如 1558）查到并得到 product_code（Y99）与真实名称
            try:
                cursor.execute("SELECT id, product_code FROM products WHERE product_code IS NOT NULL AND product_code != ''")
                for r in cursor.fetchall():
                    iid, pcode = r[0], r[1]
                    if pcode and str(iid) and products.get(pcode):
                        products[str(iid)] = products[pcode]
            except Exception:
                try:
                    cursor.execute("SELECT id_producto, codigo_producto FROM products WHERE codigo_producto IS NOT NULL AND codigo_producto != ''")
                    for r in cursor.fetchall():
                        iid, pcode = r[0], r[1]
                        if pcode and str(iid) and products.get(pcode):
                            products[str(iid)] = products[pcode]
                except Exception:
                    pass
            
            conn.close()
            db_name = 'spanish_product_database.db' if self.use_spanish_db else 'enhanced_product_database.db'
            self.logger.info(f"✅ 从{db_name}加载了 {len(products)} 个产品")
            return products
            
        except Exception as e:
            self.logger.error(f"❌ 获取产品失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {}
    
    def get_categories(self):
        """获取所有分类"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM categories')
            rows = cursor.fetchall()
            
            categories = {}
            for row in rows:
                categories[row[0]] = row[1]
            
            conn.close()
            return categories
            
        except Exception as e:
            self.logger.error(f"❌ 获取分类失败: {e}")
            return {}
    
    def get_product(self, product_id):
        """获取单个产品。支持按 codigo_producto/product_code 或 id_producto/id 查询（其他供应商可能用 id 当 code）。"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            pid_str = str(product_id).strip() if product_id is not None else ""
            if self.use_spanish_db:
                cursor.execute('SELECT * FROM products WHERE codigo_producto = ?', (pid_str,))
            else:
                cursor.execute('SELECT * FROM products WHERE product_code = ?', (pid_str,))
            row = cursor.fetchone()
            # CHANGE: 按代码未找到且入参为数字时，尝试按 id 查（如 1851 为 id_producto，codigo 为 XE82）
            if row is None and pid_str and pid_str.isdigit():
                try:
                    pid_num = int(pid_str)
                    if self.use_spanish_db:
                        cursor.execute('SELECT * FROM products WHERE id_producto = ?', (pid_num,))
                    else:
                        cursor.execute('SELECT * FROM products WHERE id = ?', (pid_num,))
                    row = cursor.fetchone()
                except Exception:
                    pass
            conn.close()
            
            if row:
                # CHANGE: 根据数据库类型构建产品信息
                if self.use_spanish_db:
                    # spanish_product_database.db 字段顺序（根据PRAGMA table_info）
                    # id_producto, codigo_producto, nombre_producto, descripcion, precio_unidad, ...
                    return {
                        'id': row[1],  # codigo_producto
                        'name': row[2],  # nombre_producto
                        'price': row[4] or 1.20,  # precio_unidad
                        'wholesale_price': row[5] or 1.00,  # precio_mayor
                        'bulk_price': row[6] or 0.80,  # precio_bulto
                        'description': row[3] or f'产品代码: {row[1]}',  # descripcion
                        'category_id': row[7] or 'default',  # categoria
                        'image_path': row[8],  # ruta_imagen
                        'created_at': row[13] or '2025-09-21'  # fecha_creacion
                    }
                else:
                    # enhanced_product_database.db 字段顺序
                    return {
                        'id': row[1],  # product_code
                        'name': row[2],  # product_name
                        'price': row[5] or 1.20,  # price_unidad
                        'wholesale_price': row[6] or 1.00,  # price_mayor
                        'bulk_price': row[7] or 0.80,  # price_bulto
                        'description': row[4] or f'产品代码: {row[1]}',
                        'category_id': row[3] or 'default',
                        'image_path': row[8],
                        'created_at': '2025-09-21'
                    }
            
            return None
            
        except Exception as e:
            self.logger.error(f"❌ 获取产品失败: {e}")
            return None
    
    def get_product_image(self, product_id):
        """获取产品图片路径"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT image_path FROM products WHERE id = ?', (product_id,))
            row = cursor.fetchone()
            
            conn.close()
            
            if row and row[0]:
                return row[0]
            
            return None
            
        except Exception as e:
            self.logger.error(f"❌ 获取产品图片失败: {e}")
            return None
    
    def get_user_cart(self, user_id):
        """获取用户购物车"""
        try:
            self.logger.info(f"📥 开始获取购物车: user_id={user_id}, 数据库路径: {self.db_path}")
            self.logger.info(f"📥 数据库文件存在: {os.path.exists(self.db_path)}")
            
            # 使用check_same_thread=False，避免多线程问题
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
            # 设置WAL模式，改善并发性能
            try:
                conn.execute('PRAGMA journal_mode = WAL')
            except:
                pass  # 如果WAL模式不支持，忽略
            # 设置超时时间，避免数据库锁定
            conn.execute('PRAGMA busy_timeout = 10000')
            # 确保读取最新数据
            conn.execute('PRAGMA read_uncommitted = 1')
            cursor = conn.cursor()
            
            # 先检查user_carts表中是否有记录
            cursor.execute('SELECT COUNT(*) FROM user_carts WHERE user_id = ?', (user_id,))
            cart_count = cursor.fetchone()[0]
            self.logger.info(f"📊 user_carts表中记录数: {cart_count}")
            
            if cart_count == 0:
                conn.close()
                self.logger.info(f"✅ 获取购物车成功: user_id={user_id}, 返回 0 个商品（购物车为空）")
                return []
            
            # 初始化购物车列表
            cart = []
            
            # 直接查询user_carts表，然后手动获取产品信息（更可靠的方法，避免JOIN查询问题）
            cursor.execute('SELECT product_id, quantity FROM user_carts WHERE user_id = ?', (user_id,))
            direct_rows = cursor.fetchall()
            self.logger.info(f"📋 直接查询user_carts返回 {len(direct_rows)} 条记录")
            
            if len(direct_rows) == 0:
                self.logger.warning(f"⚠️ 警告: cart_count={cart_count}，但直接查询返回0条记录！")
                conn.close()
                return []
            
            # 获取所有产品信息
            products = self.get_all_products()
            self.logger.info(f"📦 产品字典大小: {len(products)}")
            
            # 手动构建购物车数据
            for row in direct_rows:
                product_id = row[0]
                quantity = row[1]
                self.logger.info(f"  📦 处理商品: product_id={product_id}, quantity={quantity}")
                
                # 从products字典中查找产品信息
                product = products.get(str(product_id))
                if not product:
                    # 尝试字符串匹配
                    for key, value in products.items():
                        if str(key) == str(product_id):
                            product = value
                            self.logger.info(f"  ✅ 通过字符串匹配找到产品: {key}")
                            break
                if not product:
                    # CHANGE: product_id 可能为 TG_JUGUETESFANG_90174 等形式，products 以数字 id 为 key；用数字部分再查
                    nums = re.findall(r'\d+', str(product_id))
                    for n in reversed(nums):  # 优先靠后的数字（如 90174）
                        if products.get(str(n)):
                            product = products[str(n)]
                            self.logger.info(f"  ✅ 通过数字部分找到产品: product_id={product_id} -> key={n}")
                            break
                
                if product:
                    # CHANGE: 增加 code（展示用产品代码，如 Y99），与 Sistema Factura 一致
                    display_code = product.get('id', product_id)  # product_info['id'] 即 product_code
                    cart.append({
                        'product_id': str(product_id),
                        'code': str(display_code) if display_code else str(product_id),
                        'name': product.get('name', f'Producto {product_id}'),
                        'price': float(product.get('price', 0)),
                        'quantity': int(quantity) if quantity else 0
                    })
                    self.logger.info(f"  ✅ 找到产品信息: code={display_code}, name={product.get('name')}, price={product.get('price')}")
                else:
                    # 产品不存在，使用默认值
                    self.logger.warning(f"⚠️ 产品不存在于products表: product_id={product_id}")
                    cart.append({
                        'product_id': str(product_id),
                        'code': str(product_id),
                        'name': f'Producto {product_id}',
                        'price': 0.0,
                        'quantity': int(quantity) if quantity else 0
                    })
            
            conn.close()
            self.logger.info(f"✅ 获取购物车成功: user_id={user_id}, 返回 {len(cart)} 个商品")
            if cart:
                self.logger.info(f"✅ 购物车商品列表: {[item.get('product_id') for item in cart]}")
            else:
                self.logger.warning(f"⚠️ 警告: user_carts表中有 {cart_count} 条记录，但返回的购物车为空！")
            return cart
            
        except Exception as e:
            self.logger.error(f"❌ 获取购物车失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []
    
    def save_user_cart(self, user_id, cart):
        """保存用户购物车"""
        try:
            self.logger.info(f"💾 开始保存购物车: user_id={user_id}, 商品数={len(cart)}")
            if cart:
                self.logger.info(f"💾 购物车内容: {[item.get('product_id') for item in cart]}")
            
            # 使用check_same_thread=False，避免多线程问题
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
            # 设置WAL模式，改善并发性能
            try:
                conn.execute('PRAGMA journal_mode = WAL')
            except:
                pass  # 如果WAL模式不支持，忽略
            # 设置超时时间，避免数据库锁定
            conn.execute('PRAGMA busy_timeout = 10000')
            # 确保立即写入
            conn.execute('PRAGMA synchronous = NORMAL')
            cursor = conn.cursor()
            
            # 清空现有购物车
            cursor.execute('DELETE FROM user_carts WHERE user_id = ?', (user_id,))
            deleted_count = cursor.rowcount
            self.logger.info(f"🗑️ 清空购物车: 删除了 {deleted_count} 条记录")
            
            # 添加新商品
            inserted_count = 0
            for item in cart:
                product_id = item.get('product_id')
                quantity = item.get('quantity', 1)
                self.logger.info(f"  ➕ 添加商品: product_id={product_id}, quantity={quantity}")
                try:
                    cursor.execute('''
                        INSERT INTO user_carts (user_id, product_id, quantity)
                        VALUES (?, ?, ?)
                    ''', (user_id, str(product_id), int(quantity)))
                    inserted_count += 1
                except Exception as e:
                    self.logger.error(f"❌ 插入商品失败: product_id={product_id}, error={e}")
                    raise
            
            # 提交事务
            conn.commit()
            self.logger.info(f"✅ 事务已提交: user_id={user_id}, 插入了 {inserted_count} 条记录")
            
            # 立即同步到磁盘
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            
            # 验证保存是否成功（使用新的连接，确保读取到最新数据）
            verify_conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
            verify_cursor = verify_conn.cursor()
            verify_cursor.execute('SELECT COUNT(*) FROM user_carts WHERE user_id = ?', (user_id,))
            verify_count = verify_cursor.fetchone()[0]
            verify_conn.close()
            self.logger.info(f"✅ 保存后验证（新连接）: user_carts表中记录数={verify_count}")
            
            conn.close()
            self.logger.info(f"✅ 购物车保存成功: user_id={user_id}, 插入了 {inserted_count} 条记录")
            
        except Exception as e:
            self.logger.error(f"❌ 保存购物车失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise  # 重新抛出异常，让调用者知道保存失败
    
    def create_order(self, user_id, cart_items, total_amount, customer_info=None):
        """创建订单"""
        conn = None
        try:
            # CHANGE: 使用统一的订单ID生成函数（与 ventax_customer_bot_pedidos8.pyw 一致）
            # 格式：ORD_{invoice_num}_{YYYYMMDD}_{HHMMSS}
            # invoice_num: 从user_id的后6位生成，不足9位前面补0
            order_id = None
            try:
                # CHANGE: 强制重新导入utils模块，确保使用最新代码
                import importlib
                import sys
                if 'utils' in sys.modules:
                    importlib.reload(sys.modules['utils'])
                from utils import generate_unified_order_id as generate_new
                order_id = generate_new("ORD", user_id)
                self.logger.info(f"✅ 使用重新导入的函数生成订单ID: {order_id}")
                print(f"✅ 使用重新导入的函数生成订单ID: {order_id}")
            except Exception as reload_error:
                self.logger.warning(f"⚠️ 重新导入失败，使用原函数: {reload_error}")
                print(f"⚠️ 重新导入失败，使用原函数: {reload_error}")
                # 如果重新导入失败，使用原来的函数
                order_id = generate_unified_order_id("ORD", user_id)
            
            # CHANGE: 验证订单ID格式，确保使用新格式（4部分：ORD_invoice_num_YYYYMMDD_HHMMSS）
            # invoice_num: 9位数字，从user_id的后6位生成，不足9位前面补0
            parts = order_id.split('_')
            if len(parts) != 4:
                error_msg = f"❌❌❌ 订单ID格式错误: {order_id} (部分数: {len(parts)}, 应该是4部分: ORD_invoice_num_YYYYMMDD_HHMMSS)"
                self.logger.error(error_msg)
                print(error_msg)  # 控制台输出
                # CHANGE: 如果格式错误，强制使用正确格式重新生成（使用generate_invoice_num）
                try:
                    from datetime import datetime
                    from utils import generate_invoice_num
                    invoice_num = generate_invoice_num(user_id)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    order_id = f"ORD_{invoice_num}_{timestamp}"
                    self.logger.info(f"✅ 强制修正订单ID格式: {order_id}")
                    print(f"✅ 强制修正订单ID格式: {order_id}")
                except Exception as fix_error:
                    self.logger.error(f"❌ 强制修正订单ID失败: {fix_error}")
                    print(f"❌ 强制修正订单ID失败: {fix_error}")
                    import traceback
                    self.logger.error(f"❌ 错误堆栈:\n{traceback.format_exc()}")
                    raise RuntimeError(f"无法生成正确格式的订单ID: {order_id}") from fix_error
            else:
                # CHANGE: 验证订单ID格式是否正确（简化验证，不要求user_id是9位数字）
                if len(parts[2]) != 8 or not parts[2].isdigit():
                    self.logger.warning(f"⚠️ 订单ID的日期格式可能不正确: {parts[2]} (应该是8位数字YYYYMMDD)")
                    print(f"⚠️ 订单ID的日期格式可能不正确: {parts[2]} (应该是8位数字YYYYMMDD)")
                if len(parts[3]) != 6 or not parts[3].isdigit():
                    self.logger.warning(f"⚠️ 订单ID的时间格式可能不正确: {parts[3]} (应该是6位数字HHMMSS)")
                    print(f"⚠️ 订单ID的时间格式可能不正确: {parts[3]} (应该是6位数字HHMMSS)")
                self.logger.info(f"✅ 订单ID格式验证通过: {order_id}")
                print(f"✅ 订单ID格式验证通过: {order_id}")
            
            self.logger.info(f"📝 开始创建订单: order_id={order_id}, user_id={user_id} (type={type(user_id)}), total_amount={total_amount} (type={type(total_amount)})")
            self.logger.info(f"📦 购物车商品数: {len(cart_items)}")
            self.logger.info(f"📦 购物车数据: {cart_items}")
            self.logger.info(f"👤 客户信息: {customer_info if customer_info else '无'}")
            
            # 验证购物车数据
            for idx, item in enumerate(cart_items):
                if not isinstance(item, dict):
                    raise ValueError(f"购物车商品 {idx} 不是字典类型: {type(item)}")
                required_fields = ['product_id', 'quantity', 'price']
                for field in required_fields:
                    if field not in item:
                        raise ValueError(f"购物车商品 {idx} 缺少必需字段: {field}, 商品数据: {item}")
                self.logger.info(f"  📦 商品 {idx}: product_id={item.get('product_id')}, quantity={item.get('quantity')}, price={item.get('price')}")
            
            # 确保数据库表存在
            self._init_database()
            
            # 使用check_same_thread=False，避免多线程问题
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
            # 设置WAL模式，改善并发性能
            try:
                conn.execute('PRAGMA journal_mode = WAL')
            except:
                pass  # 如果WAL模式不支持，忽略
            # 设置超时时间，避免数据库锁定
            conn.execute('PRAGMA busy_timeout = 10000')
            # 注意：SQLite默认不启用外键约束，但在同一事务中插入数据时不需要外键约束
            # 暂时禁用外键约束，避免可能的约束检查问题
            # conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.cursor()
            
            # 验证表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('orders', 'order_items')")
            tables = [row[0] for row in cursor.fetchall()]
            if 'orders' not in tables or 'order_items' not in tables:
                raise RuntimeError(f"数据库表不存在: orders={('orders' in tables)}, order_items={('order_items' in tables)}")
            
            # 创建订单
            # 确保数据类型正确
            user_id_int = int(user_id) if user_id else None
            total_amount_float = float(total_amount) if total_amount else 0.0
            # CHANGE: 将客户信息转换为JSON字符串
            customer_info_json = json.dumps(customer_info, ensure_ascii=False) if customer_info else None
            
            self.logger.info(f"💾 插入订单记录: order_id={order_id}, user_id={user_id_int} (type={type(user_id_int)}), total_amount={total_amount_float} (type={type(total_amount_float)}), customer_info={customer_info_json}")
            try:
                cursor.execute('''
                    INSERT INTO orders (id, user_id, total_amount, customer_info)
                    VALUES (?, ?, ?, ?)
                ''', (order_id, user_id_int, total_amount_float, customer_info_json))
                self.logger.info(f"✅ 订单记录插入成功")
            except Exception as insert_error:
                self.logger.error(f"❌ 插入订单记录失败: {insert_error}")
                raise
            
            # 创建订单详情
            self.logger.info(f"💾 插入订单详情: {len(cart_items)} 个商品")
            for idx, item in enumerate(cart_items):
                product_id = str(item['product_id'])
                quantity = int(item['quantity'])
                price = float(item['price'])
                
                self.logger.info(f"  💾 插入订单详情 {idx}: order_id={order_id}, product_id={product_id}, quantity={quantity}, price={price}")
                try:
                    cursor.execute('''
                        INSERT INTO order_items (order_id, product_id, quantity, price)
                        VALUES (?, ?, ?, ?)
                    ''', (order_id, product_id, quantity, price))
                    self.logger.info(f"  ✅ 订单详情 {idx} 插入成功")
                except Exception as item_error:
                    self.logger.error(f"  ❌ 插入订单详情 {idx} 失败: {item_error}")
                    raise
            
            # CHANGE: 先保存到 unified_orders 表，成功后再提交主订单表，确保数据一致性
            try:
                self.logger.info(f"📝 准备保存订单到unified_orders表: order_id={order_id}")
                print(f"📝 准备保存订单到unified_orders表: order_id={order_id}")  # 控制台输出
                self._save_to_unified_orders(order_id, user_id, cart_items, total_amount, customer_info)
                self.logger.info(f"✅ 订单已成功保存到unified_orders表: {order_id}")
                print(f"✅ 订单已成功保存到unified_orders表: {order_id}")  # 控制台输出
            except Exception as unified_error:
                # CHANGE: 如果保存到unified_orders失败，记录详细错误并回滚主订单表，确保数据一致性
                error_msg = str(unified_error)
                error_type = type(unified_error).__name__
                self.logger.error(f"❌❌❌ 保存到unified_orders表失败: {error_msg}")
                self.logger.error(f"❌❌❌ 错误类型: {error_type}")
                print(f"❌❌❌ 保存到unified_orders表失败: {error_msg}")  # 控制台输出
                print(f"❌❌❌ 错误类型: {error_type}")  # 控制台输出
                import traceback
                error_traceback = traceback.format_exc()
                self.logger.error(f"❌❌❌ 错误堆栈:\n{error_traceback}")
                print(f"❌❌❌ 错误堆栈:\n{error_traceback}")  # 控制台输出
                # CHANGE: 记录关键信息以便调试
                self.logger.error(f"❌❌❌ 订单信息: order_id={order_id}, user_id={user_id}, total_amount={total_amount}")
                print(f"❌❌❌ 订单信息: order_id={order_id}, user_id={user_id}, total_amount={total_amount}")  # 控制台输出
                # CHANGE: 回滚主订单表，确保数据一致性
                if conn:
                    conn.rollback()
                    self.logger.error(f"❌❌❌ 已回滚主订单表，确保数据一致性")
                    print(f"❌❌❌ 已回滚主订单表，确保数据一致性")  # 控制台输出
                # CHANGE: 重新抛出异常，让调用者知道保存失败（这会导致订单创建失败，确保数据一致性）
                raise RuntimeError(f"保存订单到unified_orders表失败: {error_msg}") from unified_error
            
            # CHANGE: 只有在unified_orders表保存成功后才提交主订单表
            conn.commit()
            self.logger.info(f"✅ 订单创建成功: order_id={order_id}")
            
            return order_id
            
        except KeyError as e:
            error_msg = f"创建订单失败 - 缺少必需字段: {e}"
            self.logger.error(f"❌ {error_msg}")
            import traceback
            self.logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            raise ValueError(error_msg) from e
        except ValueError as e:
            error_msg = f"创建订单失败 - 数据验证错误: {e}"
            self.logger.error(f"❌ {error_msg}")
            import traceback
            self.logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            raise ValueError(error_msg) from e
        except sqlite3.Error as e:
            error_msg = f"创建订单失败 - 数据库错误: {e}"
            self.logger.error(f"❌ {error_msg}")
            import traceback
            self.logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            raise RuntimeError(error_msg) from e
        except Exception as e:
            error_msg = f"创建订单失败 - 未知错误: {e}"
            self.logger.error(f"❌ {error_msg}")
            import traceback
            self.logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            raise RuntimeError(error_msg) from e
        finally:
            if conn:
                conn.close()
    
    def _save_to_unified_orders(self, order_id, user_id, cart_items, total_amount, customer_info):
        """保存订单到 unified_orders 表，以便 purchaser_notification_manager_gui.pyw 可以访问"""
        try:
            self.logger.info(f"📝 开始保存订单到unified_orders表: order_id={order_id}")
            # 尝试导入共享数据库
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            shared_db_path = os.path.join(base_dir, 'Sistema Factura', 'shared_database.py')
            
            self.logger.info(f"🔍 共享数据库路径: {shared_db_path}")
            if not os.path.exists(shared_db_path):
                # NOTE: 云部署（如 Render）无 Sistema Factura 目录时跳过 unified_orders，仅保存到本地 orders/order_items
                self.logger.warning(f"⚠️ 共享数据库文件不存在（已跳过 unified_orders）: {shared_db_path}")
                return
            
            import importlib.util
            spec = importlib.util.spec_from_file_location("shared_database", shared_db_path)
            if not spec or not spec.loader:
                error_msg = "无法创建共享数据库模块规范"
                self.logger.error(f"❌ {error_msg}")
                raise RuntimeError(error_msg)
            
            shared_db_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(shared_db_module)
            db = shared_db_module.get_shared_database()
            
            if not db:
                error_msg = "无法获取共享数据库实例"
                self.logger.error(f"❌ {error_msg}")
                raise RuntimeError(error_msg)
            
            # CHANGE: 从订单ID中提取invoice_num，生成comprobante
            # 订单ID格式（统一后）：
            # - 新格式：ORD_{invoice_num}_{YYYYMMDD}_{HHMMSS} (parts长度=4, parts[1]是9位数字invoice_num)
            #   invoice_num: 从user_id的后6位生成，不足9位前面补0
            # - 旧格式：ORD_{YYYYMMDD}{HHMMSS}_{user_id} (parts长度=3, parts[1]是14位数字YYYYMMDDHHMMSS)
            # - 旧格式（简化）：ORD_{user_id}_{YYYYMMDD}_{HHMMSS} (parts长度=4, parts[1]是user_id，未补0)
            parts = order_id.split('_')
            self.logger.info(f"🔍 解析订单ID: {order_id}, parts={parts}, len={len(parts)}")
            print(f"🔍 解析订单ID: {order_id}, parts={parts}, len={len(parts)}")
            
            if len(parts) >= 4:
                # 新格式：ORD_{invoice_num}_{YYYYMMDD}_{HHMMSS}（统一后格式）
                # 或旧格式：ORD_{user_id}_{YYYYMMDD}_{HHMMSS}（简化格式，未补0）
                invoice_num_from_order = parts[1]
                # CHANGE: 如果parts[1]是9位数字，这是统一后的invoice_num格式
                if invoice_num_from_order.isdigit() and len(invoice_num_from_order) == 9:
                    # 这是统一后的格式：ORD_{invoice_num}_{YYYYMMDD}_{HHMMSS}
                    invoice_num = invoice_num_from_order
                    self.logger.info(f"✅ 统一格式订单ID，invoice_num={invoice_num}")
                    print(f"✅ 统一格式订单ID，invoice_num={invoice_num}")
                else:
                    # 这是旧格式（简化格式），parts[1]是user_id，需要转换为invoice_num
                    invoice_num = f"{str(invoice_num_from_order)[-6:]:0>9}"  # 为了comprobante，仍需要9位格式
                    self.logger.info(f"✅ 旧格式订单ID（简化），user_id={invoice_num_from_order}, invoice_num={invoice_num}")
                    print(f"✅ 旧格式订单ID（简化），user_id={invoice_num_from_order}, invoice_num={invoice_num}")
            elif len(parts) == 3:
                # 可能是旧格式：ORD_{YYYYMMDD}{HHMMSS}_{user_id} 或 ORD_{YYYYMMDD}{HHMMSS}_{sequence}
                # 检查parts[1]是否是14位数字（YYYYMMDDHHMMSS格式）
                if parts[1].isdigit() and len(parts[1]) == 14:
                    # 旧格式：从user_id生成invoice_num（使用user_id的后6位，补0到9位）
                    invoice_num = f"{str(user_id)[-6:]:0>9}"
                    self.logger.warning(f"⚠️ 检测到旧格式订单ID: {order_id}，从user_id生成invoice_num: {invoice_num}")
                    print(f"⚠️ 检测到旧格式订单ID: {order_id}，从user_id生成invoice_num: {invoice_num}")
                else:
                    # 可能是其他格式，尝试从user_id生成
                    invoice_num = f"{str(user_id)[-6:]:0>9}"
                    self.logger.warning(f"⚠️ 无法识别订单ID格式: {order_id}，从user_id生成invoice_num: {invoice_num}")
                    print(f"⚠️ 无法识别订单ID格式: {order_id}，从user_id生成invoice_num: {invoice_num}")
            elif len(parts) == 2:
                # 可能是格式：ORD_{YYYYMMDD}{HHMMSS}，需要从user_id生成invoice_num
                invoice_num = f"{str(user_id)[-6:]:0>9}"
                self.logger.warning(f"⚠️ 订单ID只有2部分: {order_id}，从user_id生成invoice_num: {invoice_num}")
                print(f"⚠️ 订单ID只有2部分: {order_id}，从user_id生成invoice_num: {invoice_num}")
            else:
                # 默认：从user_id生成invoice_num
                invoice_num = f"{str(user_id)[-6:]:0>9}"
                self.logger.warning(f"⚠️ 订单ID格式异常: {order_id}，从user_id生成invoice_num: {invoice_num}")
                print(f"⚠️ 订单ID格式异常: {order_id}，从user_id生成invoice_num: {invoice_num}")
            
            comprobante = f"001-002-{invoice_num}"
            self.logger.info(f"📝 生成的comprobante: {comprobante}")
            print(f"📝 生成的comprobante: {comprobante}")
            
            # CHANGE: 计算运费和小计
            # PWA的total_amount是商品小计（不包含运费），需要加上运费才是总价
            SHIPPING_COST = 8.00
            subtotal = float(total_amount)  # total_amount是商品小计
            shipping = SHIPPING_COST
            total_with_shipping = subtotal + shipping  # 总价 = 小计 + 运费
            
            # CHANGE: 转换cart_items格式；优先使用 cart 内已有且非占位名的 code/name（来自 PG 或 get_user_cart），避免被 SQLite 过期数据覆盖
            # PWA格式: {'product_id', 'code', 'name', 'price', 'quantity'}
            def _is_generic_name(n):
                if not n or not (n or '').strip():
                    return True
                u = (n or '').strip().upper()
                if u in ('PRODUCTO DESCONOCIDO', '未知产品', 'PRODUCTO', 'PRODUCT', 'PRODUCTO NUEVO', 'PRODUCTO NUEVO '):
                    return True
                # CHANGE: 凡以 "PRODUCTO " 开头的均视为占位名（含 PRODUCTO COD XEI4、PRODUCTO X29 等），便于用 SQLite/PG 解析真实名称
                if u.startswith('PRODUCTO '):
                    return True
                return False
            products = self.get_all_products()
            formatted_cart_items = []
            for item in cart_items:
                pid = str(item.get('product_id', item.get('code', item.get('id', '')))).strip() or ''
                item_code = str(item.get('code', item.get('product_id', item.get('id', '')))).strip() or pid
                item_name = (item.get('name') or '').strip()
                # CHANGE: 若 cart 已有有效 code/name（如 PG 补全的），直接采用，不覆盖为 SQLite
                if item_code and not _is_generic_name(item_name):
                    product_code = item_code
                    product_name = item_name or product_code
                else:
                    product = products.get(pid)
                    if not product:
                        for k, v in products.items():
                            if str(k) == str(pid):
                                product = v
                                break
                    if not product and pid:
                        nums = re.findall(r'\d+', pid)
                        for n in reversed(nums):
                            if products.get(str(n)):
                                product = products[str(n)]
                                break
                    if product:
                        product_code = str(product.get('id', pid) or pid)
                        resolved_name = (product.get('name') or '').strip() or product_code
                        product_name = resolved_name if not _is_generic_name(resolved_name) else (item_name or product_code)
                    else:
                        product_code = item_code or pid
                        product_name = item_name if not _is_generic_name(item_name) else product_code
                
                # CHANGE: 确保数据类型正确
                try:
                    quantity = float(item.get('quantity', 0))
                    price = float(item.get('price', 0))
                except (ValueError, TypeError):
                    self.logger.warning(f"⚠️ 商品数据格式错误: {item}，使用默认值")
                    quantity = 0.0
                    price = 0.0
                
                formatted_item = {
                    'code': (product_code.upper() if product_code else ''),
                    'name': (product_name.upper() if product_name else ''),
                    'quantity': quantity,
                    'price': price,
                }
                formatted_cart_items.append(formatted_item)
                self.logger.debug(f"  📦 格式化商品: code={formatted_item['code']}, name={formatted_item['name']}, quantity={quantity}, price={price}")
            
            # CHANGE: 构建订单数据，确保所有字段都正确设置
            order_data = {
                'order_id': order_id,
                'source': 'pwa',  # CHANGE: PWA订单的source
                'user_id': str(user_id),
                'nota': None,  # PWA订单没有nota
                'comprobante': comprobante,
                'customer_info': customer_info or {},
                'cart_items': formatted_cart_items,
                'subtotal': float(subtotal),  # CHANGE: 确保是float类型
                'shipping': float(shipping),  # CHANGE: 确保是float类型
                'total': float(total_with_shipping),  # CHANGE: 总价 = 小计 + 运费，确保是float类型
                'status': 'pending',  # CHANGE: PWA订单默认状态为pending
                'pdf_path': None,
            }
            
            # CHANGE: 验证订单数据完整性
            required_fields = ['order_id', 'source', 'user_id', 'comprobante', 'cart_items', 'subtotal', 'shipping', 'total', 'status']
            for field in required_fields:
                if field not in order_data:
                    raise ValueError(f"订单数据缺少必需字段: {field}")
            
            # CHANGE: 验证cart_items不为空
            if not order_data['cart_items'] or len(order_data['cart_items']) == 0:
                raise ValueError("订单商品列表为空")
            
            # CHANGE: 验证金额数据
            if order_data['subtotal'] < 0 or order_data['shipping'] < 0 or order_data['total'] < 0:
                raise ValueError(f"订单金额数据无效: subtotal={order_data['subtotal']}, shipping={order_data['shipping']}, total={order_data['total']}")
            
            # CHANGE: 保存到unified_orders表
            self.logger.info(f"💾 调用save_unified_order保存订单: order_id={order_id}")
            print(f"💾 调用save_unified_order保存订单: order_id={order_id}")  # 控制台输出
            self.logger.info(f"💾 订单数据摘要: source={order_data['source']}, user_id={order_data['user_id']}, comprobante={order_data['comprobante']}")
            print(f"💾 订单数据摘要: source={order_data['source']}, user_id={order_data['user_id']}, comprobante={order_data['comprobante']}")  # 控制台输出
            self.logger.info(f"💾 订单金额: subtotal={order_data['subtotal']}, shipping={order_data['shipping']}, total={order_data['total']}")
            print(f"💾 订单金额: subtotal={order_data['subtotal']}, shipping={order_data['shipping']}, total={order_data['total']}")  # 控制台输出
            self.logger.info(f"💾 商品数量: {len(order_data['cart_items'])}")
            print(f"💾 商品数量: {len(order_data['cart_items'])}")  # 控制台输出
            
            # CHANGE: 保存订单，带重试机制
            max_retries = 3
            retry_delay = 0.2
            last_error = None
            for attempt in range(max_retries):
                try:
                    db.save_unified_order(order_data)
                    self.logger.info(f"✅ PWA订单已成功保存到unified_orders表: order_id={order_id} (尝试 {attempt + 1}/{max_retries})")
                    # CHANGE: 同时写入 self.db_path 的 unified_orders，保证 get_user_orders（PWA 订单列表）读到与 CARRITO 一致的 total
                    try:
                        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        local_conn = sqlite3.connect(self.db_path)
                        local_cur = local_conn.cursor()
                        local_cur.execute('''
                            CREATE TABLE IF NOT EXISTS unified_orders (
                                order_id TEXT PRIMARY KEY,
                                user_id TEXT,
                                subtotal REAL,
                                shipping REAL,
                                total REAL,
                                status TEXT,
                                created_at TEXT,
                                updated_at TEXT
                            )
                        ''')
                        local_cur.execute('''
                            INSERT OR REPLACE INTO unified_orders (order_id, user_id, subtotal, shipping, total, status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            order_data['order_id'],
                            str(order_data['user_id']),
                            float(order_data['subtotal']),
                            float(order_data['shipping']),
                            float(order_data['total']),
                            order_data.get('status', 'pending'),
                            now, now
                        ))
                        local_conn.commit()
                        local_conn.close()
                        self.logger.info(f"✅ PWA订单已同步到本地 unified_orders (db_path): order_id={order_id}, total={order_data['total']}")
                    except Exception as local_err:
                        self.logger.warning(f"⚠️ 同步到本地 unified_orders 失败（不影响主流程）: {local_err}")
                    break
                except Exception as save_error:
                    last_error = save_error
                    error_msg = str(save_error)
                    error_type = type(save_error).__name__
                    self.logger.error(f"❌❌❌ 保存订单失败 (尝试 {attempt + 1}/{max_retries}): {error_msg}")
                    self.logger.error(f"❌❌❌ 错误类型: {error_type}")
                    print(f"❌❌❌ 保存订单失败 (尝试 {attempt + 1}/{max_retries}): {error_msg}")  # 控制台输出
                    print(f"❌❌❌ 错误类型: {error_type}")  # 控制台输出
                    import traceback
                    error_traceback = traceback.format_exc()
                    self.logger.error(f"❌❌❌ 错误堆栈:\n{error_traceback}")
                    print(f"❌❌❌ 错误堆栈:\n{error_traceback}")  # 控制台输出
                    
                    if attempt < max_retries - 1:
                        self.logger.warning(f"⚠️ {retry_delay}秒后重试...")
                        print(f"⚠️ {retry_delay}秒后重试...")  # 控制台输出
                        import time
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    else:
                        self.logger.error(f"❌❌❌ 保存订单最终失败 (尝试 {attempt + 1}/{max_retries}): {error_msg}")
                        print(f"❌❌❌ 保存订单最终失败 (尝试 {attempt + 1}/{max_retries}): {error_msg}")  # 控制台输出
                        raise
            
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            self.logger.error(f"❌❌❌ 保存PWA订单到unified_orders表失败: {error_msg}")
            self.logger.error(f"❌❌❌ 错误类型: {error_type}")
            print(f"❌❌❌ 保存PWA订单到unified_orders表失败: {error_msg}")  # 控制台输出
            print(f"❌❌❌ 错误类型: {error_type}")  # 控制台输出
            import traceback
            error_traceback = traceback.format_exc()
            self.logger.error(f"❌❌❌ 错误堆栈:\n{error_traceback}")
            print(f"❌❌❌ 错误堆栈:\n{error_traceback}")  # 控制台输出
            self.logger.error(f"❌❌❌ 订单信息: order_id={order_id}, user_id={user_id}, total_amount={total_amount}")
            print(f"❌❌❌ 订单信息: order_id={order_id}, user_id={user_id}, total_amount={total_amount}")  # 控制台输出
            # CHANGE: 重新抛出异常，让调用者知道保存失败（这会导致订单创建失败，确保数据一致性）
            raise
    
    def get_orders_for_sync(self):
        """获取所有订单，用于云端→本地同步；返回与 save_unified_order 一致的 order_data 列表。"""
        SHIPPING_COST = 8.00
        orders_out = []
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('orders', 'order_items')"
            )
            tables = [row[0] for row in cursor.fetchall()]
            if 'orders' not in tables or 'order_items' not in tables:
                self.logger.warning("⚠️ get_orders_for_sync: orders 或 order_items 表不存在")
                return orders_out
            # NOTE: 不依赖 created_at，兼容 Render 上可能无该列的旧 schema
            cursor.execute(
                "SELECT id, user_id, total_amount, customer_info, COALESCE(status, 'pending') FROM orders ORDER BY id ASC"
            )
            rows = cursor.fetchall()
            for row in rows:
                order_id, user_id, total_amount, customer_info_json, status = row
                cursor.execute(
                    "SELECT product_id, quantity, price FROM order_items WHERE order_id = ? ORDER BY product_id",
                    (order_id,)
                )
                items = cursor.fetchall()
                cart_items = []
                for pid, qty, price in items:
                    # CHANGE: 用 product_id 查产品名称，保证同步到本地后 ITEM 显示名称而非 ID
                    product_name = str(pid)
                    try:
                        prod = self.get_product(str(pid))
                        if prod and prod.get('name'):
                            product_name = (prod.get('name') or '').strip() or str(pid)
                    except Exception:
                        pass
                    cart_items.append({
                        'product_id': str(pid),
                        'code': str(pid),
                        'name': product_name,
                        'quantity': int(qty),
                        'price': float(price),
                    })
                try:
                    customer_info = json.loads(customer_info_json) if customer_info_json else {}
                except Exception:
                    customer_info = {}
                parts = (order_id or '').split('_')
                invoice_num = parts[1] if len(parts) >= 2 and parts[1].isdigit() else f"{str(user_id)[-6:]:0>9}"
                comprobante = f"001-002-{invoice_num}"
                subtotal = float(total_amount or 0)
                shipping = SHIPPING_COST
                total = subtotal + shipping
                order_data = {
                    'order_id': order_id,
                    'source': 'pwa',
                    'user_id': str(user_id),
                    'nota': None,
                    'comprobante': comprobante,
                    'customer_info': customer_info,
                    'cart_items': cart_items,
                    'subtotal': subtotal,
                    'shipping': shipping,
                    'total': total,
                    'status': status or 'pending',
                    'pdf_path': None,
                }
                orders_out.append(order_data)
            self.logger.info(f"📋 get_orders_for_sync: 共 {len(orders_out)} 条订单")
        except Exception as e:
            self.logger.error(f"❌ get_orders_for_sync 失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
        finally:
            if conn:
                conn.close()
        return orders_out
    
    def get_user_orders(self, user_id):
        """获取用户订单列表 - CHANGE: 优先从 shared_db.unified_orders 读取（与写入一致），保证 PEDIDOS total 与 CARRITO 一致"""
        try:
            # CHANGE: 优先从 shared_db 读取（PWA 订单写入此处），保证 total 与结账时一致
            try:
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                shared_db_path = os.path.join(base_dir, 'Sistema Factura', 'shared_database.py')
                if os.path.exists(shared_db_path):
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("shared_database", shared_db_path)
                    if spec and spec.loader:
                        sm = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(sm)
                        db = sm.get_shared_database()
                        if db and getattr(db, 'orders_adapter', None):
                            prefix = getattr(db, 'orders_table_prefix', '') or ''
                            tbl = f"{prefix}unified_orders" if prefix else "unified_orders"
                            rows = db.orders_adapter.fetchall(
                                f"SELECT order_id, subtotal, shipping, total, status, created_at FROM {tbl} WHERE user_id = %s ORDER BY created_at DESC",
                                (str(user_id),)
                            )
                            if rows is not None and len(rows) > 0:
                                orders = []
                                for r in rows:
                                    st = float(r.get('subtotal') or 0)
                                    sh = float(r.get('shipping') or 8.0)
                                    t = float(r.get('total') or 0) or (st + sh)
                                    orders.append({
                                        'id': r.get('order_id'),
                                        'total_amount': t,
                                        'status': (r.get('status') or 'pending'),
                                        'created_at': r.get('created_at')
                                    })
                                self.logger.info(f"📋 [get_user_orders] 从 shared_db 读取 {len(orders)} 条，保证 PEDIDOS=CARRITO")
                                return orders
            except Exception as shared_err:
                self.logger.debug(f"📋 [get_user_orders] 从 shared_db 读取失败，回退到 db_path: {shared_err}")

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='unified_orders'")
            has_unified_orders = cursor.fetchone() is not None
            self.logger.info(f"📋 [get_user_orders] user_id={user_id}, has_unified_orders={has_unified_orders}")
            orders = []
            if has_unified_orders:
                # CHANGE: 从unified_orders表读取订单（总价已经包含运费）
                # unified_orders表的user_id是TEXT类型，需要转换为字符串匹配
                # CHANGE: 尝试多种匹配方式，确保能够正确查询
                user_id_str = str(user_id)
                self.logger.info(f"📋 [get_user_orders] 查询unified_orders表: user_id={user_id_str}")
                
                # CHANGE: 先尝试直接字符串匹配（因为user_id在unified_orders表中是TEXT类型）
                cursor.execute('''
                    SELECT order_id, subtotal, shipping, total, status, created_at
                    FROM unified_orders
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                ''', (user_id_str,))
                
                rows = cursor.fetchall()
                self.logger.info(f"📋 [get_user_orders] 从unified_orders表查询到 {len(rows)} 条记录")
                
                # CHANGE: 如果直接匹配没有结果，尝试使用CAST（兼容性处理）
                if len(rows) == 0:
                    self.logger.info(f"📋 [get_user_orders] 直接匹配无结果，尝试使用CAST匹配")
                    cursor.execute('''
                        SELECT order_id, subtotal, shipping, total, status, created_at
                        FROM unified_orders
                        WHERE CAST(user_id AS TEXT) = ?
                        ORDER BY created_at DESC
                    ''', (user_id_str,))
                    rows = cursor.fetchall()
                    self.logger.info(f"📋 [get_user_orders] 使用CAST匹配查询到 {len(rows)} 条记录")
                
                for row in rows:
                    order_id = row[0]
                    subtotal = float(row[1]) if row[1] is not None else 0.0
                    shipping = float(row[2]) if row[2] is not None else 8.00  # 默认运费8.00
                    total = float(row[3]) if row[3] is not None else (subtotal + shipping)
                    status = row[4] if row[4] else 'pending'
                    created_at = row[5]
                    
                    # CHANGE: 验证总价是否正确（总价 = 小计 + 运费）
                    expected_total = subtotal + shipping
                    if abs(total - expected_total) > 0.01:
                        self.logger.warning(f"⚠️ 订单 {order_id} 总价不一致: total={total}, expected={expected_total}，使用计算后的总价")
                        total = expected_total
                    
                    self.logger.info(f"📋 [get_user_orders] 订单 {order_id}: subtotal={subtotal}, shipping={shipping}, total={total}")
                    
                    orders.append({
                        'id': order_id,
                        'total_amount': total,  # CHANGE: 使用unified_orders表中的总价（已包含运费）
                        'status': status,
                        'created_at': created_at
                    })
            else:
                # CHANGE: 如果unified_orders表不存在，从orders表读取并计算总价
                cursor.execute('''
                    SELECT id, total_amount, status, created_at
                    FROM orders
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                ''', (user_id,))
                
                rows = cursor.fetchall()
                
                # CHANGE: 运费常量
                SHIPPING_COST = 8.00
                
                for row in rows:
                    order_id = row[0]
                    db_total_amount = row[1]
                    status = row[2]
                    created_at = row[3]
                    
                    # CHANGE: 计算订单商品小计，然后加上运费得到正确的总价
                    # 查询订单商品
                    cursor.execute('''
                        SELECT quantity, price
                        FROM order_items
                        WHERE order_id = ?
                    ''', (order_id,))
                    
                    items_rows = cursor.fetchall()
                    # CHANGE: 计算商品小计（确保使用正确的价格）
                    subtotal = 0.0
                    for quantity, price in items_rows:
                        item_subtotal = float(quantity) * float(price)
                        subtotal += item_subtotal
                        self.logger.debug(f"  订单 {order_id}: quantity={quantity}, price={price}, item_subtotal={item_subtotal}")
                    
                    # 正确的总价 = 商品小计 + 运费
                    correct_total = subtotal + SHIPPING_COST
                    self.logger.debug(f"  订单 {order_id}: subtotal={subtotal}, shipping={SHIPPING_COST}, total={correct_total}")
                    
                    orders.append({
                        'id': order_id,
                        'total_amount': correct_total,  # CHANGE: 使用计算后的正确总价
                        'status': status,
                        'created_at': created_at
                    })
            
            conn.close()
            return orders
            
        except Exception as e:
            self.logger.error(f"❌ 获取订单列表失败: {e}")
            return []
    
    def get_order_detail(self, order_id, user_id=None):
        """获取订单详情（包括订单项） - CHANGE: 优先从 shared_db.unified_orders 读取，确保 PEDIDOS 列表与详情一致"""
        try:
            # CHANGE: 先从 shared_db 读取（与 get_user_orders 一致），避免“列表可见但详情404”
            try:
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                shared_db_path = os.path.join(base_dir, 'Sistema Factura', 'shared_database.py')
                if os.path.exists(shared_db_path):
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("shared_database", shared_db_path)
                    if spec and spec.loader:
                        sm = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(sm)
                        db = sm.get_shared_database()
                        if db and getattr(db, 'orders_adapter', None):
                            prefix = getattr(db, 'orders_table_prefix', '') or ''
                            tbl = f"{prefix}unified_orders" if prefix else "unified_orders"

                            if user_id is not None:
                                row = db.orders_adapter.fetchone(
                                    f"SELECT order_id, user_id, subtotal, shipping, total, status, created_at, cart_items FROM {tbl} WHERE order_id = %s AND user_id = %s LIMIT 1",
                                    (str(order_id), str(user_id))
                                )
                            else:
                                row = db.orders_adapter.fetchone(
                                    f"SELECT order_id, user_id, subtotal, shipping, total, status, created_at, cart_items FROM {tbl} WHERE order_id = %s LIMIT 1",
                                    (str(order_id),)
                                )

                            if row:
                                subtotal = float(row.get('subtotal') or 0)
                                shipping = float(row.get('shipping') or 8.0)
                                total = float(row.get('total') or 0) or (subtotal + shipping)
                                status = row.get('status') or 'pending'
                                created_at = row.get('created_at')
                                cart_items_raw = row.get('cart_items') or []
                                try:
                                    import json
                                    cart_items = json.loads(cart_items_raw) if isinstance(cart_items_raw, str) else list(cart_items_raw)
                                except Exception:
                                    cart_items = []

                                items = []
                                for item in cart_items:
                                    product_id = str(item.get('code', item.get('product_id', item.get('id', ''))))
                                    quantity = float(item.get('quantity', 0) or 0)
                                    price = float(item.get('price', 0) or 0)
                                    items.append({
                                        'product_id': product_id,
                                        'name': item.get('name', product_id),
                                        'quantity': quantity,
                                        'price': price,
                                        'subtotal': price * quantity
                                    })

                                self.logger.info(f"📋 [get_order_detail] 从 shared_db 读取订单: order_id={order_id}")
                                return {
                                    'order_id': row.get('order_id'),
                                    'user_id': row.get('user_id'),
                                    'total_amount': total,
                                    'subtotal': subtotal,
                                    'shipping': shipping,
                                    'status': status,
                                    'created_at': created_at,
                                    'items': items
                                }
            except Exception as shared_err:
                self.logger.debug(f"📋 [get_order_detail] 从 shared_db 读取失败，回退到 db_path: {shared_err}")

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # CHANGE: 优先从unified_orders表读取订单详情
            # 检查unified_orders表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='unified_orders'")
            has_unified_orders = cursor.fetchone() is not None
            
            order_detail = None
            if has_unified_orders:
                # CHANGE: 从unified_orders表读取订单详情
                if user_id:
                    cursor.execute('''
                        SELECT order_id, user_id, subtotal, shipping, total, status, created_at, cart_items
                        FROM unified_orders
                        WHERE order_id = ? AND CAST(user_id AS TEXT) = ?
                    ''', (order_id, str(user_id)))
                else:
                    cursor.execute('''
                        SELECT order_id, user_id, subtotal, shipping, total, status, created_at, cart_items
                        FROM unified_orders
                        WHERE order_id = ?
                    ''', (order_id,))
                
                unified_row = cursor.fetchone()
                if unified_row:
                    # CHANGE: 从unified_orders表读取的数据
                    order_id_from_db = unified_row[0]
                    user_id_from_db = unified_row[1]
                    subtotal = float(unified_row[2]) if unified_row[2] is not None else 0.0
                    shipping = float(unified_row[3]) if unified_row[3] is not None else 8.00
                    total = float(unified_row[4]) if unified_row[4] is not None else (subtotal + shipping)
                    status = unified_row[5] if unified_row[5] else 'pending'
                    created_at = unified_row[6]
                    cart_items_json = unified_row[7] if unified_row[7] else '[]'
                    
                    # CHANGE: 验证总价是否正确（总价 = 小计 + 运费）
                    expected_total = subtotal + shipping
                    if abs(total - expected_total) > 0.01:
                        self.logger.warning(f"⚠️ 订单 {order_id} 总价不一致: total={total}, expected={expected_total}，使用计算后的总价")
                        total = expected_total
                    
                    # CHANGE: 解析cart_items JSON
                    try:
                        import json
                        cart_items = json.loads(cart_items_json) if isinstance(cart_items_json, str) else cart_items_json
                    except:
                        cart_items = []
                    
                    # CHANGE: 获取产品信息
                    items = []
                    products = self.get_all_products()
                    for item in cart_items:
                        product_id = str(item.get('code', item.get('product_id', item.get('id', ''))))
                        quantity = float(item.get('quantity', 0))
                        price = float(item.get('price', 0))
                        
                        product_info = products.get(product_id, {})
                        items.append({
                            'product_id': product_id,
                            'name': item.get('name', product_info.get('name', product_id)),
                            'quantity': quantity,
                            'price': price,
                            'subtotal': price * quantity
                        })
                    
                    order_detail = {
                        'order_id': order_id_from_db,
                        'user_id': user_id_from_db,
                        'total_amount': total,  # CHANGE: 使用unified_orders表中的总价（已包含运费）
                        'subtotal': subtotal,  # CHANGE: 添加小计字段
                        'shipping': shipping,  # CHANGE: 添加运费字段
                        'status': status,
                        'created_at': created_at,
                        'items': items
                    }
                    self.logger.info(f"📋 [get_order_detail] 从unified_orders表读取订单: order_id={order_id}, subtotal={subtotal}, shipping={shipping}, total={total}")
            
            # CHANGE: 如果unified_orders表不存在或没有找到订单，从orders表读取（兼容性处理）
            if not order_detail:
                # 查询订单基本信息
                if user_id:
                    cursor.execute('''
                        SELECT id, user_id, total_amount, status, created_at
                        FROM orders
                        WHERE id = ? AND user_id = ?
                    ''', (order_id, user_id))
                else:
                    cursor.execute('''
                        SELECT id, user_id, total_amount, status, created_at
                        FROM orders
                        WHERE id = ?
                    ''', (order_id,))
                
                order_row = cursor.fetchone()
                if not order_row:
                    conn.close()
                    return None
                
                # 查询订单项
                cursor.execute('''
                    SELECT product_id, quantity, price
                    FROM order_items
                    WHERE order_id = ?
                    ORDER BY product_id
                ''', (order_id,))
                
                items_rows = cursor.fetchall()
                
                # 获取产品信息
                items = []
                products = self.get_all_products()
                subtotal = 0.0
                for item_row in items_rows:
                    product_id = item_row[0]
                    quantity = item_row[1]
                    price = item_row[2]
                    
                    product_info = products.get(product_id, {})
                    item_subtotal = price * quantity
                    subtotal += item_subtotal
                    items.append({
                        'product_id': product_id,
                        'name': product_info.get('name', product_id),
                        'quantity': quantity,
                        'price': price,
                        'subtotal': item_subtotal
                    })
                
                # CHANGE: 计算正确的总价（小计 + 运费）
                SHIPPING_COST = 8.00
                correct_total = subtotal + SHIPPING_COST
                
                order_detail = {
                    'order_id': order_row[0],
                    'user_id': order_row[1],
                    'total_amount': correct_total,  # CHANGE: 使用计算后的正确总价（包含运费）
                    'subtotal': subtotal,  # CHANGE: 添加小计字段
                    'shipping': SHIPPING_COST,  # CHANGE: 添加运费字段
                    'status': order_row[3],
                    'created_at': order_row[4],
                    'items': items
                }
                self.logger.info(f"📋 [get_order_detail] 从orders表读取订单: order_id={order_id}, subtotal={subtotal}, shipping={SHIPPING_COST}, total={correct_total}")
            
            conn.close()
            return order_detail
            
        except Exception as e:
            self.logger.error(f"❌ 获取订单详情失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None

    def get_product_price_groups(self, product_code):
        """获取产品的所有价格组"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 查询价格分组表
            cursor.execute("""
                SELECT group_number, display_name, specification,
                       processed_price_unidad, processed_price_mayor, processed_price_bulto,
                       confidence_score
                FROM price_groups 
                WHERE product_id = (SELECT id FROM products WHERE product_code = ?)
                ORDER BY group_number
            """, (product_code,))
            
            rows = cursor.fetchall()
            conn.close()
            
            price_groups = []
            for row in rows:
                price_groups.append({
                    'group_number': row[0],
                    'display_name': row[1],
                    'specification': row[2],
                    'price_unidad': row[3],
                    'price_mayor': row[4],
                    'price_bulto': row[5],
                    'confidence_score': row[6]
                })
            
            return price_groups
            
        except Exception as e:
            self.logger.error(f"❌ 获取价格组失败: {e}")
            return []
    
    def calculate_dynamic_price(self, product_code, group_number, quantity):
        """计算动态价格 - 支持多规格产品"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 获取指定价格组的价格
            cursor.execute("""
                SELECT processed_price_unidad, processed_price_mayor, processed_price_bulto
                FROM price_groups 
                WHERE product_id = (SELECT id FROM products WHERE product_code = ?)
                AND group_number = ?
            """, (product_code, group_number))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return "Precio No Disponible", 0.0
            
            price_unidad, price_mayor, price_bulto = row
            
            # 价格计算逻辑
            if quantity <= 2:
                return "Precio Por Unidad", price_unidad
            elif quantity <= 11:
                return "Precio Por Mayor", price_mayor
            else:
                return "Precio Por Bulto", price_bulto
                
        except Exception as e:
            self.logger.error(f"❌ 价格计算失败: {e}")
            return "Error", 0.0
    
    # CHANGE: 用户管理方法
    def create_user(self, email=None, password_hash=None, google_id=None, name=None, avatar_url=None, registration_method='email'):
        """创建新用户"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 检查邮箱是否已存在
            if email:
                cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
                if cursor.fetchone():
                    conn.close()
                    return None, "邮箱已被注册"
            
            # 检查谷歌ID是否已存在
            if google_id:
                cursor.execute("SELECT id FROM users WHERE google_id = ?", (google_id,))
                existing = cursor.fetchone()
                if existing:
                    # 如果已存在，更新最后登录时间
                    cursor.execute("""
                        UPDATE users 
                        SET last_login = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (existing[0],))
                    conn.commit()
                    conn.close()
                    return existing[0], None
            
            # 创建新用户
            cursor.execute("""
                INSERT INTO users (email, password_hash, google_id, name, avatar_url, registration_method, email_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (email, password_hash, google_id, name, avatar_url, registration_method, 1 if google_id else 0))
            
            user_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self.logger.info(f"✅ 用户创建成功: user_id={user_id}, email={email}, google_id={google_id}")
            return user_id, None
            
        except Exception as e:
            self.logger.error(f"❌ 创建用户失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None, str(e)
    
    def get_user_by_email(self, email):
        """通过邮箱获取用户（不区分大小写）"""
        try:
            # CHANGE: 确保邮箱是小写的，以便查询
            email = email.strip().lower() if email else ''
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # CHANGE: 使用 LOWER() 函数进行不区分大小写的查询
            cursor.execute("""
                SELECT id, email, password_hash, google_id, name, avatar_url, 
                       registration_method, email_verified, is_active, created_at, last_login
                FROM users WHERE LOWER(email) = LOWER(?)
            """, (email,))
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            return {
                'id': row[0],
                'email': row[1],
                'password_hash': row[2],
                'google_id': row[3],
                'name': row[4],
                'avatar_url': row[5],
                'registration_method': row[6],
                'email_verified': bool(row[7]),
                'is_active': bool(row[8]),
                'created_at': row[9],
                'last_login': row[10]
            }
        except Exception as e:
            self.logger.error(f"❌ 获取用户失败: {e}")
            return None
    
    def get_user_by_google_id(self, google_id):
        """通过谷歌ID获取用户"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, email, password_hash, google_id, name, avatar_url, 
                       registration_method, email_verified, is_active, created_at, last_login
                FROM users WHERE google_id = ?
            """, (google_id,))
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            return {
                'id': row[0],
                'email': row[1],
                'password_hash': row[2],
                'google_id': row[3],
                'name': row[4],
                'avatar_url': row[5],
                'registration_method': row[6],
                'email_verified': bool(row[7]),
                'is_active': bool(row[8]),
                'created_at': row[9],
                'last_login': row[10]
            }
        except Exception as e:
            self.logger.error(f"❌ 获取用户失败: {e}")
            return None
    
    def get_user_by_id(self, user_id):
        """通过ID获取用户"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, email, password_hash, google_id, name, avatar_url, 
                       registration_method, email_verified, is_active, created_at, last_login
                FROM users WHERE id = ?
            """, (user_id,))
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            return {
                'id': row[0],
                'email': row[1],
                'password_hash': row[2],
                'google_id': row[3],
                'name': row[4],
                'avatar_url': row[5],
                'registration_method': row[6],
                'email_verified': bool(row[7]),
                'is_active': bool(row[8]),
                'created_at': row[9],
                'last_login': row[10]
            }
        except Exception as e:
            self.logger.error(f"❌ 获取用户失败: {e}")
            return None
    
    def update_user_last_login(self, user_id):
        """更新用户最后登录时间"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET last_login = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (user_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"❌ 更新最后登录时间失败: {e}")

    # CHANGE: 忘记密码流程
    def create_password_reset_token(self, email, token_hash, expires_at):
        """为用户创建密码重置 token，返回 user_id 或 None"""
        try:
            user = self.get_user_by_email(email)
            if not user:
                return None
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET password_reset_token = ?, password_reset_expires = ?
                WHERE id = ?
            """, (token_hash, expires_at, user['id']))
            conn.commit()
            conn.close()
            return user['id']
        except Exception as e:
            self.logger.error(f"❌ 创建重置 token 失败: {e}")
            return None

    def get_user_by_reset_token(self, token_hash):
        """通过重置 token 获取用户，仅当 token 有效且未过期时返回"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, email, password_hash, name FROM users 
                WHERE password_reset_token = ? AND password_reset_expires > datetime('now')
            """, (token_hash,))
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None
            return {'id': row[0], 'email': row[1], 'password_hash': row[2], 'name': row[3]}
        except Exception as e:
            self.logger.error(f"❌ 查询重置 token 失败: {e}")
            return None

    def update_password_and_clear_reset(self, user_id, password_hash):
        """更新密码并清除重置 token"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET password_hash = ?, password_reset_token = NULL, password_reset_expires = NULL
                WHERE id = ?
            """, (password_hash, user_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            self.logger.error(f"❌ 更新密码失败: {e}")
            return False


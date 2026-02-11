#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VentaX 客户下单Bot - 购物车管理模块
简化版本，专注于购物车功能
"""

import os
import sys
import logging

# CHANGE: telegram 可选，无 telegram 时仅提供 get_user_cart/save_user_cart 等供 PWA API 使用
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = InlineKeyboardButton = InlineKeyboardMarkup = ContextTypes = None

# 添加模块路径
sys.path.append(os.path.dirname(__file__))

logger = logging.getLogger(__name__)

class CartManager:
    """购物车管理类"""
    
    def __init__(self, db=None):
        self.logger = logger
        # 如果提供了db实例，使用它；否则创建新实例
        if db is not None:
            self.db = db
            self.logger.info(f"📁 CartManager使用提供的DatabaseManager实例")
        else:
            from database_manager import DatabaseManager
            self.db = DatabaseManager()
            self.logger.info(f"📁 CartManager创建新的DatabaseManager实例: {self.db.db_path}")
        
    def get_user_cart(self, user_id):
        """获取用户购物车"""
        try:
            self.logger.info(f"📥 CartManager.get_user_cart开始: user_id={user_id}")
            self.logger.info(f"📥 CartManager.db实例: {self.db}")
            self.logger.info(f"📥 CartManager.db路径: {self.db.db_path if self.db else 'N/A'}")
            
            if not self.db:
                self.logger.error("❌ CartManager.db为None！")
                return []
            
            cart = self.db.get_user_cart(user_id)
            self.logger.info(f"📥 CartManager.get_user_cart: user_id={user_id}, 返回 {len(cart)} 个商品")
            if cart:
                self.logger.info(f"📥 购物车内容: {[item.get('product_id') for item in cart]}")
            else:
                self.logger.warning(f"⚠️ CartManager.get_user_cart返回空数组: user_id={user_id}")
            return cart
        except Exception as e:
            self.logger.error(f"❌ 获取购物车失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []
    
    def save_user_cart(self, user_id, cart):
        """保存用户购物车"""
        try:
            self.logger.info(f"💾 CartManager.save_user_cart: user_id={user_id}, 商品数={len(cart)}")
            self.db.save_user_cart(user_id, cart)
            self.logger.info(f"✅ CartManager.save_user_cart 成功")
        except Exception as e:
            self.logger.error(f"❌ 保存购物车失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise  # 重新抛出异常
    
    def add_to_cart(self, user_id, product_id, quantity=1, unit_price=None):
        """添加商品到购物车。unit_price 可选：前端传入（购物车页已按数量层级算好）时直接采用，保证与页面一致。"""
        try:
            product_id = str(product_id) if product_id is not None else None
            quantity = int(quantity) if quantity is not None else 1
            
            # 若前端传入了单价，转为 float 并校验
            client_price = None
            if unit_price is not None:
                try:
                    p = float(unit_price)
                    if p > 0:
                        client_price = p
                        self.logger.info(f"🛒 使用前端传入单价: {client_price}")
                except (ValueError, TypeError):
                    pass
            
            self.logger.info(f"🛒 CartManager.add_to_cart: user_id={user_id}, product_id={product_id}, quantity={quantity}, unit_price={unit_price}")
            
            if not product_id:
                self.logger.error("❌ product_id 为空")
                return False
            
            cart = self.get_user_cart(user_id)
            self.logger.info(f"📋 当前购物车商品数: {len(cart)}")
            if cart:
                self.logger.info(f"📋 当前购物车商品: {[item.get('product_id') for item in cart]}")
            
            # 检查商品是否已在购物车中（使用字符串比较）
            for item in cart:
                item_product_id = str(item.get('product_id', ''))
                if item_product_id == product_id:
                    new_quantity = item['quantity'] + quantity
                    self.logger.info(f"🔄 商品已存在，更新数量: {item['quantity']} + {quantity} = {new_quantity}")
                    # CHANGE: 若有前端单价则沿用；否则按新总量重算
                    if client_price is not None:
                        item['price'] = client_price
                        self.logger.info(f"🛒 合并项沿用前端单价: {client_price}")
                    else:
                        products = self.db.get_all_products()
                        product = self._find_product(products, product_id)
                        if product:
                            item['price'] = self._calculate_price_by_quantity(product, new_quantity)
                    item['quantity'] = new_quantity
                    self.save_user_cart(user_id, cart)
                    # 验证保存是否成功
                    verify_cart = self.get_user_cart(user_id)
                    self.logger.info(f"✅ 保存后验证: 购物车商品数={len(verify_cart)}")
                    return True
            
            # 添加新商品 - 从数据库获取产品信息
            self.logger.info(f"➕ 添加新商品: product_id={product_id}")
            products = self.db.get_all_products()
            self.logger.info(f"📦 数据库中的产品数量: {len(products)}")
            self.logger.info(f"📦 产品ID示例: {list(products.keys())[:5] if products else '无产品'}")
            
            # CHANGE: 使用统一的产品查找（兼容 W7841 / W-7841 等）
            product = self._find_product(products, product_id)
            if product:
                self.logger.info(f"✅ 找到产品: {product_id} -> price={product.get('price')}, bulk_price={product.get('bulk_price')}")
            
            # 如果产品不存在，创建临时产品信息
            if not product:
                self.logger.warning(f"⚠️ 产品不在数据库中，创建临时产品信息: {product_id}")
                product = self._create_temp_product_from_code(product_id)
            
            if product:
                # CHANGE: 优先使用前端传入单价；否则后端按数量算
                if client_price is not None:
                    price_to_save = client_price
                    self.logger.info(f"🛒 新商品使用前端传入单价: {price_to_save}")
                else:
                    price_to_save = self._calculate_price_by_quantity(product, quantity)
                new_item = {
                    'product_id': str(product_id),
                    'name': product.get('name', 'Producto desconocido'),
                    'price': price_to_save,
                    'quantity': int(quantity)
                }
                self.logger.info(f"📦 新商品信息: {new_item}")
                cart.append(new_item)
                self.logger.info(f"💾 准备保存购物车，商品数: {len(cart)}")
                self.save_user_cart(user_id, cart)
                # 验证保存是否成功
                verify_cart = self.get_user_cart(user_id)
                self.logger.info(f"✅ 保存后验证: 购物车商品数={len(verify_cart)}")
                if verify_cart:
                    self.logger.info(f"✅ 验证购物车内容: {[item.get('product_id') for item in verify_cart]}")
                if len(verify_cart) == 0:
                    self.logger.error(f"❌ 保存后验证失败: 购物车为空！")
                    return False
                # 检查是否包含刚添加的商品
                found = any(str(item.get('product_id', '')) == str(product_id) for item in verify_cart)
                if not found:
                    self.logger.error(f"❌ 保存后验证失败: 购物车中找不到刚添加的商品 {product_id}！")
                    return False
                self.logger.info(f"✅ 成功添加产品到购物车: {product_id}, 用户: {user_id}")
                return True
            
            self.logger.warning(f"⚠️ 产品不存在: {product_id}")
            return False
            
        except Exception as e:
            self.logger.error(f"❌ 添加到购物车失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def _create_temp_product_from_code(self, product_code: str) -> dict:
        """从产品代码创建临时产品信息"""
        try:
            # 根据产品代码创建基本信息
            temp_product = {
                'id': product_code,
                'name': f'Producto {product_code}',
                'description': f'产品代码: {product_code}',
                'price': 1.20,  # 默认价格
                'image_path': 'temp_product'
            }
            
            # 如果是MmKUiX5z_25656格式，尝试解析更多信息
            if 'MmKUiX5z' in product_code:
                temp_product.update({
                    'name': 'LÁMINA ADHESIVA',
                    'description': 'LÁMINA ADHESIVA - MARMOL BLANCO HUESO RAYA DORADA',
                    'price': 1.20
                })
            
            self.logger.info(f"✅ 创建临时产品信息: {product_code}")
            return temp_product
            
        except Exception as e:
            self.logger.error(f"❌ 创建临时产品失败: {e}")
            return None
    
    def remove_from_cart(self, user_id, product_id):
        """从购物车移除商品"""
        try:
            cart = self.get_user_cart(user_id)
            cart = [item for item in cart if item['product_id'] != product_id]
            self.save_user_cart(user_id, cart)
            return True
        except Exception as e:
            self.logger.error(f"❌ 从购物车移除失败: {e}")
            return False
    
    def update_quantity(self, user_id, product_id, quantity, unit_price=None):
        """更新商品数量。unit_price 可选：前端传入时直接采用，保证与页面一致。"""
        try:
            cart = self.get_user_cart(user_id)
            products = self.db.get_all_products()
            
            for item in cart:
                if str(item.get('product_id', '')) == str(product_id):
                    if quantity <= 0:
                        cart.remove(item)
                    else:
                        # CHANGE: 若有前端传入单价则沿用；否则按新数量重算
                        if unit_price is not None:
                            try:
                                p = float(unit_price)
                                if p > 0:
                                    item['price'] = p
                                    self.logger.info(f"🛒 更新数量沿用前端单价: {p}")
                            except (ValueError, TypeError):
                                pass
                        if 'price' not in item or item.get('price', 0) <= 0:
                            product = self._find_product(products, str(product_id))
                            if product:
                                item['price'] = self._calculate_price_by_quantity(product, quantity)
                        item['quantity'] = quantity
                    break
            
            self.save_user_cart(user_id, cart)
            return True
        except Exception as e:
            self.logger.error(f"❌ 更新数量失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def _find_product(self, products, product_id):
        """CHANGE: 多种方式查找产品，兼容 W7841 / W-7841 等键名差异"""
        if not products or not product_id:
            return None
        pid = str(product_id).strip()
        if pid in products:
            return products[pid]
        pid_no_hyphen = pid.replace('-', '')
        if pid_no_hyphen in products:
            return products[pid_no_hyphen]
        import re
        m = re.match(r'^([A-Za-z]+)(\d.*)$', pid_no_hyphen)
        if m:
            alt = m.group(1) + '-' + m.group(2)
            if alt in products:
                return products[alt]
        for k, v in products.items():
            if str(k).strip().replace('-', '') == pid_no_hyphen:
                return v
            if str(k).strip().upper() == pid.upper():
                return v
        return None
    
    def _get_price_tier(self, product, field_names, default=0):
        """CHANGE: 从产品中按多个可能的字段名读取价格层级"""
        if not product:
            return default
        for name in field_names:
            v = product.get(name)
            if v is not None and v != '':
                try:
                    f = float(v)
                    if f > 0:
                        return f
                except (ValueError, TypeError):
                    continue
        return default
    
    def _calculate_price_by_quantity(self, product, quantity):
        """根据数量计算价格：1-2 单价，3-11 批发价，12+ 批量价（无批量价则用批发价）
        情况1 三价: 1-2 unidad, 3-11 mayor, 12+ bulto（无 bulto 用 mayor）
        情况2 两价(unidad+bulto): 1-11 unidad, 12+ bulto
        情况3 一价: 所有数量用该价
        """
        if not product:
            return 0
        q = int(quantity) if quantity is not None else 0
        price = self._get_price_tier(product, ['price', 'precio_unidad', 'price_unidad', 'PVP1', 'price_unit'], 0)
        wholesale_price = self._get_price_tier(product, ['wholesale_price', 'precio_mayor', 'price_mayor', 'PVP2', 'price_mayor'], 0)
        bulk_price = self._get_price_tier(product, ['bulk_price', 'precio_bulto', 'price_bulto', 'PVP3', 'price_dozen'], 0)
        has_unidad = price > 0
        has_mayor = wholesale_price > 0
        has_bulto = bulk_price > 0
        tier_count = sum([has_unidad, has_mayor, has_bulto])
        if tier_count == 0:
            return 0
        # 情况3: 一个价格 → 所有数量用单价
        if tier_count == 1:
            return price if has_unidad else (wholesale_price if has_mayor else bulk_price)
        # 情况2: 两个价格(unidad+bulto)，跳过 mayor → 1-11 unidad, 12+ bulto
        scenario_skip_mayor = tier_count == 2 and has_unidad and has_bulto and not has_mayor
        if q <= 2:
            return price if price > 0 else (wholesale_price if wholesale_price > 0 else bulk_price)
        if scenario_skip_mayor and q <= 11:
            return price
        if q <= 11:
            return wholesale_price if wholesale_price > 0 else (bulk_price if bulk_price > 0 else price)
        # q >= 12
        if scenario_skip_mayor:
            return bulk_price if bulk_price > 0 else price
        return bulk_price if bulk_price > 0 else (wholesale_price if wholesale_price > 0 else price)
    
    def get_cart_total(self, user_id):
        """计算购物车总价 - CHANGE: 优先使用购物车中保存的价格，确保与前端显示一致"""
        try:
            cart = self.get_user_cart(user_id)
            total = 0
            
            for item in cart:
                product_id = str(item.get('product_id', ''))
                quantity = float(item.get('quantity', 0))
                
                # CHANGE: 优先使用购物车中保存的价格（已经根据数量计算过的正确价格）
                # 这样可以确保与前端显示的价格一致
                price_in_cart = item.get('price')
                if price_in_cart is not None:
                    try:
                        unit_price = float(price_in_cart)
                        if unit_price > 0:
                            item_total = unit_price * quantity
                            total += item_total
                            self.logger.debug(f"  📦 商品 {product_id}: 使用购物车中保存的价格 {unit_price} x {quantity} = {item_total}")
                            continue
                    except (ValueError, TypeError):
                        pass
                
                # CHANGE: 如果购物车中没有价格，才从产品数据库重新计算
                products = self.db.get_all_products()
                product = self._find_product(products, product_id)
                
                # CHANGE: 根据数量计算单价
                if product:
                    unit_price = self._calculate_price_by_quantity(product, int(quantity))
                    item_total = unit_price * quantity
                    total += item_total
                    self.logger.debug(f"  📦 商品 {product_id}: 从产品数据库重新计算价格 {unit_price} x {quantity} = {item_total}")
                else:
                    # 如果没有产品信息，使用默认价格0
                    self.logger.warning(f"  ⚠️ 商品 {product_id} 不在产品数据库中，价格设为0")
                    unit_price = 0.0
            
            self.logger.info(f"💰 购物车总价: {total} (商品数: {len(cart)})")
            return total
        except Exception as e:
            self.logger.error(f"❌ 计算总价失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return 0
    
    async def show_cart(self, query, context):
        """显示购物车"""
        if not TELEGRAM_AVAILABLE:
            return
        try:
            user_id = query.from_user.id
            cart = self.get_user_cart(user_id)
            
            if not cart:
                cart_text = """
🛒 **购物车**

购物车是空的！

🛍️ 去浏览产品吧！
                """
                
                keyboard = [
                    [InlineKeyboardButton("🛍️ 浏览产品", callback_data="show_catalog")],
                    [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
                ]
            else:
                cart_text = "🛒 **购物车**\n\n"
                total = 0
                
                for item in cart:
                    item_total = item['price'] * item['quantity']
                    total += item_total
                    cart_text += f"📦 {item['name']}\n"
                    cart_text += f"💰 ${item['price']:.2f} × {item['quantity']} = ${item_total:.2f}\n\n"
                
                cart_text += f"💵 **总计**: ${total:.2f}"
                
                keyboard = []
                for item in cart:
                    keyboard.append([
                        InlineKeyboardButton(
                            f"➖ {item['name']}", 
                            callback_data=f"cart_remove_{item['product_id']}"
                        )
                    ])
                
                keyboard.extend([
                    [InlineKeyboardButton("🛒 开始结账", callback_data="start_checkout")],
                    [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                cart_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"❌ 显示购物车失败: {e}")
    
    async def start_checkout(self, query, context):
        """开始结账"""
        if not TELEGRAM_AVAILABLE:
            return
        try:
            user_id = query.from_user.id
            cart = self.get_user_cart(user_id)
            
            if not cart:
                await query.edit_message_text("❌ 购物车是空的！")
                return
            
            total = self.get_cart_total(user_id)
            
            checkout_text = f"""
💳 **结账确认**

🛒 **购物车内容**:
"""
            
            for item in cart:
                item_total = item['price'] * item['quantity']
                checkout_text += f"📦 {item['name']} × {item['quantity']} = ${item_total:.2f}\n"
            
            checkout_text += f"""
💵 **总计**: ${total:.2f}

确认下单吗？
            """
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ 确认下单", callback_data="confirm_order"),
                    InlineKeyboardButton("❌ 取消", callback_data="show_cart")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                checkout_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"❌ 开始结账失败: {e}")
    
    async def confirm_order(self, query, context):
        """确认订单"""
        if not TELEGRAM_AVAILABLE:
            return
        try:
            user_id = query.from_user.id
            cart = self.get_user_cart(user_id)
            
            if not cart:
                await query.edit_message_text("❌ 购物车是空的！")
                return
            
            total = self.get_cart_total(user_id)
            
            # 保存订单
            from database_manager import DatabaseManager
            db = DatabaseManager()
            order_id = db.create_order(user_id, cart, total)
            
            # 清空购物车
            self.save_user_cart(user_id, [])
            
            order_text = f"""
✅ **订单确认成功！**

📋 **订单号**: {order_id}
💵 **总金额**: ${total:.2f}

📞 客服将尽快联系您确认订单详情！

感谢您的购买！
            """
            
            keyboard = [
                [InlineKeyboardButton("🛍️ 继续购物", callback_data="show_catalog")],
                [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_menu")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                order_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            self.logger.error(f"❌ 确认订单失败: {e}")

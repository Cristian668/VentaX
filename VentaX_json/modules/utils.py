#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VentaX 客户下单Bot - 工具函数模块
简化版本，专注于工具函数
"""

import os
import sys
import logging
import json
from datetime import datetime

# 添加模块路径
sys.path.append(os.path.dirname(__file__))

logger = logging.getLogger(__name__)

class Utils:
    """工具函数类"""
    
    def __init__(self):
        self.logger = logger
        
    def load_config(self, config_file):
        """加载配置文件"""
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', config_file)
            
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            return {}
            
        except Exception as e:
            self.logger.error(f"❌ 加载配置文件失败: {e}")
            return {}
    
    def save_config(self, config_file, config):
        """保存配置文件"""
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', config_file)
            
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 保存配置文件失败: {e}")
            return False
    
    def format_price(self, price):
        """格式化价格"""
        try:
            return f"${price:.2f}"
        except Exception as e:
            self.logger.error(f"❌ 格式化价格失败: {e}")
            return "$0.00"
    
    def format_quantity(self, quantity):
        """格式化数量"""
        try:
            return str(quantity)
        except Exception as e:
            self.logger.error(f"❌ 格式化数量失败: {e}")
            return "1"
    
    def format_datetime(self, dt):
        """格式化日期时间"""
        try:
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            self.logger.error(f"❌ 格式化日期时间失败: {e}")
            return "未知时间"
    
    def generate_order_id(self, user_id):
        """生成订单ID（已废弃，请使用 generate_unified_order_id）"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            return f"ORD_{timestamp}_{user_id}"
        except Exception as e:
            self.logger.error(f"❌ 生成订单ID失败: {e}")
            return f"ORD_{user_id}"


# ====== 统一的订单ID生成函数 ======
# CHANGE: 统一所有脚本的订单ID生成逻辑，与 ventax_customer_bot_pedidos8.pyw 一致
# 格式：{SOURCE}_{invoice_num}_{YYYYMMDD}_{HHMMSS}
# invoice_num: 从user_id的后6位生成，不足9位前面补0

def generate_invoice_num(user_id):
    """
    生成invoice_num（统一的发票号码格式）
    
    Args:
        user_id: 用户ID（可以是整数或字符串）
        
    Returns:
        str: 9位数字的invoice_num，格式为 000XXXXXX（从user_id的后6位生成）
        
    示例:
        user_id=607324 -> invoice_num="000607324"
        user_id=123 -> invoice_num="000000123"
    """
    try:
        # 取user_id的后6位，不足9位前面补0
        invoice_num = f"{str(user_id)[-6:]:0>9}"
        return invoice_num
    except Exception as e:
        logger.error(f"❌ 生成invoice_num失败: {e}, user_id={user_id}")
        # 返回默认值
        return "000000000"


def generate_unified_order_id(source_prefix, user_id):
    """
    生成统一的订单ID（所有脚本使用此函数确保格式一致）
    与 ventax_customer_bot_pedidos8.pyw 保持一致
    
    Args:
        source_prefix: 订单来源前缀，如 "VENTAX", "ORD", "SISTEMA"
        user_id: 用户ID（可以是整数或字符串）
        
    Returns:
        str: 格式为 {SOURCE}_{invoice_num}_{YYYYMMDD}_{HHMMSS}
        invoice_num: 9位数字，从user_id的后6位生成，不足9位前面补0
        
    示例:
        generate_unified_order_id("VENTAX", 607324) -> "VENTAX_000607324_20260124_155341"
        generate_unified_order_id("ORD", 1) -> "ORD_000000001_20260124_155341"
        generate_unified_order_id("SISTEMA", 999999) -> "SISTEMA_000999999_20260124_155341"
    """
    try:
        # CHANGE: 使用generate_invoice_num生成9位补0的invoice_num，与ventax_customer_bot_pedidos8.pyw一致
        invoice_num = generate_invoice_num(user_id)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        order_id = f"{source_prefix}_{invoice_num}_{timestamp}"
        return order_id
    except Exception as e:
        logger.error(f"❌ 生成统一订单ID失败: {e}, source_prefix={source_prefix}, user_id={user_id}")
        # 返回默认值
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"{source_prefix}_000000000_{timestamp}"


def extract_invoice_num_from_comprobante(comprobante):
    """
    从comprobante中提取invoice_num（用于Sistema Factura）
    
    Args:
        comprobante: comprobante值，可能是 "001-002-{invoice_num}" 格式或纯数字
        
    Returns:
        str: 9位数字的invoice_num
        
    示例:
        extract_invoice_num_from_comprobante("001-002-607324") -> "000607324"
        extract_invoice_num_from_comprobante("607324") -> "000607324"
    """
    try:
        invoice_num = comprobante
        if '001-002-' in str(comprobante):
            # 提取invoice_num（去掉001-002-前缀）
            invoice_num = str(comprobante).replace('001-002-', '').strip()
        # 确保invoice_num是9位数字（不足前面补0）
        invoice_num = generate_invoice_num(invoice_num)
        return invoice_num
    except Exception as e:
        logger.error(f"❌ 从comprobante提取invoice_num失败: {e}, comprobante={comprobante}")
        return "000000000"
    
    def validate_price(self, price):
        """验证价格"""
        try:
            price = float(price)
            return price >= 0
        except (ValueError, TypeError):
            return False
    
    def validate_quantity(self, quantity):
        """验证数量"""
        try:
            quantity = int(quantity)
            return quantity > 0
        except (ValueError, TypeError):
            return False
    
    def truncate_text(self, text, max_length=100):
        """截断文本"""
        try:
            if len(text) <= max_length:
                return text
            return text[:max_length] + "..."
        except Exception as e:
            self.logger.error(f"❌ 截断文本失败: {e}")
            return text
    
    def clean_filename(self, filename):
        """清理文件名"""
        try:
            # 移除非法字符
            invalid_chars = '<>:"/\\|?*'
            for char in invalid_chars:
                filename = filename.replace(char, '_')
            
            # 限制长度
            if len(filename) > 100:
                filename = filename[:100]
            
            return filename
            
        except Exception as e:
            self.logger.error(f"❌ 清理文件名失败: {e}")
            return "unknown"
    
    def get_file_size(self, file_path):
        """获取文件大小"""
        try:
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                return size
            return 0
        except Exception as e:
            self.logger.error(f"❌ 获取文件大小失败: {e}")
            return 0
    
    def format_file_size(self, size):
        """格式化文件大小"""
        try:
            if size < 1024:
                return f"{size} B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f} KB"
            elif size < 1024 * 1024 * 1024:
                return f"{size / (1024 * 1024):.1f} MB"
            else:
                return f"{size / (1024 * 1024 * 1024):.1f} GB"
        except Exception as e:
            self.logger.error(f"❌ 格式化文件大小失败: {e}")
            return "未知大小"
    
    def is_valid_image(self, file_path):
        """检查是否是有效图片"""
        try:
            if not os.path.exists(file_path):
                return False
            
            # 检查文件扩展名
            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
            _, ext = os.path.splitext(file_path.lower())
            
            return ext in valid_extensions
            
        except Exception as e:
            self.logger.error(f"❌ 检查图片有效性失败: {e}")
            return False
    
    def create_directory(self, directory_path):
        """创建目录"""
        try:
            os.makedirs(directory_path, exist_ok=True)
            return True
        except Exception as e:
            self.logger.error(f"❌ 创建目录失败: {e}")
            return False
    
    def get_timestamp(self):
        """获取当前时间戳"""
        try:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            self.logger.error(f"❌ 获取时间戳失败: {e}")
            return "未知时间"

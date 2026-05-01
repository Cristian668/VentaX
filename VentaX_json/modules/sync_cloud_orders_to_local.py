#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端→本地同步：从 PWA API 拉取订单并写入本地 shared_database.unified_orders。
供本地打单/发票（Sistema Factura、purchaser_notification_manager_gui）使用。

配置（二选一）：
  - 环境变量：CLOUD_SYNC_API_URL、SYNC_SECRET（或 SYNC_TOKEN）
  - 配置文件：VentaX_json/sync_config.json 内 "cloud_sync": { "api_base_url": "", "sync_token": "" }

产品名称（ITEM 字段）：
  - 若云端已重新部署/重启：API 会直接返回带 name 的 cart_items，开票界面 ITEM 即正确。
  - 若云端未部署：本脚本在保存前会用本地 SQLite 产品库 + PostgreSQL（sync_config 的 neon_url/pg_local）补全 name，开票界面 ITEM 仍可正确显示。

用法：
  python sync_cloud_orders_to_local.py
  python sync_cloud_orders_to_local.py --config "D:/path/to/sync_config.json"
"""

import os
import sys
import re
import json
import logging
import argparse

# 确保可导入同目录及上级模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
VENTAX_JSON_ROOT = os.path.dirname(SCRIPT_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _load_config(config_path=None):
    """优先环境变量，再读配置文件。返回 (api_base_url, sync_token) 或 (None, None)。"""
    api_url = os.environ.get("CLOUD_SYNC_API_URL", "").strip()
    token = os.environ.get("SYNC_SECRET") or os.environ.get("SYNC_TOKEN") or ""
    token = token.strip()
    if api_url and token:
        return api_url.rstrip("/"), token
    if config_path and os.path.isfile(config_path):
        path = config_path
    else:
        path = os.path.join(VENTAX_JSON_ROOT, "sync_config.json")
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cs = data.get("cloud_sync") or {}
        api_url = (cs.get("api_base_url") or "").strip()
        token = (cs.get("sync_token") or "").strip()
        if api_url and token:
            return api_url.rstrip("/"), token
    except Exception as e:
        logger.warning("读取配置失败 %s: %s", path, e)
    return None, None


def _get_shared_database():
    """加载 Sistema Factura shared_database，返回 get_shared_database() 实例或 None。"""
    base_dir = os.path.dirname(VENTAX_JSON_ROOT)  # internal
    shared_path = os.path.join(base_dir, "Sistema Factura", "shared_database.py")
    if not os.path.isfile(shared_path):
        logger.error("未找到 shared_database: %s", shared_path)
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("shared_database", shared_path)
    if not spec or not spec.loader:
        logger.error("无法加载 shared_database 模块")
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.get_shared_database()


def _get_pg_connection_string(config_path=None):
    """从 sync_config.json 的 neon_url 或 database_config.json 的 postgresql 获取 PG 连接串。返回可传给 psycopg2.connect 的字符串或 None。"""
    if config_path and os.path.isfile(config_path):
        path = config_path
    else:
        path = os.path.join(VENTAX_JSON_ROOT, "sync_config.json")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            url = (data.get("neon_url") or "").strip()
            if url:
                return url
            pg = data.get("pg_local") or {}
            if pg.get("host") and pg.get("database") and pg.get("user"):
                port = pg.get("port", 5432)
                pwd = pg.get("password", "")
                return "postgresql://%s:%s@%s:%s/%s" % (
                    pg["user"], pwd or "", pg["host"], port, pg["database"]
                )
    except Exception as e:
        logger.debug("读取 sync_config 的 PG 配置失败: %s", e)
    db_cfg = os.path.join(VENTAX_JSON_ROOT, "database_config.json")
    try:
        if os.path.isfile(db_cfg):
            with open(db_cfg, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            pg = (cfg.get("postgresql") or {})
            if pg.get("host") and pg.get("database") and pg.get("user"):
                port = pg.get("port", 5432)
                pwd = pg.get("password", "")
                return "postgresql://%s:%s@%s:%s/%s" % (
                    pg["user"], pwd or "", pg["host"], port, pg["database"]
                )
    except Exception as e:
        logger.debug("读取 database_config 的 PG 配置失败: %s", e)
    return None


def _run_cristy_stock_sync():
    """Cristy 库存同步（自家产品 codigo_proveedor=Cristy）：SQL Server -> PostgreSQL。"""
    try:
        from sync_own_stock_to_postgresql import run_sync as run_stock_sync
        success, msg = run_stock_sync()
        if success:
            logger.info("Cristy 库存同步: %s", msg)
        else:
            logger.warning("Cristy 库存同步失败: %s", msg)
    except Exception as e:
        logger.warning("Cristy 库存同步异常（不影响订单同步）: %s", e)


def _get_product_from_postgres(product_code: str, config_path=None):
    """从 PostgreSQL products 表按 codigo_producto 或 id_producto 查，返回 (codigo_producto, nombre_producto)。
    查不到返回 (None, None)。用于同步时同时修正代码与名称（如 1851 -> XE02 / ENCAUCHADC CRUESC）。"""
    conn_str = _get_pg_connection_string(config_path)
    if not conn_str:
        return None, None
    try:
        import psycopg2
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        cur = conn.cursor()
        code = str(product_code).strip()
        cur.execute(
            """SELECT codigo_producto, nombre_producto FROM products
               WHERE codigo_producto = %s OR id_producto::text = %s
               LIMIT 1""",
            (code, code),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and (row[0] or row[1]):
            return ((row[0] or "").strip(), (row[1] or "").strip())
    except ImportError:
        pass
    except Exception as e:
        logger.debug("从 PostgreSQL 查产品失败 code=%s: %s", product_code, e)
    return None, None


def main():
    parser = argparse.ArgumentParser(description="云端订单同步到本地 unified_orders")
    parser.add_argument("--config", default=None, help="sync_config.json 路径（可选）")
    parser.add_argument("--dry-run", action="store_true", help="仅拉取并打印订单数量，不写入本地")
    args = parser.parse_args()

    api_base_url, sync_token = _load_config(args.config)
    if not api_base_url or not sync_token:
        logger.error("未配置同步：请设置环境变量 CLOUD_SYNC_API_URL 与 SYNC_SECRET，或在 sync_config.json 中配置 cloud_sync.api_base_url 与 cloud_sync.sync_token")
        sys.exit(1)

    try:
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
    except ImportError:
        from urllib2 import Request, urlopen, HTTPError
    url = f"{api_base_url}/api/sync/orders"
    req = Request(url, headers={"X-Sync-Token": sync_token})
    logger.info("请求云端: %s", url)
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if getattr(raw, "decode", None):
                raw = raw.decode("utf-8")
            out = json.loads(raw)
    except HTTPError as e:
        body = e.read() if getattr(e, "read", None) else b""
        try:
            body = body.decode("utf-8") if isinstance(body, bytes) else body
            err_json = json.loads(body) if body else {}
            detail = err_json.get("detail") or err_json.get("error") or body
        except Exception:
            detail = str(e)
        logger.error("请求失败: HTTP %s - %s", e.code, detail)
        sys.exit(1)
    except Exception as e:
        logger.error("请求失败: %s", e)
        sys.exit(1)

    if not out.get("success") or "data" not in out:
        logger.error("API 返回异常: %s", out)
        sys.exit(1)

    orders = out["data"]
    if not isinstance(orders, list):
        orders = []
    logger.info("拉取到 %d 条订单", len(orders))

    if args.dry_run:
        logger.info("--dry-run：不写入本地")
        _run_cristy_stock_sync()
        return

    if not orders:
        logger.info("无新订单需同步")
        _run_cristy_stock_sync()
        return

    db = _get_shared_database()
    if not db:
        logger.error("无法连接本地 shared_database，同步终止")
        sys.exit(1)

    # CHANGE: 本地用产品库（先 SQLite 再 PostgreSQL）补全 cart_items 的 name，保证开票界面 ITEM 显示产品名称（即使云端未重启/未部署）
    _product_resolver = None
    try:
        from database_manager import DatabaseManager
        _product_resolver = DatabaseManager()
    except Exception as e:
        logger.debug("本地 SQLite 产品库不可用: %s", e)

    def _is_placeholder_name(name, code):
        """判断是否为占位名称（与 Sistema Factura need_resolve / ventax_customer_bot 开票逻辑一致）：空、等于 code、'Producto'、'PRODUCTO NUEVO'、'Producto 1847' 等需补全。"""
        if not name or not str(name).strip():
            return True
        n = str(name).strip().upper()
        if n == str(code).strip().upper():
            return True
        if n in ("NAN", "NONE", "NULL"):
            return True
        if re.match(r"^Producto\s+\d+\s*$", str(name).strip(), re.IGNORECASE):
            return True
        # 与 Factura need_resolve 一致：PRODUCTO + 空格 + 数字
        if n.startswith("PRODUCTO ") and len(n) > 8 and n[8:].strip().isdigit():
            return True
        # CHANGE: 通用占位名也需补全（其他供应商 API 返回 "Producto" / "PRODUCTO NUEVO"）
        if n == "PRODUCTO" or n == "PRODUCTO NUEVO" or n == "PRODUCT" or (len(n) < 3 and n.isalpha()):
            return True
        return False

    def _ensure_cart_item_names(order_data, cfg_path=None):
        """补全 cart_items 的 code 与 name：用本地/SQLite/PostgreSQL 解析出真实 codigo（如 XE02）与名称（如 ENCAUCHADC CRUESC）。"""
        items = order_data.get("cart_items") or []
        if not items:
            return
        for it in items:
            code = str(it.get("code") or it.get("product_id") or it.get("id") or "").strip()
            name = str(it.get("name") or "").strip()
            # NOTE: 与 Factura/pedidos 一致，优先用 product_id 查 PG（Neon 中 id_producto::text 匹配），再 code/id
            codes_to_try = []
            for key in ("product_id", "id", "code"):
                v = it.get(key)
                if v is not None and str(v).strip():
                    codes_to_try.append(str(v).strip())
            # CHANGE: 提取数字部分（如 TG_JUGUETESFANG_90029 -> 90029），Neon 中 codigo_producto 可能为 XE02，id_producto=90029
            for c in list(codes_to_try):
                for n in re.findall(r'\d+', c):
                    if n and len(n) >= 3:
                        codes_to_try.append(n)
            if not codes_to_try:
                continue
            seen = set()
            codes_to_try = [c for c in codes_to_try if c not in seen and not seen.add(c)]
            if not name:
                code = codes_to_try[0]
            if not _is_placeholder_name(name, code):
                continue
            resolved_code = None
            resolved_name = None
            for c in codes_to_try:
                if _product_resolver:
                    try:
                        prod = _product_resolver.get_product(c)
                        if prod:
                            resolved_name = (prod.get("name") or "").strip()
                            # CHANGE: 同步时写入真实 codigo（如 XE02），开票显示 CODIGO 正确
                            resolved_code = (prod.get("id") or "").strip() if prod.get("id") else None
                            if resolved_name:
                                break
                    except Exception:
                        pass
                if not resolved_name:
                    pg_codigo, pg_nombre = _get_product_from_postgres(c, cfg_path)
                    if pg_nombre:
                        resolved_name = pg_nombre
                        resolved_code = pg_codigo
                        break
                if resolved_name:
                    break
            if resolved_name:
                it["name"] = resolved_name
            if resolved_code:
                it["code"] = resolved_code
                if "product_id" in it and str(it.get("product_id")).strip() == code:
                    pass  # 保留 product_id 供追溯，code 已改为真实 codigo

    saved = 0
    for order_data in orders:
        try:
            _ensure_cart_item_names(order_data, args.config)
            db.save_unified_order(order_data)
            saved += 1
            logger.info("已写入本地: order_id=%s", order_data.get("order_id"))
        except Exception as e:
            logger.warning("写入失败 order_id=%s: %s", order_data.get("order_id"), e)

    logger.info("同步完成: 成功 %d / 共 %d", saved, len(orders))

    _run_cristy_stock_sync()


if __name__ == "__main__":
    main()

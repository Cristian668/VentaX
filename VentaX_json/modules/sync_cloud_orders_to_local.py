#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端→本地同步：从 PWA API 拉取订单并写入本地 shared_database.unified_orders。
供本地打单/发票（Sistema Factura、purchaser_notification_manager_gui）使用。

配置（二选一）：
  - 环境变量：CLOUD_SYNC_API_URL、SYNC_SECRET（或 SYNC_TOKEN）
  - 配置文件：VentaX_json/sync_config.json 内 "cloud_sync": { "api_base_url": "", "sync_token": "" }

用法：
  python sync_cloud_orders_to_local.py
  python sync_cloud_orders_to_local.py --config "D:/path/to/sync_config.json"
"""

import os
import sys
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
        return

    if not orders:
        logger.info("无新订单需同步")
        return

    db = _get_shared_database()
    if not db:
        logger.error("无法连接本地 shared_database，同步终止")
        sys.exit(1)

    saved = 0
    for order_data in orders:
        try:
            db.save_unified_order(order_data)
            saved += 1
            logger.info("已写入本地: order_id=%s", order_data.get("order_id"))
        except Exception as e:
            logger.warning("写入失败 order_id=%s: %s", order_data.get("order_id"), e)

    logger.info("同步完成: 成功 %d / 共 %d", saved, len(orders))


if __name__ == "__main__":
    main()

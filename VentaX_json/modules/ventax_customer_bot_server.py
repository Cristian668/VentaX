#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VentaX 客服机器人 — HTTP 服务器
提供 /chat（文字）和 /chat/image（图片识别）API
支持本地调试 + Render 云端部署
"""
import os
import json
import sys
import base64

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ventax_customer_bot import chat, chat_with_image

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


def _get_html_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base, "..", "..", "test ocr", "ventax_customer_debug.html"))


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
        return jsonify({"status": "ok"})

    @app.route("/chat", methods=["POST"])
    def chat_api():
        try:
            data = request.get_json() or {}
            msg = data.get("message", "").strip()
            if not msg:
                return jsonify({"error": "message vacío"}), 400
            reply = chat(msg)
            return jsonify({"reply": reply, "path": _detect_path(reply)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/chat/image", methods=["POST"])
    def chat_image_api():
        """图片识别 API — 接受 JSON(base64) 或 multipart/form-data"""
        try:
            img_b64 = None
            msg = ""
            mime = "image/jpeg"
            if request.content_type and "multipart" in request.content_type:
                f = request.files.get("image")
                if not f:
                    return jsonify({"error": "no image file"}), 400
                img_b64 = base64.b64encode(f.read()).decode("ascii")
                mime = f.content_type or "image/jpeg"
                msg = request.form.get("message", "")
            else:
                data = request.get_json() or {}
                img_b64 = data.get("image_base64", "")
                msg = data.get("message", "")
                mime = data.get("mime_type", "image/jpeg")
            if not img_b64:
                return jsonify({"error": "no image data"}), 400
            reply = chat_with_image(msg, img_b64, mime)
            return jsonify({"reply": reply, "path": "vision"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


# Render/gunicorn 入口：gunicorn ventax_customer_bot_server:app
app = create_app()


def main():
    if app is None:
        return 1
    port = int(os.environ.get("PORT", 8765))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"VentaX 客服: http://{host}:{port}/")
    print("API: POST /chat (文字) | POST /chat/image (图片)")
    print("修改代码后需重启。Esc/Ctrl+C 退出")
    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

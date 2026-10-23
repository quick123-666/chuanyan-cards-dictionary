#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
川言 · 四川方言卡片 —— 本地服务器

作用：让录音真正落盘到项目文件夹的 audio/ 目录，
      这样换浏览器、换电脑、推 GitHub 都不会丢。

只用 Python 标准库，不需要 pip install 任何东西。

启动：
    py server.py          （Windows）
    python3 server.py     （macOS / Linux）

然后浏览器打开 http://127.0.0.1:8765/

接口：
    GET    /api/recordings          -> {"ids":[1,2,3]}
    PUT    /api/recordings/<id>     请求体=音频字节，落盘为 audio/<id>.webm
    DELETE /api/recordings/<id>     删除 audio/<id>.webm

约定：每个词条一个文件，audio/<wordId>.webm。
     同一个词条再录一次会覆盖旧文件（这是既定设计，不是 bug）。
"""

import json
import os
import socket
import sys
import urllib.parse
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(ROOT, "audio")
EXT = ".webm"
DEFAULT_PORT = 8765
PORT_SCAN = 20
API_PREFIX = "/api/recordings/"


# ---------- 存储层 ----------

def ensure_audio_dir():
    if not os.path.isdir(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)


def audio_path(word_id):
    return os.path.join(AUDIO_DIR, "%d%s" % (int(word_id), EXT))


def list_ids():
    """扫描 audio/ 目录，返回所有合法的 wordId（已排序）。"""
    ensure_audio_dir()
    ids = []
    for name in os.listdir(AUDIO_DIR):
        stem, ext = os.path.splitext(name)
        if ext.lower() != EXT:
            continue
        try:
            ids.append(int(stem))
        except ValueError:
            continue
    ids.sort()
    return ids


def parse_word_id(raw):
    """从 URL 片段解析 wordId，非法返回 None。"""
    word_id = urllib.parse.unquote(raw).strip().strip("/")
    if not word_id.isdigit():
        return None
    return int(word_id)


# ---------- HTTP 处理 ----------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    # -- 工具 --

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- 路由 --

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.rstrip("/") == "/api/recordings":
            self._json({"ids": list_ids(), "mode": "server"})
            return
        if path == "/api/ping":
            self._json({"ok": True})
            return
        super().do_GET()

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith(API_PREFIX):
            self._json({"error": "not found"}, 404)
            return

        word_id = parse_word_id(path[len(API_PREFIX):])
        if word_id is None:
            self._json({"error": "bad word id"}, 400)
            return

        body = self._read_body()
        if not body:
            self._json({"error": "empty body"}, 400)
            return

        ensure_audio_dir()
        target = audio_path(word_id)
        tmp = target + ".tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(body)
            os.replace(tmp, target)          # 原子替换，避免半截文件
        except OSError as exc:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self._json({"error": "write failed: %s" % exc}, 500)
            return

        self._json({
            "ok": True,
            "wordId": word_id,
            "file": "audio/%d%s" % (word_id, EXT),
            "size": os.path.getsize(target),
        })

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith(API_PREFIX):
            self._json({"error": "not found"}, 404)
            return

        word_id = parse_word_id(path[len(API_PREFIX):])
        if word_id is None:
            self._json({"error": "bad word id"}, 400)
            return

        target = audio_path(word_id)
        existed = os.path.isfile(target)
        if existed:
            try:
                os.remove(target)
            except OSError as exc:
                self._json({"error": "delete failed: %s" % exc}, 500)
                return
        self._json({"ok": True, "wordId": word_id, "deleted": existed})

    def do_OPTIONS(self):
        # file:// 页面跨源 PUT 录音时，浏览器会先发预检
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def end_headers(self):
        # file:// 页面的 origin 是 null，必须原样回显，`*` 不生效
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # 录音文件刚写入时立刻回放，不能被浏览器缓存挡住
        if self.path.endswith(EXT):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write("  [api] %s\n" % (fmt % args))


# ---------- 启动 ----------

def pick_port(start):
    for port in range(start, start + PORT_SCAN):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("找不到可用端口（%d-%d 都被占用）" % (start, start + PORT_SCAN - 1))


def main():
    argv = sys.argv[1:]
    port = int(argv[0]) if argv and argv[0].isdigit() else pick_port(DEFAULT_PORT)
    open_browser = "--no-browser" not in argv

    ensure_audio_dir()
    existing = list_ids()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    url = "http://127.0.0.1:%d/" % port

    print("")
    print("  川言 · 四川方言卡片")
    print("  " + "-" * 42)
    print("  地址   %s" % url)
    print("  项目   %s" % ROOT)
    print("  录音   %s（已有 %d 条）" % (AUDIO_DIR, len(existing)))
    print("")
    print("  录音会自动存成 audio/<词条id>.webm，可直接进 git。")
    print("  按 Ctrl+C 停止。")
    print("")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
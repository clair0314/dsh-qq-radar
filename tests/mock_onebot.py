"""一个假的 OneBot 11 服务，用来在没装 QQ 的情况下跑通全流程。

单独运行：
    python3 tests/mock_onebot.py --port 3000
然后 qq-digest check / digest 都能连上它。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402


def build_messages(group_id: str, now: int | None = None) -> list:
    now = now or int(time.time())
    items = []
    for index, (minutes_ago, sender, text) in enumerate(fixtures.CONVERSATIONS.get(str(group_id), [])):
        ts = now - minutes_ago * 60
        segments = [{"type": "text", "data": {"text": text}}]
        if "豆子" in text or "夜景" in text:
            segments.append({"type": "image", "data": {"file": "demo.jpg", "url": "https://example.com/demo.jpg"}})
        uid = 20000 + sum(ord(ch) for ch in sender) % 5000
        items.append({
            "message_id": index + 1,
            "message_seq": index + 1,
            "real_id": index + 1,
            "time": ts,
            "sender": {"user_id": uid, "nickname": sender, "card": ""},
            "message": segments,
            "raw_message": text,
        })
    items.sort(key=lambda m: m["message_seq"])
    return items


class Handler(BaseHTTPRequestHandler):
    now = None

    def log_message(self, fmt, *args):  # 安静一点
        if os.environ.get("QQDIGEST_VERBOSE"):
            sys.stderr.write("[mock-onebot] " + (fmt % args) + "\n")

    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._send({"status": "failed", "retcode": 1400, "message": "bad json"}, 400)

        action = self.path.strip("/").split("/")[-1]
        if action == "" or action == "root":
            action = data.get("action", "")
            params = data.get("params") or {}
        else:
            params = data

        if action == "get_login_info":
            return self._ok({"user_id": fixtures.LOGIN["user_id"], "nickname": fixtures.LOGIN["nickname"]})
        if action == "get_group_list":
            return self._ok(fixtures.GROUPS)
        if action == "get_group_info":
            gid = str(params.get("group_id"))
            for group in fixtures.GROUPS:
                if str(group["group_id"]) == gid:
                    return self._ok(group)
            return self._ok({"group_id": gid, "group_name": "未知群"})
        if action == "get_group_msg_history":
            gid = str(params.get("group_id"))
            seq = int(params.get("message_seq") or 0)
            count = int(params.get("count") or 100)
            messages = build_messages(gid, self.now)
            if seq <= 0:
                end = len(messages)
            else:
                end = next((i for i, m in enumerate(messages) if m["message_seq"] >= seq), len(messages))
            start = max(0, end - count)
            return self._ok({"messages": messages[start:end]})
        return self._send({"status": "failed", "retcode": 1404, "message": "unknown action " + action}, 404)

    def _ok(self, data):
        self._send({"status": "ok", "retcode": 0, "data": data})


class MockOneBot:
    def __init__(self, port: int = 0, fixed_now: int | None = None):
        self.handler = type("FixtureHandler", (Handler,), {"now": fixed_now})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self.handler)
        self.port = self.httpd.server_address[1]
        self.thread = None

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    server = MockOneBot(args.port).start()
    print("mock OneBot 已启动：%s（Ctrl+C 退出）" % server.base_url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()

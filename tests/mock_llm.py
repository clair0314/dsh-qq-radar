"""假的 OpenAI 兼容模型服务，返回 fixtures_summaries 里的摘要。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures_summaries import SUMMARIES  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if os.environ.get("QQDIGEST_VERBOSE"):
            sys.stderr.write("[mock-llm] " + (fmt % args) + "\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return self._send({"error": {"message": "bad json"}}, 400)

        prompt = "\n".join(str(m.get("content", "")) for m in payload.get("messages", []))
        group = None
        for name in SUMMARIES:
            if ("群名：%s" % name) in prompt:
                group = name
                break
        if group is None:
            summary = {
                "headline": "测试群聊",
                "vibe": "模拟数据",
                "topics": [{"title": "测试", "summary": "ok", "points": [], "people": [], "status": "已聊完"}],
                "highlights": [], "links": [], "questions": [], "mentions_me": [],
            }
        else:
            summary = SUMMARIES[group]

        body = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": payload.get("model", "mock"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(summary, ensure_ascii=False)},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        }
        self._send(body)

    def _send(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class MockLLM:
    def __init__(self, port: int = 0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = None

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.port

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
    parser.add_argument("--port", type=int, default=3900)
    args = parser.parse_args()
    server = MockLLM(args.port).start()
    print("mock LLM 已启动：%s（Ctrl+C 退出）" % server.base_url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()

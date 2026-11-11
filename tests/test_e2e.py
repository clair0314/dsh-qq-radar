"""端到端测试：假 OneBot + 假模型，跑完 collect → bundle → summary → HTML。"""
from __future__ import annotations

import json
import os
import base64
import hashlib
import hmac
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(HERE))

import fixtures  # noqa: E402
from mock_llm import MockLLM  # noqa: E402
from mock_onebot import MockOneBot  # noqa: E402
from qqdigest import feishu, normalize, summarize, util  # noqa: E402
from qqdigest.bundle import fit_budget  # noqa: E402

BIN = ROOT / "bin" / "qq-digest"


def run_cli(home: Path, *args, timeout=90):
    env = os.environ.copy()
    env["QQDIGEST_HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(home / ".pycache")
    return subprocess.run(
        [str(BIN), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def write_config(home: Path, onebot_url: str, llm_url: str | None, page_size: int = 10):
    home.mkdir(parents=True, exist_ok=True)
    cfg = {
        "onebot": {"base_url": onebot_url, "timeout": 10},
        "groups": [
            {"id": "700001", "name": "AI 工具折腾群"},
            {"id": "700002", "name": "摄影与咖啡"},
        ],
        "me": {"names": ["阿瓜"], "qq": "1008611"},
        "collect": {"window_hours": 24, "page_size": page_size, "max_messages_per_group": 500},
        "llm": {
            "base_url": llm_url or "http://127.0.0.1:1/v1",
            "api_key": "sk-test" if llm_url else "",
            "model": "mock-model",
            "concurrency": 2,
            "max_input_chars": 30000,
        },
        "report": {"dir": "reports", "timezone": "Asia/Shanghai"},
    }
    (home / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class E2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.onebot = MockOneBot().start()
        cls.llm = MockLLM().start()

    @classmethod
    def tearDownClass(cls):
        cls.onebot.stop()
        cls.llm.stop()

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="qqdigest-test-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        write_config(self.home, self.onebot.base_url, self.llm.base_url)

    # ------------------------------------------------------------------
    def test_full_digest_ai(self):
        proc = run_cli(self.home, "digest", "--since", "24h", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ai"])
        for key in ("md", "html", "json", "dir"):
            self.assertTrue(Path(payload["paths"][key]).exists(), key)

        html_text = Path(payload["paths"]["html"]).read_text(encoding="utf-8")
        self.assertIn("Claude 新模型与涨价", html_text)
        self.assertIn("北山公园", html_text)
        self.assertIn("展开群聊原声", html_text)
        self.assertIn("提到你", html_text)
        self.assertIn("AI 摘要", html_text)

        md_text = Path(payload["paths"]["md"]).read_text(encoding="utf-8")
        self.assertIn("# QQ 群聊日报", md_text)
        self.assertIn("值得点开", md_text)
        self.assertIn("https://github.com/infiniflow/ragflow", md_text)

        index = Path(payload["paths"]["dir"]) / "index.html"
        self.assertTrue(index.exists())
        self.assertIn("群聊日报归档", index.read_text(encoding="utf-8"))

        # 消息全部入库（分页 10 条一页，共 3 页以上）
        db = sqlite3.connect(str(self.home / "messages.db"))
        count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        db.close()
        expected = sum(len(v) for v in fixtures.CONVERSATIONS.values())
        self.assertEqual(count, expected)

    def test_dedupe_on_second_run(self):
        run_cli(self.home, "collect", "--since", "24h")
        first = self._count_rows()
        proc = run_cli(self.home, "collect", "--since", "24h")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._count_rows(), first)
        self.assertEqual(first, sum(len(v) for v in fixtures.CONVERSATIONS.values()))

    def test_window_filter(self):
        proc = run_cli(self.home, "collect", "--since", "2h", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        # 两小时内只有最近的一小段对话
        total = sum(g["in_window"] for g in data["groups"])
        self.assertGreater(total, 0)
        self.assertLess(total, 20)

    def test_no_llm_fallback_and_render(self):
        proc = run_cli(self.home, "digest", "--since", "24h", "--no-llm", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ai"])
        summary_path = Path(payload["paths"]["json"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertTrue(all(g["degraded"] for g in summary["groups"]))
        self.assertTrue(any(g["topics"] for g in summary["groups"]))
        html_text = Path(payload["paths"]["html"]).read_text(encoding="utf-8")
        self.assertIn("规则摘要", html_text)

        # 素材 + render 的「AI 代写」路径
        bundle_path = self.home / "bundle.json"
        proc = run_cli(self.home, "bundle", "--since", "24h", "--out", str(bundle_path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertIn("量子猫", bundle["groups"][0]["transcript"])
        self.assertEqual(len(bundle["groups"]), 2)

        proc = run_cli(self.home, "render", "--summary", str(summary_path), "--bundle", str(bundle_path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        paths = json.loads(proc.stdout)
        self.assertIn("展开群聊原声", Path(paths["html"]).read_text(encoding="utf-8"))

    def test_html_escapes_user_content(self):
        summary = {
            "version": 1,
            "generated_at": "2026-11-11 09:00",
            "window": {"start": 1, "end": 2, "label": "测试窗口", "timezone": "Asia/Shanghai"},
            "ai": True,
            "totals": {"groups": 1, "messages": 1, "people": 1, "links": 0},
            "groups": [{
                "id": "1",
                "name": "<script>alert(1)</script>",
                "headline": "标题 <b>加粗</b>",
                "vibe": "",
                "topics": [{"title": "<img src=x onerror=1>", "summary": "正文", "points": [], "people": [], "status": "已聊完"}],
                "highlights": [], "links": [], "questions": [], "mentions_me": [],
                "stats": {"messages": 1, "people": 1, "peak_hour": "9"},
                "degraded": False, "error": "",
            }],
            "errors": [],
        }
        path = self.home / "evil.json"
        path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        proc = run_cli(self.home, "render", "--summary", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        html_text = Path(json.loads(proc.stdout)["html"]).read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", html_text)
        self.assertNotIn("<img src=x", html_text)
        self.assertIn("&lt;script&gt;", html_text)

    def test_check_and_connection_error(self):
        proc = run_cli(self.home, "check")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("AI 工具折腾群", proc.stdout)

        bad_home = Path(tempfile.mkdtemp(prefix="qqdigest-bad-"))
        self.addCleanup(shutil.rmtree, bad_home, True)
        write_config(bad_home, "http://127.0.0.1:1", None)
        proc = run_cli(bad_home, "check", timeout=30)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("连不上", proc.stderr)

    def _count_rows(self) -> int:
        db = sqlite3.connect(str(self.home / "messages.db"))
        count = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        db.close()
        return count


    def test_bundle_split(self):
        parts = self.home / "parts"
        proc = run_cli(self.home, "bundle", "--since", "24h", "--collect", "--split", str(parts))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        files = sorted(parts.glob("*.txt"))
        self.assertEqual(len(files), 2)
        self.assertTrue(files[0].name.startswith("01-"))
        first = files[0].read_text(encoding="utf-8")
        self.assertIn("AI 工具折腾群", first)
        self.assertIn("50 条消息", first)

    def test_render_partial_ai_summary(self):
        self.assertEqual(run_cli(self.home, "collect", "--since", "24h").returncode, 0)
        bundle_path = self.home / "bundle.json"
        self.assertEqual(
            run_cli(self.home, "bundle", "--since", "24h", "--out", str(bundle_path)).returncode, 0
        )
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        summary = {
            "version": 1,
            "groups": [
                {"name": g["name"], "headline": "测试标题 " + g["name"], "vibe": "一句话", "topics": []}
                for g in bundle["groups"]
            ],
            "opportunities": [
                {
                    "title": "测试机会",
                    "need": "<b>需求</b>",
                    "product": "做一个插件",
                    "evidence": [{"group": bundle["groups"][0]["name"], "quote": "原话"}],
                    "signal": "中",
                }
            ],
        }
        summary_path = self.home / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        proc = run_cli(self.home, "render", "--summary", str(summary_path), "--bundle", str(bundle_path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        html_text = (self.home / "reports" / "latest.html").read_text(encoding="utf-8")
        self.assertIn("测试标题", html_text)
        self.assertIn("90", html_text)  # totals 从 bundle 统计补齐
        self.assertIn("展开群聊原声", html_text)
        self.assertIn("机会雷达", html_text)
        self.assertIn("测试机会", html_text)
        self.assertIn("&lt;b&gt;需求", html_text)


class UnitTest(unittest.TestCase):
    def test_parse_since(self):
        import datetime as dt

        now = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(util.parse_since("24h", now=now), now - dt.timedelta(hours=24))
        self.assertEqual(util.parse_since("3d", now=now), now - dt.timedelta(days=3))
        self.assertEqual(util.parse_since("30m", now=now), now - dt.timedelta(minutes=30))
        iso = util.parse_since("2026-11-10T08:00:00", now=now)
        self.assertEqual(iso.hour, 0)  # 08:00 +08:00 == 00:00 UTC
        with self.assertRaises(ValueError):
            util.parse_since("下周", now=now)

    def test_cq_and_segments(self):
        raw = {
            "time": 1700000000,
            "message": [
                {"type": "at", "data": {"qq": "all"}},
                {"type": "text", "data": {"text": " 注意 "}},
                {"type": "image", "data": {"file": "a.jpg"}},
                {"type": "reply", "data": {"id": "1"}},
                {"type": "json", "data": {"data": json.dumps({"prompt": "分享了一张图"})}},
            ],
            "sender": {"user_id": 1, "card": "小明", "nickname": "小明明"},
        }
        row = normalize.normalize_message(raw)
        self.assertEqual(row["sender"], "小明")
        self.assertIn("@全体成员", row["text"])
        self.assertIn("[图片]", row["text"])
        self.assertIn("[卡片:分享了一张图]", row["text"])

        self.assertEqual(normalize.cq_to_text("你好[CQ:face,id=1]呀[CQ:at,qq=123]"), "你好[表情]呀@123")
        self.assertEqual(normalize.cq_to_text("看[CQ:image,file=x.jpg]"), "看[图片]")

    def test_fit_budget_keeps_links(self):
        lines = ["%02d 甲: 普通消息" % i for i in range(200)]
        lines[73] = "10:00 乙: 看这个 https://example.com/important 很关键"
        fitted, omitted = fit_budget(lines, 1200)
        self.assertGreater(omitted, 0)
        self.assertTrue(any("example.com" in line for line in fitted))
        self.assertTrue(any(line.startswith("……（略") for line in fitted))
        self.assertLessEqual(sum(len(x) + 1 for x in fitted), 1400)

    def test_bundle_rendering_contract(self):
        group = {
            "id": "1",
            "name": "测试群",
            "stats": {"messages": 3, "people": 2, "peak_hour": "9", "top_senders": [["甲", 2]], "links": []},
            "transcript": "09:01 甲: 你好\n09:02 乙: 在吗？\n09:03 甲: 在",
        }
        cfg = {"me": {"names": ["阿瓜"]}, "report": {"timezone": "Asia/Shanghai"}}
        result = summarize.fallback_summary(group, cfg)
        self.assertTrue(result["degraded"])
        self.assertTrue(result["topics"])
        self.assertTrue(any("？" in q for q in result["questions"]))


class FeishuTest(unittest.TestCase):
    def summary(self):
        return {
            "window": {"start": 1762770120, "end": 1762856520, "label": "11-10 16:02 → 11-11 16:02"},
            "totals": {"groups": 1, "messages": 12, "people": 5, "links": 0},
            "groups": [
                {
                    "name": "测试群",
                    "headline": "今天在聊部署",
                    "topics": [{"title": "小主机", "summary": "有人问 2C2G 够不够跑 NapCat。"}],
                }
            ],
            "opportunities": [
                {"title": "账号哨兵", "need": "余额突然没了", "product": "本地小面板", "signal": "中"}
            ],
        }

    def test_card_and_signature(self):
        payload = feishu.build_payload(self.summary(), secret="s3cret")
        card = payload["card"]
        self.assertIn("QQ 群聊日报", card["header"]["title"]["content"])
        raw = json.dumps(card, ensure_ascii=False)
        self.assertIn("机会雷达", raw)
        self.assertIn("账号哨兵", raw)
        ts = payload["timestamp"]
        expected = base64.b64encode(
            hmac.new(("%s\ns3cret" % ts).encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        self.assertEqual(payload["sign"], expected)

    def test_send_to_local_server(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                captured["body"] = self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"code":0,"msg":"success"}')

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d/hook" % server.server_port
            result = feishu.send(url, feishu.build_payload(self.summary(), secret="abc"))
            self.assertEqual(result.get("code"), 0)
            body = json.loads(captured["body"].decode("utf-8"))
            self.assertEqual(body["msg_type"], "interactive")
            self.assertIn("sign", body)
            self.assertIn("测试群", json.dumps(body, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()

    def test_push_without_webhook_is_config_error(self):
        home = Path(tempfile.mkdtemp(prefix="qqdigest-push-"))
        try:
            path = home / "summary.json"
            path.write_text(json.dumps(self.summary(), ensure_ascii=False), encoding="utf-8")
            proc = run_cli(home, "push", "--summary", str(path))
            self.assertEqual(proc.returncode, 2, proc.stderr)
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""生成 README 用的示例日报（假 OneBot + 假模型，不联网）。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from mock_llm import MockLLM  # noqa: E402
from mock_onebot import MockOneBot  # noqa: E402


def main():
    examples = ROOT / "examples"
    examples.mkdir(exist_ok=True)
    onebot = MockOneBot().start()
    llm = MockLLM().start()
    home = Path(tempfile.mkdtemp(prefix="qqdigest-demo-"))
    try:
        cfg = {
            "onebot": {"base_url": onebot.base_url},
            "groups": [
                {"id": "700001", "name": "AI 工具折腾群"},
                {"id": "700002", "name": "摄影与咖啡"},
            ],
            "me": {"names": ["阿瓜"], "qq": "1008611"},
            "collect": {"window_hours": 24, "page_size": 20},
            "llm": {"base_url": llm.base_url, "api_key": "sk-demo", "model": "demo-model", "max_input_chars": 30000},
            "report": {"dir": "reports", "timezone": "Asia/Shanghai"},
        }
        (home / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env["QQDIGEST_HOME"] = str(home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPYCACHEPREFIX"] = str(home / ".pycache")

        bundle_path = examples / "sample-bundle.json"
        subprocess.run([str(ROOT / "bin" / "qq-digest"), "bundle", "--collect", "--since", "24h", "--out", str(bundle_path)], env=env, check=True)

        proc = subprocess.run(
            [str(ROOT / "bin" / "qq-digest"), "digest", "--since", "24h", "--json"],
            env=env, check=True, capture_output=True, text=True,
        )
        paths = json.loads(proc.stdout)["paths"]
        shutil.copyfile(paths["html"], examples / "demo.html")
        shutil.copyfile(paths["md"], examples / "demo.md")
        shutil.copyfile(paths["json"], examples / "sample-summary.json")
        print("示例已生成：", examples / "demo.html")
    finally:
        onebot.stop()
        llm.stop()
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()

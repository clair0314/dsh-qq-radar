"""配置：默认值 + 用户配置 + 环境变量。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .util import deep_merge, log

DEFAULT_CONFIG = {
    "onebot": {
        "base_url": "http://127.0.0.1:3000",
        "token": "",
        "timeout": 20,
        "api_style": "path",  # path: POST {base}/{action}；root: POST {base} 带 action 字段
    },
    # 空列表 = 自动读取机器人所在的全部群；也可以填 group id / {"id","name","alias"}
    "groups": [],
    "exclude_ids": [],
    "me": {"names": [], "qq": ""},
    "collect": {
        "window_hours": 24,
        "max_messages_per_group": 3000,
        "page_size": 100,
    },
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.3,
        "timeout": 120,
        "max_input_chars": 60000,
        "concurrency": 3,
        "json_mode": True,
    },
    "report": {
        "dir": "reports",
        "timezone": "Asia/Shanghai",
        "keep_days": 90,
    },
    # 飞书自定义机器人：群设置 → 群机器人 → 添加 → 自定义机器人
    "push": {
        "feishu_webhook": "",
        "feishu_secret": "",
    },
}

_ENV_MAP = {
    "QQDIGEST_ONEBOT_URL": ("onebot", "base_url"),
    "QQDIGEST_ONEBOT_TOKEN": ("onebot", "token"),
    "QQDIGEST_LLM_BASE_URL": ("llm", "base_url"),
    "QQDIGEST_LLM_API_KEY": ("llm", "api_key"),
    "QQDIGEST_LLM_MODEL": ("llm", "model"),
    "QQDIGEST_FEISHU_WEBHOOK": ("push", "feishu_webhook"),
    "QQDIGEST_FEISHU_SECRET": ("push", "feishu_secret"),
}


def home_dir() -> Path:
    raw = os.environ.get("QQDIGEST_HOME") or "~/.qq-digest"
    return Path(os.path.expanduser(raw)).resolve()


def ensure_home() -> Path:
    home = home_dir()
    (home / "reports").mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home, 0o700)
    except OSError:
        pass
    return home


def config_path() -> Path:
    raw = os.environ.get("QQDIGEST_CONFIG")
    if raw:
        return Path(os.path.expanduser(raw)).resolve()
    return home_dir() / "config.json"


def load_config(path: str | Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    p = Path(path) if path else config_path()
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                user = json.load(f)
        except json.JSONDecodeError as exc:
            raise SystemExit("配置文件不是合法 JSON：%s\n%s" % (p, exc))
        cfg = deep_merge(cfg, user or {})
    for env, (section, key) in _ENV_MAP.items():
        val = os.environ.get(env)
        if val:
            cfg.setdefault(section, {})[key] = val

    # 兼容 groups: "all"
    raw_groups = cfg.get("groups")
    if isinstance(raw_groups, str):
        cfg["groups"] = [] if raw_groups.lower() in ("all", "*", "全部") else [raw_groups]
    cfg["groups"] = normalize_groups(cfg.get("groups") or [])
    cfg["exclude_ids"] = [str(x) for x in (cfg.get("exclude_ids") or [])]
    cfg["_path"] = str(p)
    return cfg


def normalize_groups(raw) -> list:
    out = []
    for item in raw or []:
        if isinstance(item, (int, str)):
            gid = str(item).strip()
            if gid:
                out.append({"id": gid, "name": "", "alias": ""})
        elif isinstance(item, dict):
            gid = str(item.get("id") or item.get("group_id") or "").strip()
            if not gid:
                continue
            out.append({
                "id": gid,
                "name": str(item.get("name") or "").strip(),
                "alias": str(item.get("alias") or "").strip(),
            })
    # 去重
    seen, uniq = set(), []
    for g in out:
        if g["id"] not in seen:
            seen.add(g["id"])
            uniq.append(g)
    return uniq


def report_dir(cfg: dict) -> Path:
    raw = cfg.get("report", {}).get("dir") or "reports"
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        p = home_dir() / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_config(cfg: dict, path: str | Path | None = None) -> Path:
    p = Path(path) if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    user = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(user, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return p


def mask_secret(secret: str, keep: int = 4) -> str:
    if not secret:
        return ""
    if len(secret) <= keep * 2:
        return "*" * len(secret)
    return secret[:keep] + "…" + secret[-keep:]


def print_config_summary(cfg: dict) -> None:
    ob = cfg["onebot"]
    llm = cfg["llm"]
    log("配置文件：%s", cfg.get("_path"))
    log("OneBot：%s（token %s）", ob["base_url"], mask_secret(ob.get("token", "")) or "未设置")
    log("群：%s", "自动（机器人所在全部群）" if not cfg["groups"] else "、".join(g["name"] or g["id"] for g in cfg["groups"]))
    if llm.get("api_key"):
        log("模型：%s @ %s", llm["model"], llm["base_url"])
    else:
        log("模型：未配置 API key，将使用本地规则摘要（或在 DSH 里由 AI 代写）")

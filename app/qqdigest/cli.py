"""命令行入口：init / check / groups / collect / bundle / digest / render。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, feishu, llm, report, summarize
from .bundle import build_bundle, bundle_to_text
from .collect import collect, make_client, resolve_targets
from .config import (
    DEFAULT_CONFIG,
    config_path,
    ensure_home,
    home_dir,
    load_config,
    print_config_summary,
    save_config,
)
from .onebot import OneBotError
from .store import Store
from .util import atomic_write, fmt_local, log, now_utc, parse_since

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_ONEBOT = 3
EXIT_LLM = 4
EXIT_PUSH = 5


def _window(cfg: dict, since: str | None):
    tz_name = cfg.get("report", {}).get("timezone", "Asia/Shanghai")
    if not since:
        hours = cfg.get("collect", {}).get("window_hours", 24) or 24
        since = "%sh" % hours
    end = now_utc()
    start = parse_since(since, tz_name, end)
    if start >= end:
        raise SystemExit("--since 必须早于现在")
    return int(start.timestamp()), int(end.timestamp()), tz_name


def _store(cfg: dict) -> Store:
    return Store(home_dir() / "messages.db")


def _emit(data, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ commands
def cmd_init(args) -> int:
    ensure_home()
    path = config_path()
    if path.exists() and not args.force:
        log("配置已存在：%s（加 --force 覆盖）", path)
        return EXIT_OK
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if args.base_url:
        cfg["onebot"]["base_url"] = args.base_url
    if args.token:
        cfg["onebot"]["token"] = args.token
    cfg["groups"] = []
    save_config(cfg, path)
    log("已创建配置：%s", path)
    log("下一步：")
    log("  1) 启动 NapCat / Lagrange，确认 OneBot HTTP 端口（默认 3000）")
    log("  2) 运行 `qq-digest check` 看能否连上、有哪些群")
    log("  3) 把要总结的群写进 config.json 的 groups，或留空表示全部群")
    return EXIT_OK


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    if not os.path.exists(os.path.expanduser(args.config or str(config_path()))) and not os.environ.get("QQDIGEST_ONEBOT_URL"):
        log("还没有配置文件：%s", config_path())
        log("先运行 `qq-digest init`，再填写 onebot.token 和目标群。")
        return EXIT_CONFIG
    print_config_summary(cfg)
    client = make_client(cfg)
    try:
        info = client.get_login_info()
        log("已连接，登录账号：%s（%s）", info.get("nickname", "?"), info.get("user_id", "?"))
        groups = client.get_group_list()
    except OneBotError as exc:
        log("连接失败：%s", exc)
        return EXIT_ONEBOT
    log("机器人共在 %d 个群：", len(groups))
    for group in groups:
        print("  %-12s %s" % (group["id"], group["name"]))
    if not cfg["groups"]:
        log("配置里 groups 为空 = 自动总结以上全部群。")
    return EXIT_OK


def cmd_groups(args) -> int:
    cfg = load_config(args.config)
    client = make_client(cfg)
    try:
        groups = client.get_group_list()
    except OneBotError as exc:
        log("连接失败：%s", exc)
        return EXIT_ONEBOT
    if args.json:
        print(json.dumps(groups, ensure_ascii=False, indent=2))
    else:
        for group in groups:
            print("%s\t%s" % (group["id"], group["name"]))
    return EXIT_OK


def cmd_collect(args) -> int:
    cfg = load_config(args.config)
    start, end, _ = _window(cfg, args.since)
    store = _store(cfg)
    try:
        result = collect(cfg, start, store, only_groups=args.group, max_messages=args.max)
    finally:
        store.close()
    failed = [g for g in result["groups"] if g.get("error")]
    if args.json:
        _emit(result, True)
    if failed:
        log("%d 个群拉取失败。", len(failed))
        return EXIT_ONEBOT
    return EXIT_OK


def cmd_bundle(args) -> int:
    cfg = load_config(args.config)
    start, end, tz_name = _window(cfg, args.since)
    store = _store(cfg)
    try:
        if args.collect:
            collect(cfg, start, store, only_groups=args.group, max_messages=args.max)
        bundle = build_bundle(cfg, store, start, end)
    finally:
        store.close()

    if not bundle["groups"]:
        log("窗口（%s）内没有消息。先运行 `qq-digest collect --since %s` 或检查群配置。", args.since or "24h", args.since or "24h")
        return EXIT_ONEBOT

    if args.out:
        path = atomic_write(args.out, json.dumps(bundle, ensure_ascii=False, indent=2))
        log("素材已写入：%s", path)
    if args.split:
        out_dir = Path(os.path.expanduser(args.split))
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, group in enumerate(bundle["groups"], 1):
            stats = group["stats"]
            head = "# %s（%s 条消息 / %s 人 / 峰值 %s）\n\n" % (
                group["name"], stats.get("messages"), stats.get("people"), stats.get("peak_hour")
            )
            safe = "".join(c if c not in '/\\:*?"<>|' else "_" for c in str(group["name"]))[:40]
            path = out_dir / ("%02d-%s.txt" % (i, safe))
            atomic_write(path, head + group["transcript"])
            log("已拆出：%s", path)
    if args.text:
        print(bundle_to_text(bundle))
    elif not args.out:
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_digest(args) -> int:
    cfg = load_config(args.config)
    start, end, tz_name = _window(cfg, args.since)
    store = _store(cfg)
    try:
        result = collect(cfg, start, store, only_groups=args.group, max_messages=args.max)
        failed = {g["id"] for g in result["groups"] if g.get("error")}
        bundle = build_bundle(cfg, store, start, end)
    finally:
        store.close()

    bundle["groups"] = [g for g in bundle["groups"] if g["id"] not in failed]
    if not bundle["groups"]:
        log("窗口（%s）内没有可总结的消息。", args.since or "24h")
        return EXIT_ONEBOT

    summary = summarize.summarize(bundle, cfg, use_llm=not args.no_llm)
    paths = report.write_reports(summary, cfg, bundle)
    if args.open:
        report.open_in_browser(paths["html"])

    if args.json:
        print(json.dumps({"paths": paths, "totals": summary["totals"], "ai": summary["ai"]}, ensure_ascii=False, indent=2))
    else:
        print(report.render_markdown(summary))
        log("HTML：%s", paths["html"])
        log("Markdown：%s", paths["md"])
    if summary.get("errors"):
        log("注意：%d 个群的 AI 总结失败，已用规则摘要兜底。", len(summary["errors"]))
    return EXIT_OK


def cmd_render(args) -> int:
    cfg = load_config(args.config)
    summary_path = Path(os.path.expanduser(args.summary))
    if not summary_path.exists():
        log("找不到摘要文件：%s", summary_path)
        return EXIT_CONFIG
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log("摘要不是合法 JSON：%s", exc)
        return EXIT_CONFIG
    bundle = None
    if args.bundle:
        try:
            bundle = json.loads(Path(os.path.expanduser(args.bundle)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log("素材文件读取失败：%s", exc)
    paths = report.write_reports(summary, cfg, bundle)
    if args.open:
        report.open_in_browser(paths["html"])
    print(json.dumps(paths, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_push(args) -> int:
    cfg = load_config(args.config)
    summary_path = Path(os.path.expanduser(args.summary))
    if not summary_path.exists():
        log("找不到摘要文件：%s", summary_path)
        return EXIT_CONFIG
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log("摘要不是合法 JSON：%s", exc)
        return EXIT_CONFIG
    bundle = None
    if args.bundle:
        try:
            bundle = json.loads(Path(os.path.expanduser(args.bundle)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log("素材文件读取失败：%s", exc)
    summary = report.hydrate_summary(summary, bundle)

    webhook = args.webhook or (cfg.get("push") or {}).get("feishu_webhook") or ""
    secret = args.secret or (cfg.get("push") or {}).get("feishu_secret") or ""
    if args.text:
        payload = feishu.sign_payload({"msg_type": "text", "content": {"text": feishu.build_text(summary)}}, secret)
    else:
        payload = feishu.build_payload(summary, secret, title=args.title)

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not webhook:
        log("没有配置飞书 Webhook：先在群里加「自定义机器人」，把地址填进 config.json 的 push.feishu_webhook，或加 --webhook")
        return EXIT_CONFIG
    result = feishu.send(webhook, payload)
    log("已推送到飞书")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_prune(args) -> int:
    cfg = load_config(args.config)
    store = _store(cfg)
    try:
        days = args.days or cfg.get("report", {}).get("keep_days", 90)
        removed = store.prune(days)
    finally:
        store.close()
    log("已清理 %s 天前的消息：%d 条", days, removed)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qq-digest",
        description="把关注的 QQ 群聊，总结成一份每天能读的日报。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  qq-digest init                     # 生成配置文件
  qq-digest check                    # 测试 OneBot 连接、看群列表
  qq-digest digest --since 24h       # 拉消息 + AI 总结 + 生成日报
  qq-digest digest --no-llm          # 不调用模型，输出规则摘要
  qq-digest bundle --out b.json      # 只导出素材，交给 AI 代写
  qq-digest render --summary s.json  # 用现成摘要渲染 HTML
  qq-digest push --summary s.json    # 把摘要推到飞书群机器人
""",
    )
    parser.add_argument("--config", help="配置文件路径（默认 ~/.qq-digest/config.json）")
    parser.add_argument("--home", help="数据目录（默认 ~/.qq-digest）")
    parser.add_argument("--version", action="version", version="qq-digest %s" % __version__)
    sub = parser.add_subparsers(dest="command", metavar="命令")

    p = sub.add_parser("init", help="生成配置文件")
    p.add_argument("--force", action="store_true", help="覆盖已有配置")
    p.add_argument("--base-url", help="OneBot HTTP 地址，如 http://127.0.0.1:3000")
    p.add_argument("--token", help="OneBot access token")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="测试连接并列出机器人所在的群")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("groups", help="列出机器人所在的群")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_groups)

    p = sub.add_parser("collect", help="只拉取消息入库")
    p.add_argument("--since", help="时间窗口：24h / 3d / today / 2026-11-10")
    p.add_argument("--group", action="append", help="只处理指定群（可重复）")
    p.add_argument("--max", type=int, help="每个群最多拉多少条")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("bundle", help="导出给模型看的素材（JSON）")
    p.add_argument("--since", help="时间窗口")
    p.add_argument("--group", action="append", help="只处理指定群")
    p.add_argument("--max", type=int)
    p.add_argument("--out", help="写入文件")
    p.add_argument("--text", action="store_true", help="输出纯文本版")
    p.add_argument("--split", metavar="DIR", help="把每个群的素材拆成单独 txt（方便逐个阅读）")
    p.add_argument("--collect", action="store_true", help="先拉取再导出")
    p.set_defaults(func=cmd_bundle)

    p = sub.add_parser("digest", help="一条命令：拉取 + 总结 + 日报")
    p.add_argument("--since", help="时间窗口，默认取配置 window_hours")
    p.add_argument("--group", action="append", help="只处理指定群")
    p.add_argument("--max", type=int)
    p.add_argument("--no-llm", action="store_true", help="不调用模型，用本地规则摘要")
    p.add_argument("--open", action="store_true", help="生成后打开浏览器")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("render", help="把摘要 JSON 渲染成日报")
    p.add_argument("--summary", required=True, help="摘要 JSON 路径")
    p.add_argument("--bundle", help="素材 JSON 路径（用于附上群聊原声）")
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("push", help="把摘要推送到飞书群机器人")
    p.add_argument("--summary", required=True, help="摘要 JSON 路径")
    p.add_argument("--bundle", help="素材 JSON 路径（用于补齐统计）")
    p.add_argument("--webhook", help="飞书自定义机器人 Webhook（默认读配置）")
    p.add_argument("--secret", help="签名校验密钥（开启签名时填）")
    p.add_argument("--title", help="卡片标题（默认 QQ 群聊日报 · 日期）")
    p.add_argument("--text", action="store_true", help="发纯文本而不是卡片")
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的内容，不真的发")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("prune", help="清理旧消息")
    p.add_argument("--days", type=int)
    p.set_defaults(func=cmd_prune)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.home:
        os.environ["QQDIGEST_HOME"] = args.home
    if args.config:
        os.environ["QQDIGEST_CONFIG"] = args.config
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_OK
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("已取消")
        return 130
    except OneBotError as exc:
        log("%s", exc)
        return EXIT_ONEBOT
    except llm.LLMError as exc:
        log("%s", exc)
        return EXIT_LLM
    except feishu.FeishuError as exc:
        log("%s", exc)
        return EXIT_PUSH
    except ValueError as exc:
        log("%s", exc)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())

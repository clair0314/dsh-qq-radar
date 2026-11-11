"""拉取群历史消息并入本地库。"""
from __future__ import annotations

from .normalize import normalize_message
from .onebot import OneBotClient, OneBotError
from .store import Store
from .util import fmt_local, log

MAX_PAGES = 80


def make_client(cfg: dict) -> OneBotClient:
    ob = cfg.get("onebot", {})
    return OneBotClient(
        base_url=ob.get("base_url", ""),
        token=ob.get("token", ""),
        timeout=ob.get("timeout", 20),
        api_style=ob.get("api_style", "path"),
    )


def resolve_targets(cfg: dict, client: OneBotClient, store: Store | None = None) -> list:
    """确定要总结哪些群：配置优先，否则用机器人加入的全部群。"""
    configured = cfg.get("groups") or []
    cached = store.group_names() if store else {}
    exclude = set(cfg.get("exclude_ids") or [])

    if configured:
        targets = []
        for item in configured:
            gid = str(item["id"])
            if gid in exclude:
                continue
            name = item.get("name") or cached.get(gid, "")
            targets.append({"id": gid, "name": name, "alias": item.get("alias") or ""})
        # 给没名字的群补一次详情
        for t in targets:
            if not t["name"]:
                try:
                    info = client.get_group_info(t["id"])
                    if info.get("name"):
                        t["name"] = info["name"]
                except OneBotError:
                    pass
        return targets

    groups = client.get_group_list()
    targets = []
    for g in groups:
        gid = str(g["id"])
        if gid in exclude:
            continue
        targets.append({"id": gid, "name": g.get("name") or cached.get(gid, ""), "alias": ""})
    return targets


def collect_group(
    client: OneBotClient,
    store: Store,
    target: dict,
    since_ts: int,
    max_messages: int = 3000,
    page_size: int = 100,
    tz_name: str = "Asia/Shanghai",
) -> dict:
    gid = str(target["id"])
    name = target.get("name") or ""
    seq = 0
    fetched = {}
    pages = 0
    reached_start = False
    while pages < MAX_PAGES and len(fetched) < max_messages:
        pages += 1
        batch = client.get_group_msg_history(gid, message_seq=seq, count=page_size)
        if not batch:
            break
        page_new = 0
        page_seqs = []
        page_ids = []
        oldest = None
        for raw in batch:
            row = normalize_message(raw, tz_name)
            if row is None:
                continue
            page_ids.append(row["id"])
            if row["id"] not in fetched:
                fetched[row["id"]] = row
                page_new += 1
            oldest = row["ts"] if oldest is None else min(oldest, row["ts"])
            for key in ("message_seq", "message_id", "real_id"):
                value = raw.get(key)
                if value not in (None, "", 0, "0"):
                    try:
                        page_seqs.append(int(value))
                    except (TypeError, ValueError):
                        pass
        if page_new == 0:
            break
        if oldest is not None and oldest < since_ts:
            reached_start = True
            break
        if len(fetched) >= max_messages or len(batch) < page_size:
            break
        if not page_seqs:
            break
        next_seq = min(page_seqs)
        if next_seq <= 0 or next_seq == seq:
            # 有的实现用 message_id 当 seq，换一个值再试一次
            ids = []
            for value in page_ids:
                try:
                    ids.append(int(value))
                except (TypeError, ValueError):
                    pass
            alt = min(ids) if ids else None
            if alt is None or alt == seq:
                break
            next_seq = alt
        seq = next_seq

    rows = [r for r in fetched.values() if r["ts"] >= since_ts]
    rows.sort(key=lambda r: (r["ts"], r["id"]))
    inserted, dup = store.add_messages(gid, rows) if rows else (0, 0)
    if name:
        store.upsert_group(gid, name)
    stats = {
        "id": gid,
        "name": name or gid,
        "fetched": len(fetched),
        "in_window": len(rows),
        "new": inserted,
        "duplicate": dup,
        "pages": pages,
        "reached_start": reached_start,
        "first": fmt_local(rows[0]["ts"], tz_name) if rows else "",
        "last": fmt_local(rows[-1]["ts"], tz_name) if rows else "",
    }
    return stats


def collect(
    cfg: dict,
    since_ts: int,
    store: Store,
    client: OneBotClient | None = None,
    only_groups: list | None = None,
    max_messages: int | None = None,
) -> dict:
    client = client or make_client(cfg)
    tz_name = cfg.get("report", {}).get("timezone", "Asia/Shanghai")
    targets = resolve_targets(cfg, client, store)
    if only_groups:
        wanted = {str(x) for x in only_groups}
        targets = [t for t in targets if str(t["id"]) in wanted]
    if not targets:
        log("没有找到任何群。请先在 config.json 里填 groups，或确认机器人已进群。")
        return {"groups": [], "targets": 0}

    max_messages = int(max_messages or cfg.get("collect", {}).get("max_messages_per_group", 3000))
    page_size = int(cfg.get("collect", {}).get("page_size", 100))
    results = []
    for target in targets:
        label = target.get("name") or target["id"]
        log("拉取「%s」…", label)
        try:
            stat = collect_group(client, store, target, since_ts, max_messages, page_size, tz_name)
        except OneBotError as exc:
            log("「%s」拉取失败：%s", label, exc)
            results.append({"id": target["id"], "name": label, "error": str(exc)})
            continue
        log(
            "  %s 条窗口内消息（新入库 %s，翻页 %s）",
            stat["in_window"],
            stat["new"],
            stat["pages"],
        )
        results.append(stat)
    return {"groups": results, "targets": len(targets)}

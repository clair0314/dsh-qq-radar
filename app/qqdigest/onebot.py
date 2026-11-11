"""OneBot 11 客户端（兼容 NapCat / Lagrange / LLOneBot 等实现）。"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


class OneBotError(RuntimeError):
    """OneBot 调用失败。"""


class OneBotClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 20, api_style: str = "path"):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = float(timeout or 20)
        self.api_style = api_style or "path"

    # ------------------------------------------------------------------ 底层
    def _headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def call(self, action: str, **params):
        if self.api_style == "root":
            url = self.base_url
            body = {"action": action, "params": params}
        else:
            url = "%s/%s" % (self.base_url, action.lstrip("/"))
            body = params

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise OneBotError("OneBot 返回 HTTP %s（%s）：%s" % (exc.code, action, detail))
        except urllib.error.URLError as exc:
            raise OneBotError(
                "连不上 OneBot 服务 %s：%s\n请确认 NapCat/Lagrange 已启动，且 HTTP 服务端口与配置一致。"
                % (self.base_url, exc.reason)
            )
        except (socket.timeout, TimeoutError):
            raise OneBotError("OneBot 请求超时（%s）：%s" % (action, self.base_url))

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise OneBotError("OneBot 返回的不是 JSON：%s" % raw[:300])

        # 有些实现直接返回 data
        if not isinstance(payload, dict):
            return payload
        retcode = payload.get("retcode")
        status = payload.get("status")
        if retcode not in (0, None) or status in ("failed", "error"):
            msg = payload.get("message") or payload.get("wording") or payload.get("msg") or raw[:300]
            raise OneBotError("OneBot 调用失败（%s, retcode=%s）：%s" % (action, retcode, msg))
        return payload.get("data", payload)

    # ------------------------------------------------------------------ 接口
    def get_login_info(self) -> dict:
        data = self.call("get_login_info") or {}
        if isinstance(data, dict) and data.get("user_id"):
            return {"user_id": str(data.get("user_id")), "nickname": data.get("nickname") or ""}
        return data if isinstance(data, dict) else {}

    def get_group_list(self) -> list:
        data = self.call("get_group_list")
        if isinstance(data, dict):
            data = data.get("groups") or data.get("list") or []
        out = []
        for item in data or []:
            if not isinstance(item, dict):
                continue
            gid = item.get("group_id") or item.get("id")
            if gid is None:
                continue
            out.append({
                "id": str(gid),
                "name": item.get("group_name") or item.get("name") or "",
                "member_count": item.get("member_count") or item.get("member_num"),
            })
        return out

    def get_group_info(self, group_id: str) -> dict:
        data = self.call("get_group_info", group_id=_as_int(group_id)) or {}
        if isinstance(data, dict):
            return {
                "id": str(data.get("group_id") or group_id),
                "name": data.get("group_name") or data.get("name") or "",
                "member_count": data.get("member_count"),
            }
        return {"id": str(group_id), "name": ""}

    def get_group_msg_history(self, group_id: str, message_seq: int = 0, count: int = 100) -> list:
        data = self.call(
            "get_group_msg_history",
            group_id=_as_int(group_id),
            message_seq=int(message_seq or 0),
            count=int(count or 100),
        )
        if isinstance(data, dict):
            data = data.get("messages") or data.get("message") or []
        if isinstance(data, dict):
            data = [data]
        return [m for m in (data or []) if isinstance(m, dict)]


def _as_int(value):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return value

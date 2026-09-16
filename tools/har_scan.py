#!/usr/bin/env python3
"""Scan a HAR capture for Ai-LiNK requests, tokens and the app's token renewal.

    python3 tools/har_scan.py capture.har

It prints, for every request:
  * local timestamp, host, path, status
  * any JWT found in request/response headers or bodies, decoded to its
    ``current`` (issuance time) / ``exp`` / ``username`` claims
  * a flag when a body carries something that looks like a credential
    (refreshToken / password / captcha / mobile)

and finally a timeline of every distinct token issuance seen, plus which request
appears to have minted a new one.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
CRED_WORDS = ("refreshtoken", "password", "pwd", "captcha", "mobile")


def decode_jwt(token: str) -> dict | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return None
    out = {k: v for k, v in data.items() if k not in ("refreshToken",)}
    if "refreshToken" in data:
        out["refreshToken"] = f"<{len(str(data['refreshToken']))} chars>"
    if isinstance(data.get("current"), (int, float)):
        out["current_time"] = datetime.fromtimestamp(data["current"] / 1000).strftime(
            "%m-%d %H:%M:%S.%f"
        )[:-3]
    if isinstance(data.get("exp"), (int, float)):
        out["exp_time"] = datetime.fromtimestamp(data["exp"]).strftime("%m-%d %H:%M:%S")
    return out


def find_tokens(text: str) -> set[str]:
    return set(JWT_RE.findall(text or ""))


def wall_clock(when: str) -> str:
    """Return HH:MM:SS.mmm exactly as written in the HAR.

    Stream exports local time but labels it as UTC ("…Z"), so converting it
    would shift it by the local offset.  Keep the recorded wall clock.
    """
    match = re.search(r"T(\d{2}:\d{2}:\d{2})(?:\.(\d{3}))?", when)
    if not match:
        return when[11:23]
    return f"{match.group(1)}.{match.group(2) or '000'}"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    har = json.load(open(sys.argv[1]))

    issuances: dict[str, list[str]] = {}
    print(f"{'时间(本地)':<14} {'方法':<5} {'主机+路径':<62} {'状态':<5} 备注")
    print("-" * 130)

    for entry in har["log"]["entries"]:
        req, resp = entry["request"], entry.get("response", {})
        local = wall_clock(entry.get("startedDateTime", ""))
        url = urlparse(req["url"])
        path = f"{url.netloc}{url.path}"
        if len(path) > 60:
            path = path[:57] + "…"

        notes: list[str] = []
        # credentials in the body
        body = (req.get("postData") or {}).get("text", "") or ""
        low = body.lower()
        for word in CRED_WORDS:
            if word in low:
                notes.append(f"body含{word}")
        # tokens anywhere
        carriers = {
            "请求头": json.dumps(req.get("headers", [])),
            "请求体": body,
            "响应头": json.dumps(resp.get("headers", [])),
            "响应体": (resp.get("content") or {}).get("text", "") or "",
        }
        for where, blob in carriers.items():
            for token in find_tokens(blob):
                info = decode_jwt(token)
                if not info:
                    continue
                stamp = f"{info.get('current_time', '?')} 签发 / exp {info.get('exp_time', '?')}"
                notes.append(f"{where}有JWT[{stamp}]")
                issuances.setdefault(info.get("current_time", "?"), []).append(path)

        print(
            f"{local:<14} {req['method']:<5} {path:<62} {str(resp.get('status','')):<5} "
            + "；".join(notes)
        )

    print()
    print("=== 看到的 token 签发时间线（去重）===")
    for minted in sorted(issuances):
        where = sorted(set(issuances[minted]))
        print(f"  {minted}  出现于 {len(where)} 处：{', '.join(where[:3])}")
    if len(issuances) <= 1:
        print("  → 只有一张卡：这次抓包**没有出现换卡动作**，换卡不是靠 HTTP 接口")
    else:
        print("  → 有多张卡：新出现的那张就是这次抓到的换卡结果，往上找它第一次出现在哪个响应里")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
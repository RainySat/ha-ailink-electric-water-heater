#!/usr/bin/env python3
"""Watch how long a captured Ai-LiNK token actually stays usable.

The cloud accepts a token well past its JWT ``exp``, so the only way to learn the
real lifetime is to keep knocking.  Run it in the background for days:

    nohup python3 tools/token_watchdog.py --interval 1800 --days 7 \
        --log .token-watchdog.log --capture ~/.config/ailink/capture.env &

The capture file holds shell assignments produced by ``tools/probe.py``:
    AL_TOK=...  AL_UID=...  AL_FAM=...  AL_CK=...
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import pathlib
import sys
import time
import types
from datetime import datetime

COMPONENT = (
    pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "ailink_ewh"
)


def _load(module: str):
    if "ailink_ewh" not in sys.modules:
        package = types.ModuleType("ailink_ewh")
        package.__path__ = [str(COMPONENT)]
        sys.modules["ailink_ewh"] = package
    name = f"ailink_ewh.{module}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, COMPONENT / f"{module}.py")
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


api = _load("api")
protocol = _load("protocol")


def read_capture(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in pathlib.Path(path).read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key:
            values[key] = value.strip("'\"")
    return values


async def knock(client: "api.AilinkClient", device_id: str) -> tuple[bool, str]:
    """Return (ok, description)."""
    try:
        status = await client.async_get_device_status(device_id)
    except api.AilinkError as err:
        return False, f"API 错误 {type(err).__name__}: {err}"
    output = protocol.extract_output_data(status)
    if not output:
        return False, "云端返回空数据（token 已不被接受）"
    return True, (
        f"实时 {output.get('realTemp')}°C / 设定 {output.get('heatingTemp')}°C "
        f"/ 电源 {output.get('powerStatus')}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", default=str(pathlib.Path.home() / ".config/ailink/capture.env"))
    parser.add_argument("--log", default=".token-watchdog.log")
    parser.add_argument("--interval", type=int, default=1800, help="秒，默认 30 分钟")
    parser.add_argument("--days", type=float, default=7)
    args = parser.parse_args()

    import aiohttp

    creds = read_capture(args.capture)
    token = creds["AL_TOK"]
    deadline = time.time() + args.days * 86400
    log_path = pathlib.Path(args.log)
    first_ok = None
    last_ok = None
    if log_path.exists():
        # Keep the uptime counter across restarts of the watchdog itself.
        for existing in log_path.read_text().splitlines():
            if "  OK  " in existing:
                first_ok = existing.split("  ", 1)[0]
                break

    async with aiohttp.ClientSession() as session:
        client = api.AilinkClient(
            session,
            access_token=token,
            user_id=creds["AL_UID"],
            family_id=creds["AL_FAM"],
            cookie=creds.get("AL_CK", ""),
        )
        device_id = ""
        while time.time() < deadline:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if not device_id:
                try:
                    devices = await client.async_get_devices()
                except api.AilinkError as err:
                    devices = []
                    line = f"{stamp}  枚举设备失败: {err}"
                else:
                    line = f"{stamp}  账号设备数 {len(devices)}"
                if devices:
                    device_id = devices[0]["device_id"]
                    line += "  → 立刻探活"
            if device_id:
                # Ask for the account's newest token first: if the phone app has
                # logged in / rotated since the last round, this picks it up and
                # also proves which endpoint the app's renewal relies on.
                before = client.token
                renewed = await client.async_renew_token()
                rotation = (
                    "App 换过卡（getLastToken 拿到新 token）"
                    if client.token != before
                    else "无更新"
                )
                ok, detail = await knock(client, device_id)
                marker = "OK  " if ok else "FAIL"
                if ok:
                    last_ok = stamp
                    if first_ok is None:
                        first_ok = stamp
                    alive = ""
                    if last_ok:
                        try:
                            d0 = datetime.strptime(first_ok, "%Y-%m-%d %H:%M:%S")
                            d1 = datetime.strptime(last_ok, "%Y-%m-%d %H:%M:%S")
                            alive = f" | 已连续可用 {(d1 - d0).total_seconds() / 3600:.1f} 小时"
                        except ValueError:
                            pass
                    line = (
                        f"{stamp}  {marker} {detail} | exp={client.token_expires_at}"
                        f" | getLastToken: {rotation} (返回={renewed}){alive}"
                    )
                else:
                    line = f"{stamp}  {marker} {detail} | getLastToken: {rotation}"
            with log_path.open("a") as handle:
                handle.write(line + "\n")
            await asyncio.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
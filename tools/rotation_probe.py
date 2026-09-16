#!/usr/bin/env python3
"""Decide whether we can mint a fresh token ourselves, without the phone app.

Rule under test (from the 11:50 capture): the cloud returns a NEW token in the
``Authorization`` response header when the request presents the account's
*current* token and that token has expired.

The experiment therefore has to run *after* the current token expires, with the
current token itself:

  1. ask /api/getLastToken for the account's newest token (T) and show its exp
  2. if T is expired, replay the app's own ``//appDevice/getAntifreeze`` call
     with T and look for an ``Authorization`` response header
  3. also try a plain ``getDeviceCurrInfo`` with T, to see whether the rotation
     is endpoint specific

Usage: nohup python3 tools/rotation_probe.py --at 12:21:30 --log /tmp/rotation.log &
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import pathlib
import sys
import time
import types
from datetime import datetime

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "ailink_ewh"


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
const = _load("const")


def read_capture(path: pathlib.Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key:
            out[key] = value.strip("'\"")
    return out


def describe(token: str) -> str:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        issued = datetime.fromtimestamp(data["current"] / 1000).strftime("%m-%d %H:%M:%S")
        exp = datetime.fromtimestamp(data["exp"]).strftime("%m-%d %H:%M:%S")
        return f"issued={issued} exp={exp}"
    except Exception:  # noqa: BLE001
        return "unparsable"


async def one_round(log, device_id: str, session) -> None:
    import aiohttp

    creds = read_capture(pathlib.Path.home() / ".config/ailink/capture.env")
    client = api.AilinkClient(
        session,
        access_token=creds["AL_TOK"],
        user_id=creds["AL_UID"],
        family_id=creds["AL_FAM"],
        cookie=creds.get("AL_CK", ""),
    )

    stamp = datetime.now().strftime("%H:%M:%S")
    before = client.token
    ok = await client.async_renew_token()
    newest = client.token
    log(f"\n[{stamp}] === round start ===")
    log(f"  getLastToken: {'ok' if ok else 'failed'}; newest token {describe(newest)}")
    if newest == before:
        log("  (no newer token on the server, ours is the newest)")

    exp = client.token_expires_at
    if exp is not None and exp.timestamp() > time.time():
        log(f"  the newest token is still valid for {int(exp.timestamp() - time.time())}s, skipping this round")
        return

    # 1) replay the app request (including the double slash, exactly as captured)
    antifreeze = await client._post(  # noqa: SLF001 - deliberate low level probe
        "//appDevice/getAntifreeze",
        {
            "familyId": client.family_id,
            "userId": client.user_id,
            "encode": client._encode(  # noqa: SLF001
                {"familyId": client.family_id, "userId": client.user_id}
            ),
        },
    )
    log(f"  getAntifreeze returned: {json.dumps(antifreeze, ensure_ascii=False)[:90]}")

    # 2) does a plain read endpoint mint as well?
    status = await client.async_get_device_status(device_id)
    temp = (status.get("appDeviceStatusInfoEntity") or {}).get("statusInfo", "")
    log(f"  getDeviceCurrInfo returned: model={status.get('productModel')!r} statusInfo length={len(temp)}")
    log(f"  token the client holds after both requests: {describe(client.token)}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", default="", help="start time HH:MM:SS; leave empty to start immediately")
    parser.add_argument("--log", default="/tmp/ailink/rotation_probe.log")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--gap", type=int, default=120, help="seconds between rounds")
    parser.add_argument("--device-id", required=True, help="device id used to read the status")
    args = parser.parse_args()

    import aiohttp

    log_path = pathlib.Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(text: str) -> None:
        with log_path.open("a") as handle:
            handle.write(text + "\n")
        print(text)

    if args.at:
        target = datetime.strptime(args.at, "%H:%M:%S").replace(
            year=datetime.now().year, month=datetime.now().month, day=datetime.now().day
        )
        wait = (target - datetime.now()).total_seconds()
        if wait > 0:
            log(f"[{datetime.now():%H:%M:%S}] waiting until {args.at} ({int(wait)}s)...")
            await asyncio.sleep(wait)

    async with aiohttp.ClientSession() as session:
        for _ in range(args.rounds):
            try:
                await one_round(log, args.device_id, session)
            except Exception as err:  # noqa: BLE001
                log(f"  round failed: {type(err).__name__}: {err}")
            await asyncio.sleep(args.gap)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
#!/usr/bin/env python3
"""Can we mint a fresh token ourselves, without the phone app?

Findings that motivate this (2026-09-16, Ai-LiNK cloud):

* the cloud hands out a NEW token in the ``Authorization`` **response** header
  (advertised via ``Access-Control-Expose-Headers: Authorization``);
* it appears to do so only when the presented token is the account's *current*
  token **and** that token has expired;
* ``POST /user/saveCID`` did it even though the call itself returned HTTP 500.

This script waits until the account's current token has expired, then knocks on
a handful of endpoints with that very token and reports which one mints a new
token.  A successful mint means the integration never needs the user to open the
phone app again.

    nohup python3 tools/mint_probe.py --at 12:57:30 --log /tmp/mint_probe.log &
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import pathlib
import sys
import time
import uuid
from datetime import datetime

import aiohttp

BASE = "https://ailink-api.hotwater.com.cn/AiLinkService"

# Endpoints the app calls right after launch; bodies taken verbatim from a capture
# where possible (a wrong body only makes the endpoint itself fail - the header
# rotation happens regardless).
CANDIDATES: tuple[tuple[str, dict], ...] = (
    ("/user/saveCID", {"cid": ""}),
    ("//appDevice/getAntifreeze", {}),
    ("/user/getUserTagInfo", {}),
    ("/appDevice/getAlertStatus", {}),
    ("/version/getLastVersion", {}),
    ("/deviceEnergy/openAPP", {"appSource": "1"}),
    ("/appDevice/getHomepageV2", {}),
    ("/appDevice/getDeviceCurrInfo", {}),
)


def decode(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def describe(token: str) -> str:
    try:
        data = decode(token)
        issued = datetime.fromtimestamp(data["current"] / 1000)
        exp = datetime.fromtimestamp(data["exp"])
        return f"issued={issued:%m-%d %H:%M:%S} exp={exp:%m-%d %H:%M:%S}"
    except Exception:  # noqa: BLE001
        return "(unparsable)"


class Probe:
    def __init__(self, session: aiohttp.ClientSession, creds: dict[str, str]) -> None:
        self.session = session
        self.token = creds["AL_TOK"].removeprefix("Bearer ").strip()
        self.user_id = creds["AL_UID"]
        self.family_id = creds["AL_FAM"]
        self.cookie = creds.get("AL_CK", "")

    async def call(self, path: str, body: dict, token: str | None = None) -> tuple[int, dict, str]:
        raw = json.dumps(
            {**body, "userId": self.user_id, "familyId": self.family_id},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        ts = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4()).upper()
        md5data = hashlib.md5(raw).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Authorization": f"Bearer {token or self.token}",
            "UserId": self.user_id,
            "version": "V1.0.1",
            "source": "IOS",
            "timestamp": ts,
            "nonce": nonce,
            "md5data": md5data,
            "sign": hashlib.md5(f"{md5data}{ts}{nonce}ng957stzh4zy3dts".encode()).hexdigest(),
            "traceId": f"{ts}-00001-{self.user_id}-00",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        async with self.session.post(f"{BASE}{path}", data=raw, headers=headers) as resp:
            text = await resp.text()
            return resp.status, dict(resp.headers), text

    async def current_token(self) -> str:
        """Ask the cloud for the account's newest token."""
        raw = json.dumps({"token": f"Bearer {self.token}"}, separators=(",", ":")).encode()
        async with self.session.post(
            f"{BASE}/api/getLastToken",
            data=raw,
            headers={"Content-Type": "application/json", "version": "V1.0.1", "source": "IOS"},
        ) as resp:
            data = json.loads(await resp.text())
        token = (data.get("info") or {}).get("token")
        if token:
            self.token = str(token).removeprefix("Bearer ").strip()
        return self.token

    @staticmethod
    def header_token(headers: dict) -> str | None:
        for key in ("Authorization", "authorization"):
            if headers.get(key):
                return headers[key].removeprefix("Bearer ").strip()
        return None


async def run_round(probe: Probe, log, device_id: str) -> None:
    token = await probe.current_token()
    exp = decode(token)["exp"]
    left = exp - time.time()
    log(f"\n[{datetime.now():%H:%M:%S}] account's newest token: {describe(token)}")
    if left > 0:
        log(f"  still valid for {int(left)}s -> skipping (rotation only happens after expiry)")
        return

    log(f"  expired {int(-left)}s ago, knocking on each candidate:")
    minted: str | None = None
    for path, body in CANDIDATES:
        status, headers, text = await probe.call(path, body, token)
        new = probe.header_token(headers)
        mark = "MINTED " if new else "none   "
        note = describe(new) if new else text[:70].replace("\n", " ")
        log(f"    {path:<32} HTTP {status:<4} {mark}  {note}")
        if new and not minted:
            minted = new
        await asyncio.sleep(0.4)

    if not minted:
        log("  -> no endpoint minted a token: rotation would depend on app-side state, so we cannot automate it")
        return

    # Is the minted token usable, and did the account record move to it?
    status, _, text = await probe.call(
        "/appDevice/getDeviceCurrInfo", {"deviceId": device_id}, minted
    )
    model = ""
    try:
        model = (json.loads(text).get("info") or {}).get("productModel", "")
    except Exception:  # noqa: BLE001
        pass
    again = await probe.current_token()
    log(f"  reading the device with the minted token: HTTP {status} model={model!r}")
    log(f"  asking getLastToken again: {describe(again)}")
    log("  => we can mint tokens ourselves; the integration no longer needs the phone app"
        if again == minted
        else "  => the token works but the account record did not move to it, needs another look")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", default="", help="start at HH:MM:SS; leave empty to start immediately")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--gap", type=int, default=120)
    parser.add_argument("--log", default="/tmp/mint_probe.log")
    parser.add_argument("--capture", default=str(pathlib.Path.home() / ".config/ailink/capture.env"))
    parser.add_argument(
        "--device-id",
        required=True,
        help="device id used to verify the minted token (see the output of tools/probe.py)",
    )
    args = parser.parse_args()

    log_path = pathlib.Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(text: str) -> None:
        with log_path.open("a") as handle:
            handle.write(text + "\n")
        print(text)

    creds = {}
    for line in pathlib.Path(args.capture).read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key:
            creds[key] = value.strip("'\"")

    if args.at:
        target = datetime.strptime(args.at, "%H:%M:%S").replace(
            year=datetime.now().year, month=datetime.now().month, day=datetime.now().day
        )
        wait = (target - datetime.now()).total_seconds()
        if wait > 0:
            log(f"[{datetime.now():%H:%M:%S}] waiting until {args.at} ({int(wait)}s) before starting...")
            await asyncio.sleep(wait)

    async with aiohttp.ClientSession() as session:
        probe = Probe(session, creds)
        for _ in range(args.rounds):
            try:
                await run_round(probe, log, args.device_id)
            except Exception as err:  # noqa: BLE001
                log(f"  round failed: {type(err).__name__}: {err}")
            await asyncio.sleep(args.gap)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
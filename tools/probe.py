#!/usr/bin/env python3
"""Validate an AI-LiNK app capture and inspect a device.

Usage:
    # 1. list every device of the account (also proves the token works)
    python3 tools/probe.py --token eyJ... --user-id YOUR_USER_ID --family-id YOUR_FAMILY_ID

    # 2. dump the reported fields of one device (the interesting part)
    python3 tools/probe.py --token ... --user-id ... --family-id ... --device-id 80A036B9128D

    # 3. ask the cloud for a fresh token (shows whether a single capture keeps working)
    python3 tools/probe.py --token ... --user-id ... --family-id ... --renew

Requires: aiohttp (pip install aiohttp)
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import pathlib
import sys
import types

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


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True, help="Authorization value from the capture (Bearer prefix optional)")
    parser.add_argument("--user-id", required=True, help="UserId request header")
    parser.add_argument("--family-id", required=True, help="familyId from the JSON request body")
    parser.add_argument("--cookie", default="", help="Cookie request header (optional)")
    parser.add_argument("--device-id", default="", help="device to inspect; leave empty to list them all")
    parser.add_argument("--renew", action="store_true", help="also test token renewal via /api/getLastToken")
    parser.add_argument("--json", action="store_true", help="dump the raw JSON of every reported field")
    args = parser.parse_args()

    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = api.AilinkClient(
            session,
            access_token=args.token,
            user_id=args.user_id,
            family_id=args.family_id,
            cookie=args.cookie,
        )

        print(f"token prefix: {client.token[:16]}... (length {len(client.token)})")
        expires = client.token_expires_at
        print(f"token expires at: {expires.isoformat() if expires else 'unknown (not a JWT, or no exp claim)'}")
        captured_token = client.token

        try:
            devices = await client.async_get_devices()
        except api.AilinkAuthError as err:
            print(f"  authentication failed: {err}")
            return 1
        except api.AilinkSignatureError as err:
            print(f"  signing rejected: {err}")
            return 1
        except api.AilinkApiError as err:
            print(f"  API error: {err}")
            return 1

        if client.token != captured_token:
            print("  the cloud handed out a new token in the response header (sliding renewal works)")
        else:
            print("  no new token in this response (that is normal - the app rotates only occasionally)")

        if not devices:
            print("\n  no device returned. The cloud does not report an error for unusable credentials,")
            print("  it just returns empty data -> token invalid/expired or user_id / family_id mismatch.")
            return 1

        print(f"\n=== {len(devices)} device(s) in this account ===")
        for device in devices:
            print(
                f"  • {device['name']}\n"
                f"      deviceId={device['device_id']} class={device['category'] or '?'} "
                f"model={device['model'] or '?'} room={device['room'] or '-'} "
                f"{'online' if device['online'] else 'offline'}"
            )

        device_id = args.device_id or next(
            (d["device_id"] for d in devices if d.get("online")), ""
        )
        if not device_id:
            print("no usable device, skipping the status read")
            return 0

        status = await client.async_get_device_status(device_id)
        output = protocol.extract_output_data(status)
        mapping = status.get("appSpaceDeviceMappingEntity") or {}
        if not output and not status.get("productModel"):
            print("\n  the cloud returned no data for this device, check the deviceId")
            return 1

        print(f"\n=== device {device_id} ===")
        print(
            json.dumps(
                {
                    "name": mapping.get("deviceName"),
                    "productMajorClassCode": status.get("productMajorClassCode"),
                    "productModel": status.get("productModel"),
                    "productSubClassCode": status.get("productSubClassCode"),
                    "productName": status.get("productName"),
                    "devState": status.get("devState"),
                    "firmware": (status.get("appDeviceStatusInfoEntity") or {}).get("ccuVersion"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        print("\n=== reported fields (outputData) ===")
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            printable = {k: v for k, v in output.items() if not isinstance(v, (dict, list))}
            for key in sorted(printable):
                print(f"  {key} = {printable[key]!r}")

        known = [k for k in protocol.KNOWN_FIELDS if k in output]
        missing = [k for k in protocol.KNOWN_FIELDS if k not in output]
        print(f"\nknown electric-water-heater fields present: {len(known)}/{len(protocol.KNOWN_FIELDS)}: {known}")
        print(f"not reported: {missing}")
        print(f"switch entities that would be created: {[s[0] for s in protocol.observed_switches(output)]}")
        print(f"derived work state: {protocol.derive_work_state(output)}")
        faults = protocol.extract_faults(status)
        print(f"faults: {faults or 'none'}")

        if args.renew:
            print("\n=== token renewal test (/api/getLastToken) ===")
            before = client.token
            before_exp = client.token_expires_at
            ok = await client.async_renew_token()
            if not ok:
                print("  renewal failed: the cloud could not derive a new token from the old one")
                print("  -> once the token stops working you would have to capture again (or log in by SMS)")
                return 2
            after_exp = client.token_expires_at
            print(f"  renewal succeeded, token changed: {before != client.token}")
            print(f"   old expiry: {before_exp.isoformat() if before_exp else 'unknown'}")
            print(f"   new expiry: {after_exp.isoformat() if after_exp else 'unknown'}")
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
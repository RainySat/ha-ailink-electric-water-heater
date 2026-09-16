#!/usr/bin/env python3
"""Validate an AI家智控 / AI-LiNK 智慧家 capture and inspect a device.

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
    parser.add_argument("--token", required=True, help="抓包得到的 Authorization 值（可带 Bearer）")
    parser.add_argument("--user-id", required=True, help="请求头 UserId")
    parser.add_argument("--family-id", required=True, help="请求体里的 familyId")
    parser.add_argument("--cookie", default="", help="请求头 Cookie（可选）")
    parser.add_argument("--device-id", default="", help="要查看的设备，留空则列出全部")
    parser.add_argument("--renew", action="store_true", help="测试 /api/getLastToken 续期")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
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

        print(f"token 前缀: {client.token[:16]}… (长度 {len(client.token)})")
        expires = client.token_expires_at
        print(f"token 过期时间: {expires.isoformat() if expires else '未知（非 JWT 或没有 exp）'}")
        captured_token = client.token

        try:
            devices = await client.async_get_devices()
        except api.AilinkAuthError as err:
            print(f"❌ 认证失败: {err}")
            return 1
        except api.AilinkSignatureError as err:
            print(f"❌ 签名被拒: {err}")
            return 1
        except api.AilinkApiError as err:
            print(f"❌ 接口报错: {err}")
            return 1

        if client.token != captured_token:
            print("✅ 云端在响应头里下发了新 token（说明滑动续期可用）")
        else:
            print("ℹ️ 本次响应未携带新 token（不代表不能用，官方 App 也只是偶尔换）")

        if not devices:
            print("\n❌ 账号下没有返回任何设备。云端对无效凭证不报错，只返回空数据，")
            print("   → 基本可以确定 token 无效/已过期，或 user_id / family_id 与手机号不匹配。")
            return 1

        print(f"\n=== 账号下共有 {len(devices)} 个设备 ===")
        for device in devices:
            print(
                f"  • {device['name']}\n"
                f"      deviceId={device['device_id']} 类别={device['category'] or '?'} "
                f"型号={device['model'] or '?'} 房间={device['room'] or '-'} "
                f"{'在线' if device['online'] else '离线'}"
            )

        device_id = args.device_id or next(
            (d["device_id"] for d in devices if d.get("online")), ""
        )
        if not device_id:
            print("没有可用设备，跳过状态读取")
            return 0

        status = await client.async_get_device_status(device_id)
        output = protocol.extract_output_data(status)
        mapping = status.get("appSpaceDeviceMappingEntity") or {}
        if not output and not status.get("productModel"):
            print("\n❌ 云端没有返回这个设备的数据，检查 deviceId 是否正确")
            return 1

        print(f"\n=== 设备 {device_id} 的基本信息 ===")
        print(
            json.dumps(
                {
                    "名称": mapping.get("deviceName"),
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

        print("\n=== 上报字段（outputData）===")
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            printable = {k: v for k, v in output.items() if not isinstance(v, (dict, list))}
            for key in sorted(printable):
                print(f"  {key} = {printable[key]!r}")

        known = [k for k in protocol.KNOWN_FIELDS if k in output]
        missing = [k for k in protocol.KNOWN_FIELDS if k not in output]
        print(f"\n电热水器已知字段命中 {len(known)}/{len(protocol.KNOWN_FIELDS)}：{known}")
        print(f"未上报：{missing}")
        print(f"可创建的开关实体：{[s[0] for s in protocol.observed_switches(output)]}")
        print(f"推导运行状态：{protocol.derive_work_state(output)}")
        faults = protocol.extract_faults(status)
        print(f"故障信息：{faults or '无'}")

        if args.renew:
            print("\n=== 测试 token 续期 (/api/getLastToken) ===")
            before = client.token
            before_exp = client.token_expires_at
            ok = await client.async_renew_token()
            if not ok:
                print(" 续期失败：云端无法根据旧 token 下发新 token")
                print("   → 意味着 token 过期后需要重新抓包（或改走手机验证码登录）")
                return 2
            after_exp = client.token_expires_at
            print(f"✅ 续期成功，token 变化：{before != client.token}")
            print(f"   旧过期时间：{before_exp.isoformat() if before_exp else '未知'}")
            print(f"   新过期时间：{after_exp.isoformat() if after_exp else '未知'}")
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
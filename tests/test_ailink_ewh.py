"""Offline tests for the request signing and status decoding.

These do not touch the cloud and do not need Home Assistant installed:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import pathlib
import sys
import types
import unittest

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "ailink_ewh"


def _load(module: str):
    """Load a component module without executing the package __init__ (which needs HA)."""
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
protocol = _load("protocol")

AilinkClient = api.AilinkClient
_compact = api._compact
jwt_expires_at = api.jwt_expires_at
ENCODE_SALT = const.ENCODE_SALT
SIGN_SECRET = const.SIGN_SECRET
derive_work_state = protocol.derive_work_state
extract_faults = protocol.extract_faults
extract_output_data = protocol.extract_output_data
power_command = protocol.power_command
switch_command = protocol.switch_command
temperature_command = protocol.temperature_command


# NOTE: the Chinese strings below are *verbatim* payloads from the vendor cloud
# (fault text, business error messages). They are test data, not prose - do not
# translate them, the integration has to pass them through unchanged.
def make_status(output: dict) -> dict:
    """Build a device record in the shape the cloud returns."""
    status_info = json.dumps(
        {
            "events": [
                {"identifier": "post", "outputData": output},
                {
                    "identifier": "faultReportEvent",
                    "outputData": [{"errorCode": "E1", "errorContent": "传感器故障"}],
                },
            ],
            "profile": {"deviceType": "EWH-80HGAWi"},
        },
        ensure_ascii=False,
    )
    return {
        "deviceId": "80A036B9128D",
        "productModel": "EWH-80HGAWi",
        "productMajorClassCode": "20",
        "devState": 1,
        "appDeviceStatusInfoEntity": {"statusInfo": status_info},
    }


class SigningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AilinkClient(
            session=None,  # type: ignore[arg-type]
            access_token="Bearer eyJ.test.token",
            user_id="1000000000000001",
            family_id="1000000000000002",
        )

    def test_token_prefix_is_stripped(self) -> None:
        self.assertEqual(self.client.token, "eyJ.test.token")

    def test_signature_matches_the_official_formula(self) -> None:
        payload = {"userId": "u", "familyId": "f", "deviceId": "d"}
        body = _compact(payload)
        timestamp, nonce = "1700000000000", "NONCE"
        headers = self.client._headers(body, timestamp, nonce)  # noqa: SLF001

        md5data = hashlib.md5(body).hexdigest()
        expected = hashlib.md5(
            f"{md5data}{timestamp}{nonce}{SIGN_SECRET}".encode()
        ).hexdigest()
        self.assertEqual(headers["md5data"], md5data)
        self.assertEqual(headers["sign"], expected)

        # The signed bytes must be exactly the bytes on the wire.
        self.assertEqual(
            body,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        )

    def test_encode_digest_sorts_values(self) -> None:
        digest = self.client._encode(  # noqa: SLF001
            {"userId": "u", "familyId": "f", "deviceId": "d"}
        )
        expected = hashlib.md5(f"dfu{ENCODE_SALT}".encode()).hexdigest()
        self.assertEqual(digest, expected)

    def test_jwt_expiry_is_parsed(self) -> None:
        payload = base64.urlsafe_b64encode(
            json.dumps({"exp": 1700000000}).encode()
        ).decode().rstrip("=")
        token = f"header.{payload}.signature"
        self.assertEqual(jwt_expires_at(token).year, 2023)
        self.assertIsNone(jwt_expires_at("not-a-jwt"))


class ProtocolTest(unittest.TestCase):
    def test_extract_output_data_and_faults(self) -> None:
        device = make_status({"powerStatus": "1", "heatingTemp": "55", "realTemp": "31"})
        output = extract_output_data(device)
        self.assertEqual(output["heatingTemp"], "55")
        self.assertEqual(extract_faults(device), ["E1 传感器故障"])

    def test_work_state_mirrors_the_official_client(self) -> None:
        self.assertEqual(derive_work_state({"powerStatus": "0"}), "off")
        self.assertEqual(
            derive_work_state({"powerStatus": "1", "heatStatus": "1"}), "heating"
        )
        self.assertEqual(
            derive_work_state({"powerStatus": "1", "heatStatus": "0"}), "standby"
        )
        self.assertEqual(
            derive_work_state(
                {"powerStatus": "1", "heatStatus": "0", "preheatStatus1": "1"}
            ),
            "scheduled",
        )
        self.assertIsNone(derive_work_state({}))

    def test_commands(self) -> None:
        self.assertEqual(temperature_command(55.4), {"Temperature": "55"})
        self.assertEqual(power_command(True), {"powerStatus": "1"})
        self.assertEqual(power_command(False), {"powerStatus": "0"})
        self.assertEqual(switch_command("instantHeating", True, {}), {"instantHeating": "1"})
        self.assertEqual(
            switch_command("Mesotherm", True, {"mesothermTemp": "40"}),
            {"Mesotherm": {"OnOff": "1", "Temperature": "40"}},
        )
        self.assertEqual(
            switch_command(
                "PeakValley",
                False,
                {"pvStartTime": "22:00", "pvEndTime": "08:00"},
            ),
            {"PeakValley": {"OnOff": "0", "OpenTime": "22:00", "CloseTime": "08:00"}},
        )


if __name__ == "__main__":
    unittest.main()

class _FakeResponse:
    def __init__(self, status: int, payload: dict, headers: dict | None = None) -> None:
        self.status = status
        self._payload = json.dumps(payload)
        self.headers = headers or {}

    async def text(self) -> str:
        return self._payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _FakeSession:
    """Returns canned responses and records what was sent."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict, dict]] = []

    def post(self, url: str, data: bytes | None = None, headers: dict | None = None):
        self.calls.append((url, json.loads(data or b"{}"), headers or {}))
        return self._responses.pop(0)


class RenewalTest(unittest.IsolatedAsyncioTestCase):
    """A single captured token must survive an expiry without user action."""

    async def test_expired_token_is_renewed_and_the_request_retried(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse(200, {"status": "401", "msg": "token expired"}),
                _FakeResponse(200, {"status": 200, "info": {"token": "NEWTOKEN"}}),
                _FakeResponse(200, {"status": "200", "info": {"deviceId": "d1"}}),
            ]
        )
        client = AilinkClient(  # type: ignore[arg-type]
            session,
            access_token="OLDTOKEN",
            user_id="u1",
            family_id="f1",
        )

        status = await client.async_get_device_status("d1")

        self.assertEqual(status, {"deviceId": "d1"})
        self.assertEqual(client.token, "NEWTOKEN")
        self.assertEqual(len(session.calls), 3)
        self.assertTrue(session.calls[1][0].endswith("/api/getLastToken"))
        self.assertEqual(session.calls[1][1], {"token": "Bearer OLDTOKEN"})
        self.assertEqual(session.calls[2][2]["Authorization"], "Bearer NEWTOKEN")

    async def test_response_header_token_is_stored(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    {"status": "200", "info": {"deviceId": "d1"}},
                    headers={"Authorization": "Bearer HEADERTOKEN"},
                )
            ]
        )
        client = AilinkClient(  # type: ignore[arg-type]
            session, access_token="OLD", user_id="u", family_id="f"
        )
        await client.async_get_device_status("d1")
        self.assertEqual(client.token, "HEADERTOKEN")

    async def test_signature_rejection_is_reported_clearly(self) -> None:
        session = _FakeSession([_FakeResponse(200, {"status": 999999, "msg": "校验失败"})])
        client = AilinkClient(  # type: ignore[arg-type]
            session, access_token="T", user_id="u", family_id="f"
        )
        with self.assertRaises(api.AilinkSignatureError):
            await client.async_get_device_status("d1")

    async def test_auth_error_when_renewal_fails(self) -> None:
        session = _FakeSession(
            [
                _FakeResponse(401, {}),
                _FakeResponse(200, {"status": 500, "msg": "根据旧token信息无法获取新token信息"}),
            ]
        )
        client = AilinkClient(  # type: ignore[arg-type]
            session, access_token="DEAD", user_id="u", family_id="f"
        )
        with self.assertRaises(api.AilinkAuthError):
            await client.async_get_device_status("d1")

    async def test_mint_endpoint_adopts_the_response_header_token(self) -> None:
        """The cloud mints a token in the response header once ours expired."""
        session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    {"status": "200", "msg": "操作成功", "info": {"url": "x", "isShow": "0"}},
                    headers={"Authorization": "Bearer MINTEDTOKEN"},
                )
            ]
        )
        client = AilinkClient(  # type: ignore[arg-type]
            session, access_token="EXPIRED", user_id="u", family_id="f"
        )
        self.assertTrue(await client.async_mint_token())
        self.assertEqual(client.token, "MINTEDTOKEN")
        self.assertIn("getAntifreeze", session.calls[0][0])

    async def test_mint_endpoint_reports_no_new_token(self) -> None:
        """Without a rotation header the call is a harmless no-op."""
        session = _FakeSession([_FakeResponse(200, {"status": "200", "info": ""})])
        client = AilinkClient(  # type: ignore[arg-type]
            session, access_token="SAME", user_id="u", family_id="f"
        )
        self.assertFalse(await client.async_mint_token())
        self.assertEqual(client.token, "SAME")

    async def test_renewal_is_never_permanently_given_up(self) -> None:
        """Repeated failures must not stop a later attempt from succeeding."""
        session = _FakeSession(
            [_FakeResponse(200, {"status": 500, "msg": "no new token"})] * 4
            + [_FakeResponse(200, {"status": 200, "info": {"token": "FRESH"}})]
        )
        client = AilinkClient(  # type: ignore[arg-type]
            session, access_token="OLD", user_id="u", family_id="f"
        )
        for _ in range(4):
            self.assertFalse(await client.async_renew_token())
        self.assertTrue(await client.async_renew_token())
        self.assertEqual(client.token, "FRESH")
        self.assertEqual(len(session.calls), 5)

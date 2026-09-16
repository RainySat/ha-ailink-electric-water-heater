"""Async client for the Ai-LiNK (A.O. Smith) cloud.

The protocol was reverse engineered from the official "AI家智控 / AI-LiNK 智慧家"
H5 client. Two things are worth calling out:

* every ``/AiLinkService`` request is signed (``md5data``/``sign``) over the exact
  bytes that are transmitted, and carries an ``encode`` digest inside the body;
* the bearer token is short lived (~30 min) but the cloud hands out a fresh one
  in the ``Authorization`` response header, and ``/api/getLastToken`` can mint a
  replacement from a known token.  Both mechanisms are implemented here so a
  single manual token capture keeps working.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    ENCODE_SALT,
    PATH_MINT,
    PATH_DEVICE_INFO,
    PATH_HOMEPAGE,
    PATH_INVOKE,
    PATH_LAST_TOKEN,
    SIGN_SECRET,
    SOURCE,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

_BIZ_SUCCESS = {"0", "200"}
_BIZ_AUTH = {"401", "403", "1001"}
_BIZ_SIGNATURE = {"999999"}


class AilinkError(Exception):
    """Base error."""


class AilinkAuthError(AilinkError):
    """The cloud rejected the credentials."""


class AilinkSignatureError(AilinkError):
    """The cloud rejected our request signature."""


class AilinkApiError(AilinkError):
    """Any other cloud failure."""


def jwt_expires_at(token: str) -> datetime | None:
    """Return the ``exp`` claim of a JWT (UTC aware), if it has one."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return datetime.fromtimestamp(float(exp), tz=timezone.utc) if exp else None
    except Exception:  # noqa: BLE001 - never let diagnostics break the client
        return None


def token_seconds_left(token: str) -> float | None:
    """Return how long the token is still valid, in seconds."""
    expires = jwt_expires_at(token)
    if expires is None:
        return None
    return (expires - datetime.now(tz=timezone.utc)).total_seconds()


def _compact(payload: Any) -> bytes:
    """Serialize exactly like the official client (compact, unescaped UTF-8)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


class AilinkClient:
    """Minimal client for one Ai-LiNK account (and optionally one device)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        access_token: str,
        user_id: str,
        family_id: str,
        cookie: str | None = None,
        on_token_update: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        self._session = session
        self._token = access_token.removeprefix("Bearer ").strip()
        self._user_id = user_id
        self._family_id = family_id
        self._cookie = cookie or ""
        self._on_token_update = on_token_update
        self._renew_failures = 0

    # -- properties ---------------------------------------------------------
    @property
    def token(self) -> str:
        """Return the current raw access token."""
        return self._token

    @property
    def token_expires_at(self) -> datetime | None:
        """Return the expiry of the current token."""
        return jwt_expires_at(self._token)

    @property
    def has_cookie(self) -> bool:
        """Return whether a cookie was captured alongside the token."""
        return bool(self._cookie)

    @property
    def user_id(self) -> str:
        """Return the account user id."""
        return self._user_id

    @property
    def family_id(self) -> str:
        """Return the family id."""
        return self._family_id

    # -- token handling -----------------------------------------------------
    async def _set_token(self, token: str, *, reason: str) -> None:
        token = token.removeprefix("Bearer ").strip()
        if not token or token == self._token:
            return
        self._token = token
        self._renew_failures = 0
        expires = self.token_expires_at
        _LOGGER.info(
            "Ai-LiNK token updated (%s), expires at %s",
            reason,
            expires.isoformat() if expires else "unknown",
        )
        if self._on_token_update is not None:
            result = self._on_token_update(token)
            if result is not None:
                await result

    async def async_renew_token(self) -> bool:
        """Ask the cloud for the account's newest token.

        Returns True when the cloud answered with a token.  Callers throttle how
        often this is called; it is deliberately never given up permanently.
        """
        body = _compact({"token": f"Bearer {self._token}"})
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "version": VERSION,
            "source": SOURCE,
            "UserId": self._user_id,
        }
        try:
            async with self._session.post(
                f"{API_BASE}{PATH_LAST_TOKEN}", data=body, headers=headers
            ) as response:
                text = await response.text()
                header_token = _header_token(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("getLastToken failed: %s", err)
            self._renew_failures += 1
            return False
        new_token = None
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        if isinstance(data, dict):
            info = data.get("info")
            if isinstance(info, dict):
                new_token = info.get("token")
            if not new_token and str(data.get("status")) not in _BIZ_SUCCESS:
                _LOGGER.debug("getLastToken rejected: %s", text[:200])
        new_token = new_token or header_token
        if not new_token:
            self._renew_failures += 1
            return False
        await self._set_token(str(new_token), reason="getLastToken")
        return True

    async def async_mint_token(self) -> bool:
        """Ask the cloud to hand out a fresh token in the response header.

        The cloud rotates tokens on a few endpoints when the presented token is
        the account's current (and expired) one; ``getAntifreeze`` is the one
        that still answers 200 while doing so.  ``_post`` adopts the response
        header, so a successful call simply updates :attr:`token`.
        """
        payload = {
            "familyId": self._family_id,
            "userId": self._user_id,
            "encode": self._encode(
                {"familyId": self._family_id, "userId": self._user_id}
            ),
        }
        before = self._token
        try:
            await self._post(PATH_MINT, payload, retry=False)
        except AilinkError as err:
            _LOGGER.debug("token mint request failed: %s", err)
            return False
        return self._token != before

    # -- requests -----------------------------------------------------------
    def _headers(self, body: bytes, timestamp: str, nonce: str) -> dict[str, str]:
        md5data = hashlib.md5(body).hexdigest()
        sign = hashlib.md5(
            f"{md5data}{timestamp}{nonce}{SIGN_SECRET}".encode()
        ).hexdigest()
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-Hans-CN;q=1",
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"Bearer {self._token}",
            "UserId": self._user_id,
            "userId": self._user_id,
            "familyId": self._family_id,
            "familyUk": "",
            "version": VERSION,
            "source": SOURCE,
            "timestamp": timestamp,
            "nonce": nonce,
            "md5data": md5data,
            "sign": sign,
            "traceId": f"{timestamp}-{uuid.uuid4().int % 100000:05d}-{self._user_id}-00",
        }

    def _encode(self, values: dict[str, Any]) -> str:
        joined = "".join(str(values[key]) for key in sorted(values)) + ENCODE_SALT
        return hashlib.md5(joined.encode()).hexdigest()

    async def _post(
        self, path: str, payload: dict[str, Any], *, retry: bool = True
    ) -> dict[str, Any]:
        body = _compact(payload)
        timestamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4()).upper()
        headers = self._headers(body, timestamp, nonce)
        if self._cookie:
            headers["Cookie"] = self._cookie

        try:
            async with self._session.post(
                f"{API_BASE}{path}", data=body, headers=headers
            ) as response:
                status = response.status
                text = await response.text()
                header_token = _header_token(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AilinkApiError(f"network error: {err}") from None

        if header_token:
            await self._set_token(header_token, reason="response header")

        if status in (401, 403):
            return await self._retry_after_auth(path, payload, retry, f"HTTP {status}")

        try:
            data = json.loads(text)
        except ValueError:
            raise AilinkApiError(f"invalid response: {text[:200]}") from None
        if not isinstance(data, dict):
            raise AilinkApiError("invalid response payload")

        biz = str(data.get("status"))
        if biz in _BIZ_AUTH:
            return await self._retry_after_auth(path, payload, retry, "token rejected")
        if biz in _BIZ_SIGNATURE:
            raise AilinkSignatureError(
                f"cloud rejected the request signature: {data.get('msg')}"
            )
        if biz not in _BIZ_SUCCESS:
            raise AilinkApiError(f"cloud error {biz}: {data.get('msg')}")
        return data

    async def _retry_after_auth(
        self, path: str, payload: dict[str, Any], retry: bool, why: str
    ) -> dict[str, Any]:
        if not retry:
            raise AilinkAuthError(f"authentication failed ({why})")
        if await self.async_renew_token():
            return await self._post(path, payload, retry=False)
        raise AilinkAuthError(f"authentication failed and token could not be renewed ({why})")

    # -- API ----------------------------------------------------------------
    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return every device of the account."""
        payload = {
            "encode": self._encode(
                {"familyId": self._family_id, "userId": self._user_id}
            ),
            "homePageVersion": "3",
            "userId": self._user_id,
            "familyId": self._family_id,
        }
        data = await self._post(PATH_HOMEPAGE, payload)
        info = data.get("info") or {}
        entries: list[dict[str, Any]] = []

        listed = info.get("devInfoItemInfoList")
        if isinstance(listed, list):
            entries.extend(item for item in listed if isinstance(item, dict))
        for room in info.get("roomInfoItemInfoList") or []:
            if not isinstance(room, dict):
                continue
            room_name = room.get("roomName") or room.get("name")
            for device in room.get("deviceList") or []:
                if isinstance(device, dict):
                    entries.append({**device, "_room": room_name})

        devices: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries:
            device_id = entry.get("deviceId") or entry.get("id")
            if not device_id or device_id in seen:
                continue
            seen.add(str(device_id))
            mapping = entry.get("appSpaceDeviceMappingEntity")
            if not isinstance(mapping, dict):
                mapping = {}
            devices.append(
                {
                    "device_id": str(device_id),
                    "name": (
                        entry.get("deviceName")
                        or mapping.get("deviceName")
                        or entry.get("productName")
                        or "A.O. Smith 设备"
                    ),
                    "product_name": entry.get("productName") or "",
                    "model": entry.get("productModel")
                    or entry.get("productModelName")
                    or "",
                    "category": str(entry.get("deviceCategory") or ""),
                    "room": entry.get("_room") or mapping.get("roomName") or "",
                    "online": str(entry.get("devState", "1")) != "0",
                    "raw": entry,
                }
            )
        return devices

    async def async_get_device_status(self, device_id: str) -> dict[str, Any]:
        """Return the full status record of one device."""
        payload = {
            "userId": self._user_id,
            "familyId": self._family_id,
            "deviceId": device_id,
            "encode": self._encode(
                {
                    "deviceId": device_id,
                    "familyId": self._family_id,
                    "userId": self._user_id,
                }
            ),
        }
        data = await self._post(PATH_DEVICE_INFO, payload)
        info = data.get("info")
        if not isinstance(info, dict):
            raise AilinkApiError("device status is missing from the response")
        return info

    async def async_send_command(
        self,
        device_id: str,
        identifier: str,
        input_data: dict[str, Any],
        *,
        product_type: str,
        device_type: str,
    ) -> dict[str, Any]:
        """Send one control command."""
        payload = {
            "userId": self._user_id,
            "familyId": self._family_id,
            "appSource": 2,
            "commandSource": 1,
            "invokeTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payLoad": json.dumps(
                {
                    "profile": {
                        "deviceId": device_id,
                        "productType": str(product_type),
                        "deviceType": str(device_type),
                    },
                    "service": {
                        "identifier": identifier,
                        "inputData": input_data,
                    },
                },
                ensure_ascii=False,
            ),
        }
        return await self._post(PATH_INVOKE, payload)


def _header_token(response: aiohttp.ClientResponse) -> str | None:
    """Return the token the cloud may hand out with every response."""
    for key in ("Authorization", "authorization"):
        value = response.headers.get(key)
        if value and value.strip():
            return value.strip()
    return None

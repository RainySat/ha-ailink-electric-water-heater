"""Data update coordinator for the Ai-LiNK electric water heater."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AilinkApiError,
    AilinkAuthError,
    AilinkClient,
    AilinkSignatureError,
    jwt_expires_at,
)
from .const import (
    CONF_DEVICE_TYPE,
    CONF_PRODUCT_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .protocol import extract_output_data

_LOGGER = logging.getLogger(__name__)

# Try to pick up the token the phone app is using once our own copy is about to
# expire: the cloud hands out the account's newest token for an old one, so
# asking ahead of time keeps us in sync and we never actually hit the expiry.
RENEW_AHEAD = timedelta(minutes=12)
# Throttle for the token sync attempts.  Timed only - never a permanent give-up,
# so a token that really died can be recovered whenever the app mints a new one.
MIN_RENEW_GAP = timedelta(minutes=3)      # before the JWT claim expires
EXPIRED_RENEW_GAP = timedelta(minutes=30)  # after it expired (cloud still accepts)
FAILURE_RENEW_GAP = timedelta(minutes=5)   # the cloud stopped returning data


def _matches(current: Any, expected: Any, tolerance: float) -> bool:
    """Compare a reported value against an expected one."""
    if current is None:
        return False
    if isinstance(expected, bool):
        truthy = str(current).strip().lower() not in ("", "0", "false", "off")
        return truthy is expected
    try:
        return abs(float(current) - float(expected)) <= tolerance
    except (TypeError, ValueError):
        return str(current) == str(expected)


class AilinkCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one electric water heater and serialises commands to it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AilinkClient,
        device_id: str,
    ) -> None:
        self.client = client
        self.entry = entry
        self.device_id = device_id
        self._command_lock = asyncio.Lock()
        self._last_output: dict[str, Any] = {}
        self._last_renew_attempt: datetime | None = None
        self._warned_token: str | None = None
        # token value we have already made a post-expiry attempt for; the first
        # attempt after expiry should be prompt, later ones back off.
        self._post_expiry_attempted: str | None = None
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{device_id}",
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )

    # -- data access --------------------------------------------------------
    @property
    def device_data(self) -> dict[str, Any]:
        """Return the raw device record."""
        return self.data or {}

    @property
    def output(self) -> dict[str, Any]:
        """Return the reported properties (kept across transient failures)."""
        output = extract_output_data(self.device_data)
        return output or self._last_output

    @property
    def product_type(self) -> str:
        """Return the product major class code reported by the cloud."""
        value = self.device_data.get("productMajorClassCode") or self.entry.data.get(
            CONF_PRODUCT_TYPE
        )
        return str(value or "")

    @property
    def device_type(self) -> str:
        """Return the model string reported by the cloud."""
        value = self.device_data.get("productModel") or self.entry.data.get(
            CONF_DEVICE_TYPE
        )
        return str(value or "")

    @property
    def model_name(self) -> str | None:
        """Return a human readable model name."""
        return self.device_data.get("productModel") or self.entry.data.get(
            CONF_DEVICE_TYPE
        )

    @property
    def device_name(self) -> str:
        """Return the device name."""
        mapping = self.device_data.get("appSpaceDeviceMappingEntity")
        if isinstance(mapping, dict) and mapping.get("deviceName"):
            return str(mapping["deviceName"])
        return str(
            self.entry.data.get("device_name")
            or self.device_data.get("deviceName")
            or self.model_name
            or "A.O. Smith 电热水器"
        )

    # -- polling ------------------------------------------------------------
    async def _sync_token(self, *, force_gap: timedelta | None = None) -> bool:
        """Try to adopt the account's newest token.

        Returns True when a different (newer) token was picked up.  The attempt
        is throttled by time only - never permanently given up - so a token that
        really expired can still be recovered as soon as the phone app mints a
        newer one.
        """
        expires = jwt_expires_at(self.client.token)
        if expires is None:
            return False
        now = datetime.now(tz=timezone.utc)
        if expires - now > RENEW_AHEAD:
            return False

        if force_gap is not None:
            gap = force_gap
        elif expires <= now:
            # The JWT claim has passed.  Try promptly once (to mint a fresh token
            # right away), then back off - the cloud accepts the stale token for
            # a long time, so there is no rush afterwards.
            if self._post_expiry_attempted != self.client.token:
                gap = MIN_RENEW_GAP
            else:
                gap = EXPIRED_RENEW_GAP
        else:
            gap = MIN_RENEW_GAP
        if self._last_renew_attempt is not None and now - self._last_renew_attempt < gap:
            return False

        self._last_renew_attempt = now
        before = self.client.token
        if expires <= now:
            self._post_expiry_attempted = before
        _LOGGER.debug(
            "Asking the cloud for the account's newest token (JWT exp %s, %+.0f min)",
            expires.isoformat(),
            (expires - now).total_seconds() / 60,
        )
        await self.client.async_renew_token()
        if self.client.token != before:
            _LOGGER.info("已同步到账号上更新的 access_token（App 或换卡接口刷新过）")
            return True

        # Nothing newer on the server.  If our own token has passed its `exp`,
        # the cloud will mint a brand new one in the response header of a few
        # endpoints - that is how the phone app gets its tokens, and it means we
        # never need the user to open the app.
        if expires <= now and await self.client.async_mint_token():
            _LOGGER.info("已通过云端换卡接口领到新的 access_token（无需打开 App）")
            return True

        if expires <= now and self._warned_token != before:
            # Informational only: the cloud keeps accepting a token well past its
            # JWT `exp`, so this is not an error yet.
            self._warned_token = before
            _LOGGER.info(
                "access_token 的 JWT 声明已于 %s 到期，且换卡接口未返回新 token；"
                "当前 token 仍可正常使用，若实体变成不可用，在手机上打开一次"
                "「AI家智控」App 即可自动恢复。",
                expires.astimezone().strftime("%m-%d %H:%M"),
            )
        return False

    async def _async_update_data(self) -> dict[str, Any]:
        await self._sync_token()
        try:
            status = await self.client.async_get_device_status(self.device_id)
        except AilinkAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AilinkSignatureError as err:
            raise UpdateFailed(
                f"云端拒绝了请求签名，可能需要更新集成：{err}"
            ) from err
        except AilinkApiError as err:
            raise UpdateFailed(str(err)) from err

        if not self._looks_authorised(status):
            # The cloud answers 200 with an empty record when the token is not
            # accepted - that is what an expiry looks like.  Ask the server for
            # the account's latest token and retry before reporting a failure.
            if await self._sync_token(force_gap=FAILURE_RENEW_GAP):
                try:
                    status = await self.client.async_get_device_status(self.device_id)
                except AilinkApiError:
                    status = {}
            if not self._looks_authorised(status):
                # Not a hard failure: keep the entry loaded so the regular
                # polling can pick up a fresh token as soon as the phone app
                # refreshes one.  We never stop retrying.
                raise UpdateFailed(
                    "access_token 已失效（云端未返回设备数据）。"
                    "在手机上打开一次「AI家智控」App 即可恢复，集成会自动同步；"
                    "也可以重新抓包后走「重新认证」立即恢复。"
                )

        output = extract_output_data(status)
        if output:
            self._last_output = output
        return status

    @staticmethod
    def _looks_authorised(status: dict[str, Any]) -> bool:
        """Guess whether the cloud accepted our credentials for this device."""
        entity = status.get("appDeviceStatusInfoEntity")
        entity = entity if isinstance(entity, dict) else {}
        return bool(
            status.get("productModel")
            or status.get("productMajorClassCode")
            or entity.get("statusInfo")
            or status.get("deviceId")
        )

    # -- commands -----------------------------------------------------------
    async def async_send_command(
        self,
        identifier: str,
        input_data: dict[str, Any],
        *,
        expect: dict[str, Any] | None = None,
        tolerance: float = 0.51,
        confirm: bool = True,
    ) -> None:
        """Send a command and (best effort) confirm the device accepted it."""
        async with self._command_lock:
            product_type = self.product_type
            device_type = self.device_type
            if not product_type or not device_type:
                raise HomeAssistantError(
                    "设备信息不完整（productMajorClassCode/productModel 为空），无法下发指令"
                )
            try:
                await self.client.async_send_command(
                    self.device_id,
                    identifier,
                    input_data,
                    product_type=product_type,
                    device_type=device_type,
                )
            except AilinkAuthError as err:
                raise HomeAssistantError(
                    f"access_token 已失效，请在手机上打开一次 App 后重试：{err}"
                ) from err
            except AilinkApiError as err:
                raise HomeAssistantError(f"下发指令失败：{err}") from err

        if not confirm or not expect:
            await self.async_request_refresh()
            return

        for delay in (1, 2, 3, 4):
            await asyncio.sleep(delay)
            await self.async_refresh()
            output = self.output
            if all(_matches(output.get(key), value, tolerance) for key, value in expect.items()):
                return
        _LOGGER.warning(
            "Command %s was accepted by the cloud but the device did not report %s within 10s "
            "(current: %s)",
            identifier,
            expect,
            {key: self.output.get(key) for key in expect},
        )
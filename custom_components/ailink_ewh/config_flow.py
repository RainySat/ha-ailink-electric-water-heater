"""Config flow for the Ai-LiNK electric water heater."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .api import AilinkApiError, AilinkAuthError, AilinkClient, AilinkSignatureError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COOKIE,
    CONF_CREATE_ALL_SWITCHES,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_EXTRA_ATTRIBUTES,
    CONF_FAMILY_ID,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_PRODUCT_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_USER_ID,
    DEFAULT_CREATE_ALL_SWITCHES,
    DEFAULT_EXTRA_ATTRIBUTES,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

MANUAL_DEVICE = "__manual__"

ACCOUNT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCESS_TOKEN): str,
        vol.Required(CONF_USER_ID): str,
        vol.Required(CONF_FAMILY_ID): str,
        vol.Optional(CONF_COOKIE, default=""): str,
    }
)


class AilinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._account: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []

    # -- helpers ------------------------------------------------------------
    def _client(self, account: dict[str, Any] | None = None) -> AilinkClient:
        account = account or self._account
        return AilinkClient(
            async_get_clientsession(self.hass),
            access_token=account[CONF_ACCESS_TOKEN],
            user_id=account[CONF_USER_ID],
            family_id=account[CONF_FAMILY_ID],
            cookie=account.get(CONF_COOKIE),
        )

    @staticmethod
    def _normalise_account(user_input: dict[str, Any]) -> dict[str, Any]:
        return {
            CONF_ACCESS_TOKEN: str(user_input[CONF_ACCESS_TOKEN])
            .removeprefix("Bearer ")
            .strip(),
            CONF_USER_ID: str(user_input[CONF_USER_ID]).strip(),
            CONF_FAMILY_ID: str(user_input[CONF_FAMILY_ID]).strip(),
            CONF_COOKIE: str(user_input.get(CONF_COOKIE) or "").strip(),
        }

    @staticmethod
    def _error_key(err: Exception) -> str:
        if isinstance(err, AilinkAuthError):
            return "invalid_auth"
        if isinstance(err, AilinkSignatureError):
            return "signature_error"
        return "cannot_connect"

    @staticmethod
    def _status_ok(status: dict[str, Any]) -> bool:
        """Return whether the cloud really answered for this device."""
        entity = status.get("appDeviceStatusInfoEntity")
        entity = entity if isinstance(entity, dict) else {}
        return bool(
            status.get("productModel")
            or status.get("productMajorClassCode")
            or entity.get("statusInfo")
        )

    @staticmethod
    def _device_label(device: dict[str, Any]) -> str:
        parts = [str(device.get("name") or device["device_id"])]
        if device.get("model"):
            parts.append(f"model {device['model']}")
        if device.get("category"):
            parts.append(f"class {device['category']}")
        if device.get("room"):
            parts.append(str(device["room"]))
        parts.append("online" if device.get("online") else "offline")
        return " · ".join(parts)

    # -- steps --------------------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account = self._normalise_account(user_input)
            client = self._client(account)
            try:
                devices = await client.async_get_devices()
            except (AilinkAuthError, AilinkApiError) as err:
                _LOGGER.debug("Device discovery failed: %s", err)
                errors["base"] = self._error_key(err)
            else:
                if not devices:
                    # An unaccepted token is answered with an empty account
                    # instead of an error, so treat this as an auth problem.
                    errors["base"] = "invalid_auth"
                else:
                    account[CONF_ACCESS_TOKEN] = client.token
                    self._account = account
                    self._devices = devices
                    return await self.async_step_device()

        return self.async_show_form(
            step_id="user", data_schema=ACCOUNT_SCHEMA, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            if device_id == MANUAL_DEVICE:
                return await self.async_step_manual()
            result = await self._async_create(device_id)
            if isinstance(result, dict):
                return result
            errors["base"] = result

        if not self._devices:
            return await self.async_step_manual()

        options = [
            SelectOptionDict(value=device["device_id"], label=self._device_label(device))
            for device in self._devices
        ]
        options.append(
            SelectOptionDict(value=MANUAL_DEVICE, label="Enter the device id manually")
        )
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_create(str(user_input[CONF_DEVICE_ID]).strip())
            if isinstance(result, dict):
                return result
            errors["base"] = result

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE_ID): str}),
            errors=errors,
        )

    async def _async_create(self, device_id: str) -> FlowResult | str:
        """Validate the device and create the entry (or return an error key)."""
        if not device_id:
            return "device_not_found"
        client = self._client()
        try:
            status = await client.async_get_device_status(device_id)
        except (AilinkAuthError, AilinkApiError) as err:
            _LOGGER.debug("Device validation failed: %s", err)
            return self._error_key(err)

        if not self._status_ok(status):
            return "invalid_auth"

        mapping = status.get("appSpaceDeviceMappingEntity")
        mapping = mapping if isinstance(mapping, dict) else {}
        known = next(
            (d for d in self._devices if d["device_id"] == device_id), None
        )
        name = (
            mapping.get("deviceName")
            or (known or {}).get("name")
            or status.get("productName")
            or status.get("productModel")
            or DEFAULT_NAME
        )
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=str(name),
            data={
                **self._account,
                CONF_DEVICE_ID: device_id,
                CONF_DEVICE_NAME: str(name),
                CONF_PRODUCT_TYPE: str(status.get("productMajorClassCode") or ""),
                CONF_DEVICE_TYPE: str(status.get("productModel") or ""),
            },
        )

    # -- reauth / reconfigure ----------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = str(user_input[CONF_ACCESS_TOKEN]).removeprefix("Bearer ").strip()
            account = {**dict(entry.data), CONF_ACCESS_TOKEN: token}
            client = self._client(account)
            try:
                status = await client.async_get_device_status(entry.data[CONF_DEVICE_ID])
            except (AilinkAuthError, AilinkApiError) as err:
                errors["base"] = self._error_key(err)
            else:
                if not self._status_ok(status):
                    errors["base"] = "invalid_auth"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_ACCESS_TOKEN: client.token},
                        reason="reauth_successful",
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_ACCESS_TOKEN): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account = self._normalise_account(user_input)
            client = self._client(account)
            try:
                status = await client.async_get_device_status(entry.data[CONF_DEVICE_ID])
            except (AilinkAuthError, AilinkApiError) as err:
                errors["base"] = self._error_key(err)
            else:
                if not self._status_ok(status):
                    errors["base"] = "invalid_auth"
                else:
                    account[CONF_ACCESS_TOKEN] = client.token
                    return self.async_update_reload_and_abort(
                        entry, data_updates=account
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ACCESS_TOKEN, default=entry.data[CONF_ACCESS_TOKEN]
                    ): str,
                    vol.Required(CONF_USER_ID, default=entry.data[CONF_USER_ID]): str,
                    vol.Required(
                        CONF_FAMILY_ID, default=entry.data[CONF_FAMILY_ID]
                    ): str,
                    vol.Optional(CONF_COOKIE, default=entry.data.get(CONF_COOKIE, "")): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AilinkOptionsFlow()


class AilinkOptionsFlow(OptionsFlow):
    """Integration options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=900)),
                vol.Optional(
                    CONF_MIN_TEMP, default=options.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
                ): vol.All(vol.Coerce(float), vol.Range(min=25, max=60)),
                vol.Optional(
                    CONF_MAX_TEMP, default=options.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)
                ): vol.All(vol.Coerce(float), vol.Range(min=40, max=90)),
                vol.Optional(
                    CONF_EXTRA_ATTRIBUTES,
                    default=options.get(
                        CONF_EXTRA_ATTRIBUTES, DEFAULT_EXTRA_ATTRIBUTES
                    ),
                ): bool,
                vol.Optional(
                    CONF_CREATE_ALL_SWITCHES,
                    default=options.get(
                        CONF_CREATE_ALL_SWITCHES, DEFAULT_CREATE_ALL_SWITCHES
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
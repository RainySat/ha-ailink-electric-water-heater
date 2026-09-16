"""The Ai-LiNK (A.O. Smith) electric water heater integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AilinkApiError, AilinkAuthError, AilinkClient
from .const import CONF_ACCESS_TOKEN, DOMAIN, PLATFORMS
from .coordinator import AilinkCoordinator

_LOGGER = logging.getLogger(__name__)

type AilinkConfigEntry = ConfigEntry[AilinkCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AilinkConfigEntry) -> bool:
    """Set up one electric water heater from a config entry."""
    session = async_get_clientsession(hass)

    async def _persist_token(token: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ACCESS_TOKEN: token}
        )

    client = AilinkClient(
        session,
        access_token=entry.data[CONF_ACCESS_TOKEN],
        user_id=entry.data["user_id"],
        family_id=entry.data["family_id"],
        cookie=entry.data.get("cookie"),
        on_token_update=_persist_token,
    )
    coordinator = AilinkCoordinator(hass, entry, client, entry.data["device_id"])

    try:
        await coordinator.async_config_entry_first_refresh()
    except AilinkAuthError as err:  # pragma: no cover - defensive
        raise ConfigEntryNotReady(str(err)) from err
    except AilinkApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AilinkConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: AilinkConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)
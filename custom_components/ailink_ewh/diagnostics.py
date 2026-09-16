"""Diagnostics for the Ai-LiNK electric water heater."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ACCESS_TOKEN
from .coordinator import AilinkCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for this config entry (token is redacted)."""
    coordinator: AilinkCoordinator = entry.runtime_data
    client = coordinator.client
    token = entry.data.get(CONF_ACCESS_TOKEN, client.token)
    output = coordinator.output
    return {
        "account": {
            "user_id": client.user_id,
            "family_id": client.family_id,
            "cookie": client.has_cookie,
        },
        "token": {
            "captured_prefix": f"{token[:10]}…",
            "captured_length": len(token),
            "current_expires_at": (
                client.token_expires_at.astimezone().isoformat()
                if client.token_expires_at
                else None
            ),
            "renew_mechanism": "proactive /api/getLastToken every 3 min near expiry",
        },
        "device": {
            "device_id": coordinator.device_id,
            "device_name": coordinator.device_name,
            "product_type": coordinator.product_type,
            "device_type": coordinator.device_type,
            "online": coordinator.device_data.get("devState") == 1,
        },
        "properties": output,
        "raw_keys": sorted(coordinator.device_data.keys()),
    }
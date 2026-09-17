"""The heating mode selector of the electric water heater.

The device reports the current mode as ``workModel`` and is switched by sending
``HeaterMode`` through the usual service.  On the models this integration targets
the modes are single tank / dual tank / winter large volume.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    HEATER_MODES,
    HEATER_MODE_COMMAND_FIELD,
    HEATER_MODE_STATUS_FIELD,
    HEATER_MODE_TEMPERATURE_LOCKED,
    SERVICE_SET_EWH,
)
from .coordinator import AilinkCoordinator
from .entity import AilinkEntity
from .protocol import heater_mode_command, heater_mode_option, heater_mode_value

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the heating mode selector if the device reports one."""
    coordinator: AilinkCoordinator = entry.runtime_data
    if heater_mode_value(coordinator.output) is None:
        _LOGGER.debug(
            "The device does not report %s, no heating mode entity was created",
            HEATER_MODE_STATUS_FIELD,
        )
        return
    async_add_entities([AilinkHeaterModeSelect(coordinator)])


class AilinkHeaterModeSelect(AilinkEntity, SelectEntity):
    """Single tank / dual tank / winter large volume."""

    _attr_translation_key = "heater_mode"
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator: AilinkCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_heater_mode"

    @property
    def options(self) -> list[str]:
        """Return the modes, keeping one the device reports but we do not name."""
        options = [key for key, _value in HEATER_MODES]
        current = heater_mode_option(self.output)
        if current and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        return heater_mode_option(self.output)

    async def async_select_option(self, option: str) -> None:
        """Switch the heating mode."""
        command = heater_mode_command(option)
        if command is None:
            raise ValueError(f"unsupported heating mode: {option}")
        value = int(command[HEATER_MODE_COMMAND_FIELD])
        if value == HEATER_MODE_TEMPERATURE_LOCKED:
            _LOGGER.info(
                "Heating mode set to %s: the device ignores target temperature changes "
                "in this mode, so the temperature control will have no effect.",
                option,
            )
        await self.coordinator.async_send_command(
            SERVICE_SET_EWH,
            command,
            expect={HEATER_MODE_STATUS_FIELD: value},
        )

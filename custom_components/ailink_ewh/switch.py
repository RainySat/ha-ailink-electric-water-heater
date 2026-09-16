"""Auxiliary switches of the electric water heater."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CREATE_ALL_SWITCHES,
    DEFAULT_CREATE_ALL_SWITCHES,
    SERVICE_SET_EWH,
    SWITCH_TYPES,
)
from .coordinator import AilinkCoordinator
from .entity import AilinkEntity
from .protocol import flag, observed_switches, switch_command

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one switch per feature the device actually reports."""
    coordinator: AilinkCoordinator = entry.runtime_data
    output = coordinator.output
    specs = (
        list(SWITCH_TYPES)
        if entry.options.get(CONF_CREATE_ALL_SWITCHES, DEFAULT_CREATE_ALL_SWITCHES)
        else observed_switches(output)
    )
    if not specs:
        _LOGGER.warning(
            "设备状态里没有任何可识别的开关字段，未创建开关实体。"
            "可在集成选项里打开「创建全部开关实体」后重载。"
        )
    async_add_entities(
        AilinkSwitch(coordinator, spec_key, status_field, command_field, icon)
        for spec_key, status_field, command_field, icon in specs
    )


class AilinkSwitch(AilinkEntity, SwitchEntity):
    """One auxiliary switch (instant heating, sterilise, AES, ...)."""

    def __init__(
        self,
        coordinator: AilinkCoordinator,
        key: str,
        status_field: str,
        command_field: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._status_field = status_field
        self._command_field = command_field
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.device_id}_{key}"

    @property
    def is_on(self) -> bool | None:
        return flag(self.output, self._status_field)

    async def async_turn_on(self, **kwargs) -> None:
        await self._send(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._send(False)

    async def _send(self, on: bool) -> None:
        await self.coordinator.async_send_command(
            SERVICE_SET_EWH,
            switch_command(self._command_field, on, self.output),
            expect={self._status_field: on},
        )
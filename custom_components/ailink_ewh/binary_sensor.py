"""Binary sensors of the electric water heater."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AilinkCoordinator
from .entity import AilinkEntity
from .protocol import extract_faults, flag


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator: AilinkCoordinator = entry.runtime_data
    async_add_entities(
        [AilinkHeatingBinarySensor(coordinator), AilinkFaultBinarySensor(coordinator)]
    )


class AilinkHeatingBinarySensor(AilinkEntity, BinarySensorEntity):
    """Whether the heating element is currently running.

    ``RUNNING`` (running / not running), not ``HEAT``: HA's ``heat`` device class
    means "the thing is hot", and it renders as "Hot" in English and "过热" in
    Chinese - a heating element that is simply working is not overheating.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "heating"

    def __init__(self, coordinator: AilinkCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_heating"

    @property
    def is_on(self) -> bool | None:
        if not flag(self.output, "powerStatus", "powerOn"):
            return False
        return flag(self.output, "heatStatus")


class AilinkFaultBinarySensor(AilinkEntity, BinarySensorEntity):
    """Whether the device reports a fault."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "fault"

    def __init__(self, coordinator: AilinkCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_fault"

    @property
    def is_on(self) -> bool:
        return bool(extract_faults(self.coordinator.device_data))
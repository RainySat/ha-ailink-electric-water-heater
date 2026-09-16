"""Sensors of the electric water heater."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AilinkCoordinator
from .entity import AilinkEntity
from .protocol import derive_work_state, extract_faults, numeric


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator: AilinkCoordinator = entry.runtime_data
    async_add_entities(
        [
            AilinkCurrentTempSensor(coordinator),
            AilinkWorkStateSensor(coordinator),
            AilinkFaultSensor(coordinator),
        ]
    )


class AilinkCurrentTempSensor(AilinkEntity, SensorEntity):
    """Actual water temperature (also useful for HomeKit)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_translation_key = "current_temperature"

    def __init__(self, coordinator: AilinkCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_current_temperature"

    @property
    def native_value(self) -> float | None:
        return numeric(self.output, "realTemp", "currentTemp")


class AilinkWorkStateSensor(AilinkEntity, SensorEntity):
    """Derived state (heating / scheduled / standby / off)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "heating", "scheduled", "standby"]
    _attr_translation_key = "work_state"

    def __init__(self, coordinator: AilinkCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_work_state"

    @property
    def native_value(self) -> str | None:
        return derive_work_state(self.output)


class AilinkFaultSensor(AilinkEntity, SensorEntity):
    """Fault/warning message reported by the device."""

    _attr_translation_key = "fault"
    _attr_icon = "mdi:alert"

    def __init__(self, coordinator: AilinkCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_fault"

    @property
    def native_value(self) -> str | None:
        messages = extract_faults(self.coordinator.device_data)
        return "；".join(messages) if messages else None
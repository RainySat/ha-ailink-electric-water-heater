"""Shared entity helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AilinkCoordinator


class AilinkEntity(CoordinatorEntity[AilinkCoordinator]):
    """Base class for every entity of one water heater."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AilinkCoordinator) -> None:
        super().__init__(coordinator)
        self._device_id = coordinator.device_id

    @property
    def output(self) -> dict[str, Any]:
        """Return the reported properties."""
        return self.coordinator.output

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the device for the registry."""
        data = self.coordinator.device_data
        status_entity = data.get("appDeviceStatusInfoEntity")
        status_entity = status_entity if isinstance(status_entity, dict) else {}
        mapping = data.get("appSpaceDeviceMappingEntity")
        mapping = mapping if isinstance(mapping, dict) else {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self.coordinator.device_name,
            manufacturer=MANUFACTURER,
            model=self.coordinator.model_name or data.get("productName") or "Water heater",
            sw_version=status_entity.get("ccuVersion") or data.get("ccuVersion") or None,
            suggested_area=mapping.get("roomName") or None,
        )

    @property
    def available(self) -> bool:
        """Return whether the cloud still reports the device as reachable."""
        if not super().available:
            return False
        if str(self.coordinator.device_data.get("devState", "1")) == "0":
            return False
        return True
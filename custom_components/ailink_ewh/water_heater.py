"""Water heater entity (power + target temperature)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_OFF,
    STATE_ON,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_EXTRA_ATTRIBUTES,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    DEFAULT_EXTRA_ATTRIBUTES,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DOMAIN,
    SERVICE_SET_EWH,
)
from .coordinator import AilinkCoordinator
from .entity import AilinkEntity
from .protocol import (
    KNOWN_FIELDS,
    derive_work_state,
    flag,
    heater_mode_option,
    numeric,
    power_command,
    temperature_command,
    temperature_is_adjustable,
)

# Fields that are always shown as attributes, even when they are empty.
_ALWAYS_ATTRS = (
    "powerStatus",
    "realTemp",
    "heatingTemp",
    "heatStatus",
    "workModel",
    "instantHeating",
    "disinfection",
    "aes",
    "peekValley",
    "mesotherm",
    "mesothermTemp",
    "increaseCapacity",
    "preheatStatus1",
    "preheatStatus2",
    "preheatStatus3",
    "preheatTime",
    "timerOneStartTime",
    "timerOneEndTime",
    "timerTwoStartTime",
    "timerTwoEndTime",
    "antiscaleFilter",
    "autoSterilizeHeating",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the water heater entity."""
    coordinator: AilinkCoordinator = entry.runtime_data
    async_add_entities([AilinkWaterHeater(coordinator, entry)])


class AilinkWaterHeater(AilinkEntity, WaterHeaterEntity):
    """The electric water heater itself."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # ON_OFF is what HomeKit and automations use; OPERATION_MODE with an
    # off/on list is what the Home Assistant frontend renders.  The frontend has
    # no on/off widget for water heaters - it only offers a temperature control
    # and an operation mode selector - so both features are needed.
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.OPERATION_MODE
    )
    _attr_operation_list = [STATE_OFF, STATE_ON]
    _attr_target_temperature_step = 1.0
    _attr_precision = 1.0
    _attr_name = None

    def __init__(self, coordinator: AilinkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.device_id}_water_heater"

    @property
    def _min(self) -> float:
        return self._entry.options.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)

    @property
    def _max(self) -> float:
        return self._entry.options.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)

    @property
    def current_temperature(self) -> float | None:
        """Actual water temperature."""
        return numeric(self.output, "realTemp", "currentTemp", "waterTemp")

    @property
    def target_temperature(self) -> float | None:
        """Target water temperature."""
        return numeric(self.output, "heatingTemp", "setTemp", "constantTemp")

    @property
    def min_temp(self) -> float:
        return self._min

    @property
    def max_temp(self) -> float:
        return self._max

    @property
    def is_on(self) -> bool | None:
        return flag(self.output, "powerStatus", "powerOn")

    @property
    def current_operation(self) -> str | None:
        """Drive the entity state (HA reads ``current_operation`` for it)."""
        state = self.is_on
        if state is None:
            return None
        return STATE_ON if state else STATE_OFF

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Handle the frontend's operation mode selector."""
        if operation_mode == STATE_ON:
            await self.async_turn_on()
        elif operation_mode == STATE_OFF:
            await self.async_turn_off()
        else:
            raise ValueError(f"Unsupported operation mode: {operation_mode}")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        output = self.output
        attrs: dict[str, Any] = {
            "work_state": derive_work_state(output),
            "token_expires_at": (
                self.coordinator.client.token_expires_at.astimezone().isoformat()
                if self.coordinator.client.token_expires_at
                else None
            ),
        }
        if not self._entry.options.get(CONF_EXTRA_ATTRIBUTES, DEFAULT_EXTRA_ATTRIBUTES):
            return attrs
        attrs.update({key: output[key] for key in _ALWAYS_ATTRS if key in output})
        attrs.update(
            {
                f"raw_{key}": output[key]
                for key in KNOWN_FIELDS
                if key in output and key not in _ALWAYS_ATTRS
            }
        )
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if not temperature_is_adjustable(self.output):
            # The device takes the command but ignores it, and the official client
            # hides the control in this mode too.  Warn instead of raising: moving
            # to another heating mode makes the very same call work.
            _LOGGER.warning(
                "Heating mode %s ignores target temperatures, so the new value will not "
                "take effect until another mode is selected",
                heater_mode_option(self.output),
            )
        value = max(self._min, min(self._max, float(temperature)))
        await self.coordinator.async_send_command(
            SERVICE_SET_EWH,
            temperature_command(value),
            expect={"heatingTemp": value},
            tolerance=1.01,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_send_command(
            SERVICE_SET_EWH,
            power_command(True),
            expect={"powerStatus": True},
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_send_command(
            SERVICE_SET_EWH,
            power_command(False),
            expect={"powerStatus": False},
        )
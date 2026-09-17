"""Status decoding for Ai-LiNK electric water heaters.

Field names come straight from the official ``/ElectricWaterHeater`` H5 page.
"""

from __future__ import annotations

import json
import math
from typing import Any, Final

from .const import (
    HEATER_MODE_COMMAND_FIELD,
    HEATER_MODES,
    HEATER_MODE_STATUS_FIELD,
    HEATER_MODE_TEMPERATURE_LOCKED,
    STATE_HEATING,
    STATE_SCHEDULED,
    STATE_STANDBY,
)

# Fields worth exposing as attributes / sensors.
KNOWN_FIELDS: Final = (
    "powerStatus",
    "realTemp",
    "heatingTemp",
    "constantTemp",
    "heatStatus",
    "workModel",
    "deviceStatus",
    "errorCode",
    "mainTain",
    "protectLevel",
    "instantHeating",
    "disinfection",
    "increaseCapacity",
    "aes",
    "mesotherm",
    "mesothermTemp",
    "peekValley",
    "pvStartTime",
    "pvEndTime",
    "preheatStatus1",
    "preheatStatus2",
    "preheatStatus3",
    "preheatStatus4",
    "preheatTime",
    "preheatTime2",
    "timerOneStartTime",
    "timerOneEndTime",
    "timerTwoStartTime",
    "timerTwoEndTime",
    "antiscaleFilter",
    "autoSterilizeHeating",
    "fixedPointOne",
    "fpOneTime",
    "fpOneKeepTime",
    "fpTwoTime",
    "fpTwoKeepTime",
    "timingType",
    "timingData",
)


def _parse_status_info(device_data: dict[str, Any]) -> dict[str, Any]:
    """Return the parsed ``statusInfo`` document of a device record."""
    raw = device_data.get("statusInfo")
    if not raw:
        nested = device_data.get("appDeviceStatusInfoEntity")
        if isinstance(nested, dict):
            raw = nested.get("statusInfo")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def extract_output_data(device_data: dict[str, Any]) -> dict[str, Any]:
    """Return the reported property bag of the device."""
    parsed = _parse_status_info(device_data)
    events = parsed.get("events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("identifier") == "post":
                output = event.get("outputData")
                if isinstance(output, dict):
                    return output
    output = parsed.get("outputData")
    return output if isinstance(output, dict) else {}


def extract_faults(device_data: dict[str, Any]) -> list[str]:
    """Return fault/warning messages reported by the device."""
    parsed = _parse_status_info(device_data)
    events = parsed.get("events")
    messages: list[str] = []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("identifier") not in ("faultReportEvent", "warnReportEvent"):
                continue
            payload = event.get("outputData") or []
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("errorCode") or "")
                if not code or code in ("0", "W0"):
                    continue
                content = str(item.get("errorContent") or "").strip()
                messages.append(f"{code} {content}".strip())
    # De-duplicate while keeping order.
    return list(dict.fromkeys(messages))


def numeric(output: dict[str, Any], *keys: str) -> float | None:
    """Return the first key that holds a finite number."""
    for key in keys:
        try:
            value = float(output[key])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def flag(output: dict[str, Any], *keys: str) -> bool | None:
    """Return the first key that holds a 0/1 flag as a bool."""
    value = numeric(output, *keys)
    return None if value is None else value != 0


def derive_work_state(output: dict[str, Any]) -> str | None:
    """Mirror the state derivation of the official H5 client."""
    power = flag(output, "powerStatus", "powerOn")
    if power is None:
        return None
    if not power:
        return "off"
    if flag(output, "heatStatus"):
        return STATE_HEATING
    scheduled = (
        (flag(output, "preheatStatus3") and numeric(output, "preheatTime"))
        or flag(output, "preheatStatus1")
        or flag(output, "fixedPointOne")
        or flag(output, "preheatStatus2")
        or flag(output, "peekValley")
    )
    return STATE_SCHEDULED if scheduled else STATE_STANDBY


def temperature_command(value: float) -> dict[str, str]:
    """Build the ``Temperature`` input for the electric water heater service."""
    return {"Temperature": str(int(round(value)))}


def power_command(on: bool) -> dict[str, str]:
    """Build the ``powerStatus`` input."""
    return {"powerStatus": "1" if on else "0"}


def switch_command(command_field: str, on: bool, output: dict[str, Any]) -> dict[str, Any]:
    """Build the input bag for one of the auxiliary switches."""
    from .const import SWITCH_NESTED_FIELDS  # local import avoids a cycle

    nested = SWITCH_NESTED_FIELDS.get(_switch_key(command_field))
    if not nested:
        return {command_field: "1" if on else "0"}
    body: dict[str, Any] = {"OnOff": "1" if on else "0"}
    for target, source in nested.items():
        value = output.get(source)
        if value not in (None, ""):
            body[target] = str(value)
    return {command_field: body}


def _switch_key(command_field: str) -> str:
    from .const import SWITCH_TYPES

    for key, _status, command, _icon in SWITCH_TYPES:
        if command == command_field:
            return key
    return ""


def observed_switches(output: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """Return the switches this device actually reports."""
    from .const import SWITCH_TYPES

    return [spec for spec in SWITCH_TYPES if spec[1] in output]


def heater_mode_value(output: dict[str, Any]) -> int | None:
    """Return the reported heating mode (`workModel`)."""
    value = numeric(output, HEATER_MODE_STATUS_FIELD)
    return None if value is None else int(value)


def heater_mode_option(output: dict[str, Any]) -> str | None:
    """Return the heating mode as a select option key.

    A mode the device reports but this table does not name is returned as
    ``mode_<n>``: the current mode is then never silently lost, and another
    model family can still be switched back to what it was using.
    """
    value = heater_mode_value(output)
    if value is None:
        return None
    for key, mapped in HEATER_MODES:
        if mapped == value:
            return key
    return f"mode_{value}"


def heater_mode_command(option: str) -> dict[str, str] | None:
    """Build the `HeaterMode` input for a select option, or None if unknown."""
    for key, value in HEATER_MODES:
        if key == option:
            return {HEATER_MODE_COMMAND_FIELD: str(value)}
    if option.startswith("mode_"):
        try:
            return {HEATER_MODE_COMMAND_FIELD: str(int(option[5:]))}
        except ValueError:
            return None
    return None


def temperature_is_adjustable(output: dict[str, Any]) -> bool:
    """Return whether the device still accepts a target temperature."""
    return heater_mode_value(output) != HEATER_MODE_TEMPERATURE_LOCKED
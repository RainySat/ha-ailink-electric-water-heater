"""Constants for the Ai-LiNK (A.O. Smith) electric water heater integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ailink_ewh"
NAME: Final = "Ai-LiNK Water Heater"
MANUFACTURER: Final = "A.O. Smith"

# --- config entry / options keys -------------------------------------------
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_USER_ID: Final = "user_id"
CONF_FAMILY_ID: Final = "family_id"
CONF_COOKIE: Final = "cookie"
CONF_DEVICE_ID: Final = "device_id"
CONF_DEVICE_NAME: Final = "device_name"
CONF_PRODUCT_TYPE: Final = "product_type"
CONF_DEVICE_TYPE: Final = "device_type"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_MIN_TEMP: Final = "min_temp"
CONF_MAX_TEMP: Final = "max_temp"
CONF_EXTRA_ATTRIBUTES: Final = "extra_attributes"
CONF_CREATE_ALL_SWITCHES: Final = "create_all_switches"

DEFAULT_SCAN_INTERVAL: Final = 60
DEFAULT_MIN_TEMP: Final = 35.0
DEFAULT_MAX_TEMP: Final = 75.0
DEFAULT_EXTRA_ATTRIBUTES: Final = True
DEFAULT_CREATE_ALL_SWITCHES: Final = False
DEFAULT_NAME: Final = "A.O. Smith water heater"

# --- cloud endpoint --------------------------------------------------------
# Reverse engineered from the official AI-LiNK H5 client (China),
# served from ailink-appservice-h5-prd.hotwater.com.cn.
API_BASE: Final = "https://ailink-api.hotwater.com.cn"
API_PREFIX: Final = "/AiLinkService"

PATH_HOMEPAGE: Final = f"{API_PREFIX}/appDevice/getHomepageV2"
PATH_DEVICE_INFO: Final = f"{API_PREFIX}/appDevice/getDeviceCurrInfo"
PATH_INVOKE: Final = f"{API_PREFIX}/device/invokeMethod"
PATH_LAST_TOKEN: Final = f"{API_PREFIX}/api/getLastToken"

# A couple of endpoints re-issue a token in the Authorization *response* header
# once the presented (account-current) token has expired.  getAntifreeze is the
# one that answers 200 while doing so, which makes it a safe "mint" call.
PATH_MINT: Final = f"{API_PREFIX}//appDevice/getAntifreeze"

# Request signing (see the official H5 bundle):
#   md5data = md5(exact request bytes)
#   sign    = md5(md5data + timestamp + nonce + SIGN_SECRET)
#   encode  = md5(sorted values of the body + ENCODE_SALT)
SIGN_SECRET: Final = "ng957stzh4zy3dts"
ENCODE_SALT: Final = "AILink_2021#"

SOURCE: Final = "IOS"
VERSION: Final = "V1.0.1"

# --- device protocol -------------------------------------------------------
# Electric water heaters use one single service identifier with a bag of
# input fields (unlike gas water heaters which have one identifier per action).
SERVICE_SET_EWH: Final = "SetElectricWaterHeater"

# Supported switch entities: (translation key, status field, command field, icon)
SWITCH_TYPES: Final = (
    ("instant_heating", "instantHeating", "instantHeating", "mdi:flash"),
    ("disinfection", "disinfection", "Disinfection", "mdi:shield-sun"),
    ("aes", "aes", "AES", "mdi:leaf"),
    ("peak_valley", "peekValley", "PeakValley", "mdi:chart-bell-curve-cumulative"),
    ("mesotherm", "mesotherm", "Mesotherm", "mdi:thermometer-low"),
    ("capacity_boost", "increaseCapacity", "Max", "mdi:water-plus"),
)

# Switches whose command needs extra fields taken from the current status.
SWITCH_NESTED_FIELDS: Final = {
    "mesotherm": {"Temperature": "mesothermTemp"},
    "peak_valley": {"OpenTime": "pvStartTime", "CloseTime": "pvEndTime"},
}

# --- heating modes ---------------------------------------------------------
# The device reports `workModel` and is switched with `HeaterMode`.  The official
# H5 client builds that list per model family; for the models this integration
# targets (EWH-HGAWi and relatives) it is 1/2/4.
#
# Do not derive the value from the position in the list: other families reuse the
# same field with their own labels AND their own values (the third entry is 0 for
# the PE/NPE, E9W, BPW and D1 families, 3 for 50FW, and 4 for HGX/HGE/HT5).
HEATER_MODE_STATUS_FIELD: Final = "workModel"
HEATER_MODE_COMMAND_FIELD: Final = "HeaterMode"
HEATER_MODES: Final = (
    ("single_tank", 1),
    ("dual_tank", 2),
    ("winter_large_volume", 4),
)
# In this mode the device stops accepting a target temperature; the official
# client hides the control as well (`return 4 !== workModel`).
HEATER_MODE_TEMPERATURE_LOCKED: Final = 4

PLATFORMS: Final = ["water_heater", "switch", "select", "sensor", "binary_sensor"]

# Work state reported by the official H5 client.
STATE_HEATING: Final = "heating"
STATE_SCHEDULED: Final = "scheduled"
STATE_STANDBY: Final = "standby"
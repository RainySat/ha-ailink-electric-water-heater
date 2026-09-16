#!/usr/bin/env bash
# Copy the integration into a Home Assistant installation and verify it imports.
#
# Works for a docker-based HA on another host (the usual case) or locally.
#
#   HA_HOST=root@ha.local HA_CONFIG=/opt/homeassistant/config ./scripts/deploy.sh
#   ./scripts/deploy.sh --restart          # restart HA through the REST API
#   CONTAINER=home-assistant-free ./scripts/deploy.sh
#
# Environment:
#   HA_HOST    ssh target (empty = run locally)
#   HA_CONFIG  path of the HA config dir on that host
#   CONTAINER  docker container name (used for the import check / restart)
#   HASS_URL   e.g. http://homeassistant.local:8123  (only for --restart)
#   HASS_TOKEN a long-lived access token             (only for --restart)
set -euo pipefail

HA_HOST="${HA_HOST:-}"
HA_CONFIG="${HA_CONFIG:-/config}"
CONTAINER="${CONTAINER:-home-assistant}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -n "$HA_HOST" ]]; then
  run() { ssh -o BatchMode=yes "$HA_HOST" "$@"; }
else
  run() { bash -c "$*"; }
fi

echo "→ 打包并同步 custom_components/ailink_ewh"
find custom_components/ailink_ewh -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
tar czf /tmp/ailink_ewh.tgz custom_components/ailink_ewh
if [[ -n "$HA_HOST" ]]; then
  scp -q /tmp/ailink_ewh.tgz "$HA_HOST:/tmp/"
  run "cd '$HA_CONFIG' && rm -rf custom_components/ailink_ewh && tar xzf /tmp/ailink_ewh.tgz && echo '  已解压到 $HA_CONFIG'"
else
  run "cd '$HA_CONFIG' && rm -rf custom_components/ailink_ewh && tar xzf /tmp/ailink_ewh.tgz && echo '  已解压到 $HA_CONFIG'"
fi

echo "→ 在 HA 容器里做 import 校验"
run "docker exec $CONTAINER python -c '
import importlib, sys
sys.path.insert(0, \"/config/custom_components\")
mods = [\"ailink_ewh.api\",\"ailink_ewh.const\",\"ailink_ewh.protocol\",\"ailink_ewh.coordinator\",
        \"ailink_ewh.entity\",\"ailink_ewh.config_flow\",\"ailink_ewh.water_heater\",\"ailink_ewh.switch\",
        \"ailink_ewh.sensor\",\"ailink_ewh.binary_sensor\",\"ailink_ewh.diagnostics\",\"ailink_ewh\"]
bad = 0
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad += 1
        print(\"FAIL\", m, type(e).__name__, e)
print(\"import check:\", \"OK\" if not bad else f\"{bad} failures\")
'"

if [[ "${1:-}" == "--restart" ]]; then
  : "${HASS_URL:?请设置 HASS_URL}"; : "${HASS_TOKEN:?请设置 HASS_TOKEN}"
  echo "→ 重启 Home Assistant"
  curl -s -o /dev/null -w "  restart HTTP %{http_code}\n" -X POST \
    "${HASS_URL%/}/api/services/homeassistant/restart" \
    -H "Authorization: Bearer $HASS_TOKEN" -H "Content-Type: application/json" -d '{}'
fi
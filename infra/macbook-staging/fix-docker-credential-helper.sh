#!/usr/bin/env bash
set -euo pipefail

config_dir="${DOCKER_CONFIG:-${HOME}/.docker}"
config_path="${config_dir}/config.json"

mkdir -p "${config_dir}"

if [ -f "${config_path}" ]; then
  backup_path="${config_path}.bak.$(date +%Y%m%d%H%M%S)"
  cp "${config_path}" "${backup_path}"
  echo "Backed up ${config_path} to ${backup_path}"
else
  printf '{}\n' > "${config_path}"
  echo "Created ${config_path}"
fi

python3 - "${config_path}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8") or "{}")
except json.JSONDecodeError:
    payload = {}

removed = []
for key in ("credsStore", "credHelpers"):
    if key in payload:
        payload.pop(key)
        removed.append(key)

payload.setdefault("auths", {})
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if removed:
    print("Removed Docker credential helper keys: " + ", ".join(removed))
else:
    print("No Docker credential helper keys were present.")
PY

echo "Testing public image pulls without macOS Keychain credential helper..."
docker pull python:3.12-slim
docker pull pgvector/pgvector:pg16

echo "Docker credential helper cleanup completed."

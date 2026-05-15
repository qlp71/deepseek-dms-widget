#!/usr/bin/env bash
set -e
PLUGIN_ID="DeepSeekWidget"
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$HOME/.config/DankMaterialShell/plugins/$PLUGIN_ID"
OLD_DST="$HOME/.config/DankMaterialShell/plugins/DeepSeekUsageWidget"

[ -d "$OLD_DST" ] && rm -rf "$OLD_DST" && echo "Removed old plugin: $OLD_DST"

mkdir -p "$DST"
rsync -av --inplace \
  --exclude='.superpowers' \
  --exclude='docs' \
  --exclude='.git' \
  --exclude='sync.sh' \
  "$SRC/" "$DST/"
echo "Synced to: $DST"

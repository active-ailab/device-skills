#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ACTIVE_SKILLS_ROOT="$(cd "$SKILL_ROOT/../.." && pwd)"
SHARED_EXPORTER="$ACTIVE_SKILLS_ROOT/active_skills_install/export_skill_for_github.sh"

[[ -x "$SHARED_EXPORTER" ]] || {
  echo "[ERR] shared exporter not found or not executable: $SHARED_EXPORTER" >&2
  exit 1
}

exec "$SHARED_EXPORTER" --skill-dir "$SKILL_ROOT" "$@"

#!/usr/bin/env bash
# SentinelChat dev data reset (macOS/Linux/Git Bash). Deletes the LOCAL DEV DATA so you start from a
# clean slate: Postgres (users, chats, cases, tickets), Qdrant (chat memories, Work-tab runs) and the
# local upload / run / log folders. It never touches .env, other Docker projects, or
# `docker volume prune`.
#
# Usage:
#   bash scripts/reset-dev.sh                       # wipe this project's Docker volumes + local files, then
#                                                   # bring up an empty, migrated database
#   bash scripts/reset-dev.sh --keep-stack          # keep the containers running; empty Postgres and Qdrant
#                                                   # in place (keeps the IPC standards index)
#   bash scripts/reset-dev.sh --keep-stack --include-standards   # ...and drop the standards index too
#   bash scripts/reset-dev.sh --force               # no confirmation prompt
#   bash scripts/reset-dev.sh --dry-run             # say what would happen, change nothing
#   bash scripts/reset-dev.sh --no-rebuild          # do not bring Postgres/Qdrant up and migrate afterwards
#
# After a reset the first user you register becomes Admin. Also clear the browser's localStorage
# (DevTools > Application > Local Storage) or you will see ghost conversations.

set -uo pipefail

KEEP_STACK=0
INCLUDE_STANDARDS=0
FORCE=0
DRY_RUN=0
NO_REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --keep-stack) KEEP_STACK=1 ;;
    --include-standards) INCLUDE_STANDARDS=1 ;;
    --force) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-rebuild) NO_REBUILD=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

COMPOSE_FILE="infra/development/docker-compose.yml"
# The IPC standards index Agent 2's precedent search reads (the current name, and the older one the
# legacy indexer and agent2_config.yaml still use). It costs embeddings to rebuild, so a
# --keep-stack reset keeps it unless asked.
STANDARDS_COLLECTIONS=("pcb_standards_precedents" "ipc_defect_precedents")

env_value() { # env_value NAME DEFAULT
  local line value
  if [ -f .env ]; then
    line="$(grep -E "^$1=" .env | tail -n 1 | tr -d '\r')"
    if [ -n "$line" ]; then
      value="${line#*=}"
      value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
      if [ -n "$value" ]; then printf '%s' "$value"; return; fi
    fi
  fi
  printf '%s' "$2"
}

step() { if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] $1"; else echo "==> $1"; fi; }

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found. Install Docker first."; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running. Start it and retry."; exit 1
fi

COMPOSE=(docker compose -f "$COMPOSE_FILE")
[ -f .env ] && COMPOSE+=(--env-file .env)

QDRANT_URL="$(env_value QDRANT_URL http://localhost:6333)"; QDRANT_URL="${QDRANT_URL%/}"
DB_URL="$(env_value DATABASE_URL postgresql+asyncpg://sentinelchat:sentinelchat@localhost:5433/sentinelchat)"
DB_USER="sentinelchat"; DB_NAME="sentinelchat"
if [[ "$DB_URL" =~ ^postgresql(\+[a-z0-9]+)?://([^:]+):([^@]*)@[^/]+/([^?]+) ]]; then
  DB_USER="${BASH_REMATCH[2]}"; DB_NAME="${BASH_REMATCH[4]}"
fi

# The local folders the backend writes to (settings defaults, overridable in .env). Never delete
# anything outside the repo, whatever .env says.
DATA_DIRS=()
for pair in "CHAT_UPLOAD_DIR:data/uploads" "ORCHESTRATOR_DATA_DIR:data/orchestrator" "LOG_DIR:data/logs"; do
  name="${pair%%:*}"; default="${pair#*:}"
  relative="$(env_value "$name" "$default")"
  case "$relative" in
    /*|[A-Za-z]:*|*..*) echo "    Skipping $name=$relative - it is not a plain path inside the repo." ;;
    *) DATA_DIRS+=("$ROOT_DIR/${relative#./}") ;;
  esac
done

STANDARDS_POINTS=0
if command -v curl >/dev/null 2>&1; then
  for collection in "${STANDARDS_COLLECTIONS[@]}"; do
    points="$(curl -fsS --max-time 3 "$QDRANT_URL/collections/$collection" 2>/dev/null \
      | grep -o '"points_count":[0-9]*' | head -n 1 | cut -d: -f2 || true)"
    STANDARDS_POINTS=$(( STANDARDS_POINTS + ${points:-0} ))
  done
fi

echo
echo "This will DELETE the local dev data:"
if [ "$KEEP_STACK" = 1 ]; then
  echo "  - Postgres '$DB_NAME': every table (the schema is dropped and recreated)"
  if [ "$INCLUDE_STANDARDS" = 1 ]; then
    echo "  - Qdrant ($QDRANT_URL): every collection, including the IPC standards index"
  else
    echo "  - Qdrant ($QDRANT_URL): every collection EXCEPT the IPC standards index"
  fi
else
  echo "  - this project's Docker volumes (sentinelchat_*): Postgres, Qdrant, LangFuse"
  if [ "$STANDARDS_POINTS" -gt 0 ]; then
    echo "    Qdrant holds the IPC standards index ($STANDARDS_POINTS points); it goes too and must be"
    echo "    re-ingested (embeddings cost money). Use --keep-stack to keep it."
  fi
fi
for dir in "${DATA_DIRS[@]}"; do
  [ -e "$dir" ] && echo "  - $dir"
done
echo "It does NOT touch .env or any other Docker project."
echo

if [ "$DRY_RUN" = 1 ]; then
  echo "[dry-run] nothing will be changed."
elif [ "$FORCE" != 1 ]; then
  read -r -p "Type 'yes' to continue: " answer
  if [ "$answer" != "yes" ]; then echo "Aborted - nothing was changed."; exit 0; fi
fi

run() { if [ "$DRY_RUN" = 1 ]; then :; else "$@"; fi; }

if [ "$KEEP_STACK" = 1 ]; then
  step "Postgres: dropping and recreating the public schema in '$DB_NAME'"
  if [ "$DRY_RUN" != 1 ]; then
    if ! "${COMPOSE[@]}" exec -T db psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
        -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"; then
      echo "ERROR: could not reset Postgres. Is the 'db' container running?"; exit 1
    fi
  fi

  step "Qdrant: deleting collections"
  names="$(curl -fsS --max-time 5 "$QDRANT_URL/collections" 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4 || true)"
  if [ -z "$names" ]; then
    echo "WARNING: no collections found, or Qdrant is not reachable at $QDRANT_URL; its data was not touched."
  fi
  for name in $names; do
    keep=0
    for standards in "${STANDARDS_COLLECTIONS[@]}"; do [ "$name" = "$standards" ] && keep=1; done
    if [ "$keep" = 1 ] && [ "$INCLUDE_STANDARDS" != 1 ]; then
      echo "    keeping $name (the IPC standards index; --include-standards drops it)"
      continue
    fi
    echo "    deleting $name"
    run curl -fsS -X DELETE --max-time 30 "$QDRANT_URL/collections/$name" >/dev/null
  done
else
  step "Docker: stopping the stack and removing this project's volumes"
  if [ "$DRY_RUN" != 1 ]; then
    if ! "${COMPOSE[@]}" --profile full down -v --remove-orphans; then
      echo "ERROR: docker compose down failed. Nothing else was deleted."; exit 1
    fi
  fi
fi

for dir in "${DATA_DIRS[@]}"; do
  if [ -e "$dir" ]; then
    step "Deleting $dir"
    run rm -rf "$dir"
  fi
done

if [ "$NO_REBUILD" != 1 ]; then
  if [ "$KEEP_STACK" != 1 ]; then
    step "Starting an empty Postgres + Qdrant (docker compose up -d --wait)"
    if [ "$DRY_RUN" != 1 ]; then
      "${COMPOSE[@]}" up -d --wait db qdrant || { echo "ERROR: could not start Postgres + Qdrant."; exit 1; }
    fi
  fi
  step "Creating the schema (uv run alembic upgrade head)"
  if [ "$DRY_RUN" != 1 ]; then
    uv run alembic upgrade head || { echo "ERROR: alembic upgrade head failed."; exit 1; }
  fi
fi

echo
if [ "$DRY_RUN" = 1 ]; then
  echo "Dry run finished - nothing was changed."
else
  echo "Done. Next:"
  echo "  1. Clear the browser's localStorage for the app (DevTools > Application > Local Storage)."
  echo "  2. Start the app (infra/development/scripts/unix/start-dev.sh) and register - the first user"
  echo "     becomes Admin."
  [ "$NO_REBUILD" = 1 ] && echo "  (You skipped the rebuild: run setup-dev.sh, then 'uv run alembic upgrade head'.)"
fi

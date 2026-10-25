#!/bin/bash
# Скрипт установки облачного окружения Claude Code для VisaCRM.
# Куда: claude.ai/code → окружение → Setup script (вставить текст). Облако
# запускает его от root на Ubuntu 24.04 до старта сессии и сохраняет снимок
# файлов, если скрипт уложился примерно в 5 минут. Процессы в снимок не
# попадают: Postgres в каждой сессии запускает хук .claude/hooks/cloud-session.cjs.
#
# В образе уже есть uv, ruff, pytest, Node 20–22 и PostgreSQL 16, поэтому SDK
# здесь не ставятся. Скрипт:
#   - делает Node 22 (как в CI) узлом по умолчанию, если это не так;
#   - кладёт в кэш Python 3.12 для uv;
#   - создаёт в Postgres роль и базу visacrm, как в docker-compose.yml и CI;
#   - прогревает кэши uv и npm по lock-файлам проекта, если найдёт checkout,
#     и проверяет базу тестом /api/health.
# Браузеров Playwright нет: их CDN не входит в сеть Trusted, e2e остаются в CI.
#
# Скрипт всегда завершается с кодом 0: при ненулевом коде сессия не стартует.

NODE_MAJOR=22
PYTHON_VERSION=3.12

log() { echo "cloud-setup: $*"; }

# --- Node 22 ------------------------------------------------------------------
if ! node --version 2>/dev/null | grep -q "^v${NODE_MAJOR}\."; then
  node22=$(ls -d /root/.nvm/versions/node/v${NODE_MAJOR}.*/bin /usr/local/nvm/versions/node/v${NODE_MAJOR}.*/bin \
    /opt/nvm/versions/node/v${NODE_MAJOR}.*/bin /opt/node*${NODE_MAJOR}*/bin 2>/dev/null | sort -V | tail -n 1)
  if [ -n "$node22" ] && [ -x "$node22/node" ]; then
    for tool in node npm npx; do ln -sf "$node22/$tool" "/usr/local/bin/$tool"; done
    hash -r
  else
    log "Node ${NODE_MAJOR} не найден, остаётся $(node --version 2>/dev/null || echo 'без Node')" >&2
  fi
fi

# --- Python для uv -------------------------------------------------------------
uv python install "$PYTHON_VERSION" >/dev/null 2>&1 || log "uv не поставил Python $PYTHON_VERSION" >&2

# --- PostgreSQL 16: роль и база как в CI -----------------------------------------
service postgresql start >/dev/null 2>&1 || log "Postgres не запустился" >&2
for _ in $(seq 1 20); do pg_isready -h localhost -p 5432 -q && break; sleep 0.5; done
# Суперпользователь, как POSTGRES_USER в CI: миграция 0001 создаёт роль visacrm_app.
# Пароль — одноразовый локальный из docker-compose.yml, не секрет.
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -tAc \
  "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'visacrm') THEN
     CREATE ROLE visacrm LOGIN SUPERUSER PASSWORD 'visacrm'; END IF; END \$\$" >/dev/null 2>&1 ||
  log "роль visacrm не создана" >&2
if [ "$(runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'visacrm'" 2>/dev/null)" != "1" ]; then
  runuser -u postgres -- createdb -O visacrm visacrm >/dev/null 2>&1 || log "база visacrm не создана" >&2
fi

# --- Прогрев по lock-файлам проекта ----------------------------------------------
repo=$(git rev-parse --show-toplevel 2>/dev/null)
if [ ! -f "$repo/backend/uv.lock" ]; then
  lock=$(find /home /root /workspace /workspaces -maxdepth 4 -path '*/backend/uv.lock' 2>/dev/null | head -n 1)
  repo=${lock%/backend/uv.lock}
fi
if [ -n "$repo" ] && [ -f "$repo/backend/uv.lock" ]; then
  (cd "$repo/backend" && uv sync >/dev/null 2>&1) || log "uv sync не прошёл" >&2
  (cd "$repo/frontend" && npm ci --no-audit --no-fund >/dev/null 2>&1) || log "npm ci не прошёл" >&2
  (cd "$repo/backend" && timeout 120 uv run pytest -q tests/test_health.py >/dev/null 2>&1) ||
    log "проверочный тест не прошёл: сессия разберётся при первом pytest" >&2
else
  log "checkout не найден: зависимости поставит первый uv run / npm ci в сессии"
fi

log "$(node --version 2>/dev/null) · $(uv --version 2>/dev/null) · $(psql --version 2>/dev/null)"
exit 0

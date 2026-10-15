# VisaCRM

CRM для визовиков: картотека клиентов, кейсы, очереди в визовые центры, финансы и аналитика. Позже — документы, затем открытие для других визовиков (SaaS), Telegram-боты, AI-агент изменений и модуль слотов.

- Продукт: [docs/PRD.md](docs/PRD.md)
- Дорожная карта: [docs/roadmap.md](docs/roadmap.md)
- Этап 1 (клиенты): [docs/clients-module.md](docs/clients-module.md)
- Решения: [docs/decisions.md](docs/decisions.md) · Открытые вопросы: [docs/open-questions.md](docs/open-questions.md)
- Бэклог: [docs/backlog.md](docs/backlog.md)
- Деплой на Railway: [docs/deploy.md](docs/deploy.md) · Эксплуатация: [docs/operations.md](docs/operations.md)
- Макеты экранов (справочно): [docs/prototype/](docs/prototype/README.md)

## Стек

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 16 · React 18 · TypeScript · Vite · TanStack Query · react-i18next · Railway (EU West)

## Запуск локально

Нужны Docker, [uv](https://docs.astral.sh/uv/) и Node.js 22.

```bash
docker compose up -d

cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python -m app.cli create-admin you@example.com   # первый админ, спросит пароль
uv run uvicorn app.main:app --reload    # http://localhost:8000/api/health

cd ../frontend
npm install
npm run dev                             # http://localhost:5173
```

## Деплой

Railway собирает образ из `Dockerfile` (фронтенд + бэкенд в одном контейнере) по настройкам `railway.json`. Переменные окружения: `DATABASE_URL` (из сервиса Postgres), `SECRET_KEY`, `VAULT_MASTER_KEY`, `ENV=production`. Регион всех сервисов — EU West.

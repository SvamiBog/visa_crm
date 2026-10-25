---
paths:
  - "backend/**/*.py"
---
> Adapted from ECC `rules/python/coding-style.md`, `patterns.md` and `security.md` (github.com/affaan-m/ECC, MIT). `CLAUDE.md` and the project docs take precedence over this file.

# Python Coding Style

- Python 3.12; PEP 8 through ruff as set in `backend/pyproject.toml` (line length 100; rules E, F, I, UP, B, SIM): `uv run ruff check . && uv run ruff format .` from `backend/`. Black, isort, mypy and bandit are not project tools.
- Type annotations on every signature; modern syntax (`X | None`, `list[str]`); `Annotated[...]` for FastAPI dependencies.
- Money: `Decimal` built from strings (`Decimal("0.01")`), never float; rounding through `finance.money()`.
- Time: aware UTC datetimes; dates without time as `date`.
- Value objects that must not change: `@dataclass(frozen=True)` (`backend/app/core/stay.py`). Request and response bodies: Pydantic models next to their router.
- Logging: `logger = logging.getLogger(__name__)`, ids and technical facts only; `print` only in `backend/app/cli.py`.
- Blocking calls (boto3, SMTP, api.nbrb.by) run through `asyncio.to_thread` (`backend/app/core/storage.py`).
- Settings and secrets come from `backend/app/core/config.py` (pydantic-settings), not from `os.environ` in code.

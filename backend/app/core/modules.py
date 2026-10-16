"""Product modules (docs/PRD.md, «Карта модулей»). The admin turns them on per specialist;
a specialist gives employees a subset of their own. Code features, so a code list."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, Field


@dataclass(frozen=True)
class Module:
    key: str
    block: str
    stage: int


MODULES: tuple[Module, ...] = (
    Module("clients", "core", 1),
    Module("cases", "core", 1),
    Module("vault", "core", 1),
    Module("tasks", "core", 1),
    Module("country_rules", "core", 1),
    Module("analytics", "analytics", 1),
    Module("document_templates", "documents", 2),
    Module("form_autofill", "documents", 2),
    Module("passport_mrz", "documents", 2),
    Module("package_check", "documents", 2),
    Module("stay_calculator", "tools", 2),
    Module("ai_documents", "documents", 2),
    Module("client_bot", "communications", 4),
    Module("specialist_bot", "communications", 4),
    Module("inbox", "communications", 4),
    Module("invoices", "finance", 4),
    Module("contracts", "finance", 4),
    Module("price_calculator", "tools", 4),
    Module("ai_agent", "knowledge", 5),
    Module("refusal_base", "knowledge", 5),
    Module("process_insights", "analytics", 5),
    Module("slots", "slots", 6),
)
MODULE_KEYS = frozenset(m.key for m in MODULES)
# A new specialist starts with the core CRM (stage 1); the admin can change it.
DEFAULT_MODULES = tuple(m.key for m in MODULES if m.stage == 1)
_ORDER = {m.key: i for i, m in enumerate(MODULES)}


def normalize_modules(keys: list[str]) -> list[str]:
    """Unique, known keys in registry order. Raises ValueError on an unknown key."""
    unknown = set(keys) - MODULE_KEYS
    if unknown:
        raise ValueError(f"unknown modules: {', '.join(sorted(unknown))}")
    return sorted(set(keys), key=_ORDER.__getitem__)


# Request field types shared by the admin and employee APIs.
ModuleList = Annotated[list[str], AfterValidator(normalize_modules)]
TaxRate = Annotated[
    Decimal,
    Field(ge=0, le=100, max_digits=5, decimal_places=2),
    AfterValidator(lambda rate: rate.quantize(Decimal("0.01"))),
]

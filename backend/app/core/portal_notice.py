"""The data notice of the client portal (GDPR Art. 13) and the explicit consent to special
categories of data (Art. 9(2)(a)); backlog 2.9, docs/decisions.md → «Портал клиента и
вход по коду».

The platform's text of a version, with the specialist's details as the controller put in.
The client sees it before the first upload; the text exactly as shown is kept with its
SHA-256 (portal_notices), so the specialist can show what the client was told. A new
version is a new file in notices/, never an edit of an old one: notices already shown are
in the database anyway.

DRAFT 1, not yet checked by a lawyer (docs/open-questions.md): the specialist is
registered in Poland and works under the GDPR and Polish law.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

VERSION = "1"
LANGUAGES = ("ru", "en")
# The supervisory authority when the specialist has named none: she is registered in Poland.
DEFAULT_AUTHORITY = (
    "Prezes Urzędu Ochrony Danych Osobowych (UODO), ul. Stawki 2, 00-193 Warszawa, uodo.gov.pl"
)

# The texts lie next to this module, one file a version and language, kept as they are.
_NOTICES = Path(__file__).with_name("notices")


def _read(language: str) -> str:
    # The same text whatever line endings a checkout gave the file: its hash is the proof.
    text = (_NOTICES / f"portal-{VERSION}.{language}.txt").read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").strip()


_TEXTS = {language: _read(language) for language in LANGUAGES}


@dataclass(frozen=True)
class Controller:
    name: str | None
    address: str | None
    email: str | None
    authority: str | None

    @property
    def ready(self) -> bool:
        return bool(self.name and self.address and self.email)


@dataclass(frozen=True)
class Notice:
    version: str
    language: str
    body: str

    @property
    def sha256(self) -> bytes:
        return hashlib.sha256(f"{self.version}\n{self.language}\n{self.body}".encode()).digest()


def language_of(requested: str | None) -> str:
    return requested if requested in LANGUAGES else LANGUAGES[0]


def render(controller: Controller, language: str) -> Notice:
    """The notice as the client will see it; the controller must be ready."""
    assert controller.ready
    body = _TEXTS[language_of(language)].format(
        name=controller.name,
        address=controller.address,
        email=controller.email,
        authority=controller.authority or DEFAULT_AUTHORITY,
    )
    return Notice(version=VERSION, language=language_of(language), body=body)

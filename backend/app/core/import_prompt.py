"""The universal prompt (backlog 1.20, docs/import-prompt.md): what the specialist gives an
AI together with her spreadsheets and the template, so that it makes a file in the VisaCRM
data format. The Russian text is the block of docs/import-prompt.md, the English one its
translation; the sheets and columns listed in both are those of app/core/data_format.py.
Tests keep all three in step.
"""

from pathlib import Path

LANGUAGES = ("ru", "en")
_PROMPTS = Path(__file__).with_name("prompts")


def import_prompt(language: str) -> str:
    return (_PROMPTS / f"import_{language}.txt").read_text(encoding="utf-8")

"""Passwords for client accounts by the rules of a site (справочник систем, the vault).

Characters easy to mistype when the password is read off the screen (0/O, 1/l/I) are
left out: the specialist often types it into a visa center form by hand.
"""

import secrets
from dataclasses import dataclass

LOWER = "abcdefghijkmnpqrstuvwxyz"
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DIGITS = "23456789"


@dataclass(frozen=True)
class PasswordRules:
    length: int
    lower: bool
    upper: bool
    digits: bool
    symbols: str  # the special characters the site accepts; empty — none


def generate_password(rules: PasswordRules) -> str:
    """A random password of the given length with at least one character of each kind
    the rules ask for. Raises ValueError if the rules cannot be met."""
    pools = [
        pool
        for pool, wanted in (
            (LOWER, rules.lower),
            (UPPER, rules.upper),
            (DIGITS, rules.digits),
            (rules.symbols, bool(rules.symbols)),
        )
        if wanted
    ]
    if not pools or rules.length < len(pools):
        raise ValueError("password rules cannot be met")
    chars = [secrets.choice(pool) for pool in pools]
    alphabet = "".join(pools)
    chars += [secrets.choice(alphabet) for _ in range(rules.length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)

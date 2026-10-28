"""A workspace's directions: «destination × submission country» rules and their cities
(backlog 1.20, docs/decisions.md → «Свои направления визовика»)."""

from collections.abc import Iterable


def city_key(city: str) -> str:
    """«Могилёв» and «могилев» are one city: case and «ё» do not matter."""
    return " ".join(city.casefold().replace("ё", "е").split())


def new_cities(existing: Iterable[str], wanted: Iterable[str]) -> list[str]:
    """Cities from `wanted` the direction does not have yet, in the order typed, with
    spaces tidied; empty entries are skipped."""
    seen = {city_key(c) for c in existing}
    added = []
    for city in wanted:
        name = " ".join(city.split())
        if name and city_key(name) not in seen:
            seen.add(city_key(name))
            added.append(name)
    return added

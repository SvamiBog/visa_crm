"""The server's start (start.sh): on a new stand the database may come up after the app, and
the app used to fail its migrations at once and never become healthy (PR stands,
docs/staging-setup.md). It waits for the database first, for a bounded time."""

import time

from app.core.db import wait_for_database

# A host that never resolves (RFC 2606): what a stand's app saw before its database was up.
NOWHERE = "postgresql+asyncpg://visacrm:visacrm@db.invalid:5432/visacrm"


async def test_a_database_that_answers_is_not_waited_for(migrated_database: str):
    started = time.monotonic()
    assert await wait_for_database(migrated_database, timeout=30, pause=0.1)
    assert time.monotonic() - started < 5


async def test_a_database_that_is_not_up_is_asked_again_until_the_time_runs_out():
    started = time.monotonic()
    assert not await wait_for_database(NOWHERE, timeout=1, pause=0.2)
    assert time.monotonic() - started >= 1

"""Error reports to Sentry (app/core/error_reports.py): nothing starts without SENTRY_DSN, and
a failure of a request reaches Sentry with where it failed and without what the request
carried (CLAUDE.md → «Персональные данные», docs/decisions.md)."""

import json
import logging
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest
import sentry_sdk
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app import main
from app.core import error_reports
from app.core.config import Settings, get_settings
from app.main import create_app

# A DSN of the right shape that leads nowhere: nothing is captured in these tests unless a
# collecting transport replaces the real one.
DSN = "https://0123456789abcdef0123456789abcdef@o1.ingest.de.sentry.io/2"
SERVER_KEY = "test-only-server-key-0123456789abcdef"
NAME = "Ivanova Olga"
PHONE = "+375291234567"
PASSWORD = "Passw0rd-of-the-client"
SESSION = "session-cookie-value"


class Collected(Transport):
    """Keeps the reports as they would go over the wire, after the scrubbers."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.events += [item.payload.json for item in envelope.items if item.type == "event"]


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Callable[..., Settings]]:
    """The settings of a server started with these variables, in an empty folder so that a
    developer's backend/.env sets nothing; the SDK is switched off again afterwards."""
    monkeypatch.chdir(tmp_path)

    def start(**variables: str | None) -> Settings:
        for name, value in variables.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return get_settings()

    yield start
    get_settings.cache_clear()
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


async def test_without_a_dsn_the_sdk_does_not_start_and_the_site_works(started):
    settings = started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=None)
    assert settings.sentry_dsn == ""
    assert error_reports.start_error_reports(settings) is False

    app = create_app()

    assert not sentry_sdk.get_client().is_active()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        response = await http.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_with_a_dsn_the_site_starts_the_sdk_with_the_scrubber(started):
    started(
        ENV="staging",
        SECRET_KEY=SERVER_KEY,
        SENTRY_DSN=DSN,
        RAILWAY_GIT_COMMIT_SHA="e810807c1e91de880db090b11daaa7a867dab80b",
    )

    create_app()

    client = sentry_sdk.get_client()
    assert client.is_active()
    assert client.options["dsn"] == DSN
    assert client.options["environment"] == "staging"
    assert client.options["release"] == "e810807c1e91de880db090b11daaa7a867dab80b"
    assert client.options["before_send"] is error_reports.scrub_event
    assert client.options["before_breadcrumb"] is error_reports.scrub_breadcrumb
    collected = client.options["data_collection"]
    assert collected["user_info"] is False
    assert collected["cookies"]["mode"] == "off"
    assert collected["url_query_params"]["mode"] == "off"
    assert collected["http_bodies"] == []
    assert collected["stack_frame_variables"] is False
    assert collected["database_query_data"] is False
    # No performance traces: they would carry every request's queries and timings.
    assert client.options["traces_sample_rate"] is None


def test_without_a_commit_the_release_is_left_to_the_sdk(started):
    settings = started(
        ENV="staging", SECRET_KEY=SERVER_KEY, SENTRY_DSN=DSN, RAILWAY_GIT_COMMIT_SHA=None
    )
    assert error_reports.options(settings)["release"] is None


async def _failing_request(monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    """A request full of a client's data whose handler fails: its status and the handler's
    name."""
    # create_app() would start the SDK again with the real transport.
    monkeypatch.setattr(main, "start_error_reports", lambda settings: False)
    app = create_app()

    @app.post("/api/test-failure")
    async def fail() -> None:
        logging.getLogger("app.test").warning("Saving the card of %s", NAME)
        raise ValueError(f"{NAME} {PHONE}")

    # Alembic's logging config switches off the loggers made before it.
    monkeypatch.setattr(logging.getLogger("app.core.server_errors"), "disabled", False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        response = await http.post(
            f"/api/test-failure?q={NAME}",
            json={"name": NAME, "phone": PHONE, "password": PASSWORD},
            headers={
                "Cookie": f"visacrm_session={SESSION}",
                "Authorization": f"Bearer {PASSWORD}",
                "User-Agent": "Mozilla/5.0",
                "X-Real-IP": "203.0.113.7",
                "Referer": f"http://t/contacts?q={NAME}",
            },
        )
    sentry_sdk.flush()
    return response.status_code, f"{fail.__module__}.{fail.__qualname__}"


async def test_an_unexpected_error_reaches_sentry_without_what_the_request_carried(
    started, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    settings = started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=DSN)
    sent_to = Collected()
    assert error_reports.start_error_reports(settings, transport=sent_to)

    with caplog.at_level(logging.WARNING):
        status, handler = await _failing_request(monkeypatch)

    assert status == 500
    # One report: the exception, not also the log line that names it.
    assert len(sent_to.events) == 1
    event = sent_to.events[0]
    sent = json.dumps(event, ensure_ascii=False)
    for secret in (NAME, "Ivanova", PHONE, PASSWORD, SESSION, "203.0.113.7"):
        assert secret not in sent
    error = event["exception"]["values"][-1]
    assert error["type"] == "ValueError"
    assert "value" not in error
    assert error["stacktrace"]["frames"][-1]["function"] == "fail"
    assert event["transaction"] == handler
    assert event["environment"] == "production"
    assert event["request"]["method"] == "POST"
    assert set(event["request"]["headers"]) == {"user-agent", "content-type", "content-length"}
    assert event["breadcrumbs"]["values"][-1]["message"] == "Saving the card of [Filtered]"
    # The server's own log is as it was.
    assert "failed: ValueError" in caplog.text


async def test_the_sdk_itself_collects_no_body_cookies_query_user_or_variables(
    started, monkeypatch: pytest.MonkeyPatch
):
    """The first lock alone: the SDK's settings without the scrubbers still keep what the
    request carried out of the report (the URL's path and the error's text are left for
    the scrubber)."""
    settings = started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=DSN)
    sent_to = Collected()
    sentry_sdk.init(
        **{**error_reports.options(settings), "before_send": None, "before_breadcrumb": None},
        transport=sent_to,
    )

    await _failing_request(monkeypatch)

    (event,) = sent_to.events
    request = event["request"]
    assert "data" not in request and "cookies" not in request
    assert not request.get("query_string")
    shown = {name for name, value in request["headers"].items() if value != "[Filtered]"}
    assert shown <= {"user-agent", "content-type", "content-length"}
    assert "user" not in event
    for frame in event["exception"]["values"][-1]["stacktrace"]["frames"]:
        assert "vars" not in frame
    sent = json.dumps(event, ensure_ascii=False)
    for secret in (PHONE, PASSWORD, SESSION, "203.0.113.7"):
        assert secret not in sent.replace(f"{NAME} {PHONE}", "")


def test_the_test_report_needs_a_dsn(started):
    settings = started(ENV="staging", SECRET_KEY=SERVER_KEY, SENTRY_DSN=None)
    assert error_reports.send_test_report(settings) is None
    assert not sentry_sdk.get_client().is_active()


def test_the_test_report_says_which_environment_sent_it(started):
    settings = started(ENV="staging", SECRET_KEY=SERVER_KEY, SENTRY_DSN=DSN)
    sent_to = Collected()

    event_id = error_reports.send_test_report(settings, transport=sent_to)

    (event,) = sent_to.events
    assert event["event_id"] == event_id
    assert event["exception"]["values"][-1]["type"] == "ErrorReportCheck"
    assert event["environment"] == "staging"


@pytest.mark.parametrize(
    ("status", "detail", "reported"),
    [
        (502, "storage_failed", True),  # the storage did not answer
        (503, "mail_unavailable", True),
        (404, "not_found", False),  # refusals by the rules are not errors
        (403, "forbidden", False),
        (429, "limit_reached", False),
    ],
)
async def test_an_answer_of_the_server_side_is_reported_with_its_code(
    started, monkeypatch: pytest.MonkeyPatch, status: int, detail: str, reported: bool
):
    settings = started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=DSN)
    sent_to = Collected()
    assert error_reports.start_error_reports(settings, transport=sent_to)
    monkeypatch.setattr(main, "start_error_reports", lambda settings: False)
    app = create_app()

    @app.get("/api/test-answer")
    async def answer() -> None:
        raise HTTPException(status, detail)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        assert (await http.get("/api/test-answer")).status_code == status
    sentry_sdk.flush()

    values = [event["exception"]["values"][-1] for event in sent_to.events]
    assert values == ([{**values[0], "type": "HTTPException", "value": detail}] if reported else [])


def test_outgoing_requests_carry_no_sentry_headers(started):
    """The bank's rates and the file storage are asked over http.client: they learn nothing
    of the error reports (trace id, environment, release)."""
    heard: list[dict[str, str]] = []

    class Listener(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            heard.append({name.lower(): value for name, value in self.headers.items()})
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Listener)
    Thread(target=server.handle_request, daemon=True).start()
    settings = started(ENV="production", SECRET_KEY=SERVER_KEY, SENTRY_DSN=DSN)
    assert error_reports.start_error_reports(settings, transport=Collected())

    with urlopen(f"http://127.0.0.1:{server.server_port}/rates", timeout=5) as answer:
        assert answer.status == 204
    server.server_close()

    (headers,) = heard
    assert "sentry-trace" not in headers
    assert "baggage" not in headers

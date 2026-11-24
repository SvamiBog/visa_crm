"""What an error report may carry to Sentry (app/core/error_reports.py): where it failed and
the API's error code, never clients' data, passwords, keys or the session (CLAUDE.md →
«Персональные данные», docs/decisions.md)."""

import json
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.error_reports import scrub_breadcrumb, scrub_event

NAME = "Ivanova Olga"
PHONE = "+375291234567"
PASSWORD = "Passw0rd-of-the-client"
SESSION = "session-cookie-value"
EMAIL = "olga.ivanova@example.com"
PASSPORT = "MP1234567"
SECRETS = (NAME, "Ivanova", PHONE, PASSWORD, SESSION, EMAIL, PASSPORT, "203.0.113.7")
ACCOUNT = "6f0b7f5e-3a0c-4c2e-9d55-7d3c7a4c1f10"
HANDLER = "app.api.contacts.update_contact"


def _frame(function: str, lineno: int) -> dict:
    return {
        "filename": "app/api/contacts.py",
        "abs_path": "/srv/backend/app/api/contacts.py",
        "function": function,
        "module": "app.api.contacts",
        "lineno": lineno,
        "in_app": True,
        "context_line": "    await session.flush()",
        "pre_context": ["    contact.phone = body.phone"],
        "post_context": ["    return contact"],
        # Local variables: the request body and the card itself.
        "vars": {"body": {"name": NAME, "phone": PHONE}, "password": PASSWORD},
    }


def _event() -> dict:
    """An error event shaped as the SDK hands it to before_send, with everything a request
    of the site may put into it."""
    return {
        "event_id": "847ffb005e0848bdb2e23949729d3533",
        "timestamp": "2026-11-24T17:33:16.270208Z",
        "level": "error",
        "platform": "python",
        "logger": "app.api.contacts",
        "server_name": "visacrm-web-7c9f",
        "release": "e810807c1e91de880db090b11daaa7a867dab80b",
        "environment": "production",
        "transaction": HANDLER,
        "transaction_info": {"source": "component"},
        "exception": {
            "values": [
                {
                    "type": "DataError",
                    "module": "asyncpg.exceptions",
                    # asyncpg quotes the value it could not store.
                    "value": f'invalid input for query argument $2: "{PHONE}" ({NAME})',
                    "mechanism": {"type": "chained", "handled": True, "exception_id": 1},
                    "stacktrace": {"frames": [_frame("update_contact", 412)]},
                }
            ]
        },
        "logentry": {
            "message": "Contact %s not saved",
            "formatted": f"Contact {NAME} not saved",
            "params": [NAME],
        },
        "request": {
            "method": "PATCH",
            "url": f"https://visacrm.example/api/contacts/{ACCOUNT}",
            "query_string": f"q={NAME}&email={EMAIL}",
            "cookies": {"session": SESSION},
            "data": {"name": NAME, "phone": PHONE, "passport": PASSPORT, "password": PASSWORD},
            "headers": {
                "host": "visacrm.example",
                "user-agent": "Mozilla/5.0",
                "content-type": "application/json",
                "content-length": "84",
                "cookie": f"session={SESSION}",
                "authorization": f"Bearer {PASSWORD}",
                "x-real-ip": "203.0.113.7",
                "referer": f"https://visacrm.example/contacts?q={NAME}",
                "x-totp-code": "123456",
            },
            "env": {"REMOTE_ADDR": "203.0.113.7"},
        },
        "user": {"id": ACCOUNT, "email": EMAIL, "ip_address": "203.0.113.7", "username": NAME},
        "tags": {"client": NAME},
        "extra": {"sys.argv": ["app.cli", "create-admin", EMAIL]},
        "contexts": {
            "trace": {"trace_id": "c7bc77b098514bb9a472c772a6ce91e3", "span_id": "999e"},
            "runtime": {"name": "CPython", "version": "3.12.10"},
            "client": {"name": NAME},
        },
        "breadcrumbs": {
            "values": [
                {
                    "type": "log",
                    "category": "app.api.contacts",
                    "level": "warning",
                    "message": "Contact [Filtered] saved",
                    "timestamp": "2026-11-24T17:33:16.263529Z",
                    "data": {"phone": PHONE},
                }
            ]
        },
        "threads": {"values": [{"stacktrace": {"frames": [_frame("run", 1)]}}]},
        "_meta": {"request": {"headers": {"cookie": {"": {"rem": [["!config", "s"]]}}}}},
    }


def test_a_report_leaves_without_clients_data_passwords_or_the_session():
    sent = json.dumps(scrub_event(_event(), {}), ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in sent


def test_a_report_still_says_where_it_failed():
    event = scrub_event(_event(), {})

    error = event["exception"]["values"][0]
    assert (error["type"], error["module"]) == ("DataError", "asyncpg.exceptions")
    assert "value" not in error
    assert error["mechanism"] == {"type": "chained", "handled": True, "exception_id": 1}
    frame = error["stacktrace"]["frames"][0]
    # The code around the failing line is the site's own source, not data.
    assert frame == {
        "filename": "app/api/contacts.py",
        "abs_path": "/srv/backend/app/api/contacts.py",
        "function": "update_contact",
        "module": "app.api.contacts",
        "lineno": 412,
        "in_app": True,
        "context_line": "    await session.flush()",
        "pre_context": ["    contact.phone = body.phone"],
        "post_context": ["    return contact"],
    }
    assert event["transaction"] == HANDLER
    assert event["transaction_info"] == {"source": "component"}
    assert event["request"] == {
        "method": "PATCH",
        "headers": {
            "user-agent": "Mozilla/5.0",
            "content-type": "application/json",
            "content-length": "84",
        },
    }
    for key in ("event_id", "timestamp", "level", "platform", "server_name", "release"):
        assert event[key] == _event()[key]
    assert event["environment"] == "production"
    assert event["contexts"] == {
        "trace": {"trace_id": "c7bc77b098514bb9a472c772a6ce91e3", "span_id": "999e"},
        "runtime": {"name": "CPython", "version": "3.12.10"},
    }
    # The log line's template, not the client's name it was filled with.
    assert event["logentry"] == {
        "message": "Contact %s not saved",
        "formatted": "Contact [Filtered] not saved",
    }


@pytest.mark.parametrize("missing", ["user", "tags", "extra", "threads", "_meta"])
def test_who_was_signed_in_and_free_form_extras_are_not_sent(missing: str):
    assert missing not in scrub_event(_event(), {})


@pytest.mark.parametrize(
    ("kind", "value", "kept"),
    [
        ("HTTPException", "storage_failed", True),  # the API's code (detail)
        ("HTTPException", "mail_unavailable", True),
        ("HTTPException", NAME, False),
        ("HTTPException", "Not Found Ivanova", False),
        ("HTTPException", f"storage_failed {PHONE}", False),
        # A code is a code only in the API's answer: another error's text may be anything.
        ("ValueError", "storage_failed", False),
        ("KeyError", "'phone'", False),
    ],
)
def test_only_an_api_error_code_is_kept_from_the_text_of_an_error(
    kind: str, value: str, kept: bool
):
    event = {
        "exception": {"values": [{"type": kind, "module": "fastapi.exceptions", "value": value}]}
    }
    error = scrub_event(event, {})["exception"]["values"][0]
    assert error.get("value") == (value if kept else None)


def test_every_error_of_a_chain_is_cleaned():
    event = {
        "exception": {
            "values": [
                {"type": "DataError", "value": PHONE, "stacktrace": {"frames": [_frame("a", 1)]}},
                {"type": "DBAPIError", "value": NAME, "stacktrace": {"frames": [_frame("b", 2)]}},
            ]
        }
    }
    sent = scrub_event(event, {})
    assert [e["type"] for e in sent["exception"]["values"]] == ["DataError", "DBAPIError"]
    assert NAME not in json.dumps(sent) and PHONE not in json.dumps(sent)


@pytest.mark.parametrize(
    "transaction",
    [f"/api/contacts/{ACCOUNT}", f"/contacts?q={NAME}", "generic ASGI request", NAME],
)
def test_a_transaction_is_a_handler_name_never_a_path(transaction: str):
    event = {"transaction": transaction, "transaction_info": {"source": "url"}}
    sent = scrub_event(event, {})
    assert "transaction" not in sent
    assert "transaction_info" not in sent


ID = uuid.UUID(ACCOUNT)


@pytest.mark.parametrize(
    ("param", "shown"),
    [
        (ID, ACCOUNT),  # ids are what the log is allowed to carry
        (ACCOUNT, ACCOUNT),
        (412, "412"),
        (True, "True"),
        (None, "None"),
        ("DBAPIError", "DBAPIError"),  # the kind of failure
        ("IntegrityError", "IntegrityError"),
        ("SMTPException", "SMTPException"),
        (NAME, "[Filtered]"),
        ("Ivanova", "[Filtered]"),
        (PHONE, "[Filtered]"),
        (EMAIL, "[Filtered]"),
        (PASSPORT, "[Filtered]"),
        ("storage_failed", "[Filtered]"),
        ("IvanovaError Olga", "[Filtered]"),
        (Decimal("1250.00"), "[Filtered]"),  # a client's payment
        (date(1990, 5, 17), "[Filtered]"),  # a date of birth
        (datetime(2026, 11, 24, tzinfo=UTC), "[Filtered]"),
        ({"name": NAME}, "[Filtered]"),
        (b"bytes", "[Filtered]"),
    ],
)
def test_a_log_line_keeps_ids_numbers_and_kinds_of_failure_only(param: object, shown: str):
    record = logging.LogRecord("app.x", logging.ERROR, "x.py", 1, "Failed: %s", (param,), None)
    crumb = scrub_breadcrumb(
        {"type": "log", "message": record.getMessage()}, {"log_record": record}
    )
    assert crumb["message"] == f"Failed: {shown}"


def test_a_log_line_with_named_values_is_cleaned_too():
    args = {"who": NAME, "id": ID}
    record = logging.LogRecord(
        "app.x", logging.WARNING, "x.py", 1, "%(who)s of %(id)s", (args,), None
    )
    crumb = scrub_breadcrumb(
        {"type": "log", "message": record.getMessage()}, {"log_record": record}
    )
    assert crumb["message"] == f"[Filtered] of {ACCOUNT}"


def test_another_librarys_log_line_is_cleaned_the_same_way():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "x.py",
        1,
        '%s - "%s %s" %d',
        ("203.0.113.7", "GET", f"/?q={NAME}", 200),
        None,
    )
    crumb = scrub_breadcrumb(
        {"type": "log", "message": record.getMessage()}, {"log_record": record}
    )
    assert crumb["message"] == '[Filtered] - "[Filtered] [Filtered]" 200'


def test_a_log_line_that_does_not_format_without_its_values_keeps_the_template():
    record = logging.LogRecord(
        "app.x", logging.ERROR, "x.py", 1, "Paid %d by %s", (Decimal("1250"), NAME), None
    )
    crumb = scrub_breadcrumb(
        {"type": "log", "message": record.getMessage()}, {"log_record": record}
    )
    # «%d» of a filtered value does not format: the template stands alone.
    assert crumb["message"] == "Paid %d by %s"


def test_a_log_breadcrumb_keeps_its_place_and_level_not_its_extras():
    record = logging.LogRecord(
        "app.api.contacts", logging.WARNING, "x.py", 1, "Contact %s", (NAME,), None
    )
    crumb = {
        "type": "log",
        "level": "warning",
        "category": "app.api.contacts",
        "message": record.getMessage(),
        "timestamp": "2026-11-24T17:33:16Z",
        "data": {"phone": PHONE},
    }
    assert scrub_breadcrumb(crumb, {"log_record": record}) == {
        "type": "log",
        "level": "warning",
        "category": "app.api.contacts",
        "message": "Contact [Filtered]",
        "timestamp": "2026-11-24T17:33:16Z",
    }


def test_an_outgoing_request_keeps_the_host_method_and_status_only():
    crumb = {
        "type": "http",
        "category": "httplib",
        "timestamp": "2026-11-24T17:33:16Z",
        "data": {
            "url": f"https://s3.eu-central-1.example.com/bucket/{ACCOUNT}/{NAME}.pdf",
            "method": "PUT",
            "status_code": 200,
            "reason": "OK",
            "http.query": f"X-Amz-Credential={PASSWORD}",
            "http.fragment": "",
        },
    }
    assert scrub_breadcrumb(crumb, {}) == {
        "type": "http",
        "category": "httplib",
        "timestamp": "2026-11-24T17:33:16Z",
        "data": {"url": "https://s3.eu-central-1.example.com", "method": "PUT", "status_code": 200},
    }


def test_any_other_breadcrumb_keeps_no_message_or_data():
    crumb = {
        "type": "subprocess",
        "category": "subprocess",
        "message": f"pg_dump --password={PASSWORD}",
        "data": {"args": [PASSWORD]},
    }
    assert scrub_breadcrumb(crumb, {}) == {"type": "subprocess", "category": "subprocess"}


def test_breadcrumbs_inside_a_report_keep_only_what_scrub_breadcrumb_left():
    event = _event()
    sent = scrub_event(event, {})
    assert sent["breadcrumbs"] == {
        "values": [
            {
                "type": "log",
                "category": "app.api.contacts",
                "level": "warning",
                "message": "Contact [Filtered] saved",
                "timestamp": "2026-11-24T17:33:16.263529Z",
            }
        ]
    }


def test_another_librarys_line_without_values_is_not_sent_as_it_is():
    """A library may log a line it formatted itself: without values there is no template to
    tell the text from the data. The site's own lines are written with values apart
    (ruff G)."""
    theirs = logging.LogRecord(
        "botocore.endpoint", logging.WARNING, "x.py", 1, f"Retrying {NAME}.pdf", (), None
    )
    ours = logging.LogRecord(
        "app.core.rates", logging.WARNING, "x.py", 1, "Refreshing the rates failed", (), None
    )
    crumb = {"type": "log", "category": theirs.name, "message": theirs.getMessage()}
    assert scrub_breadcrumb(crumb, {"log_record": theirs})["message"] == "[Filtered]"
    crumb = {"type": "log", "category": ours.name, "message": ours.getMessage()}
    assert scrub_breadcrumb(crumb, {"log_record": ours})["message"] == "Refreshing the rates failed"

    event = {"logger": theirs.name, "logentry": {"message": theirs.msg, "params": ()}}
    assert scrub_event(event, {})["logentry"] == {
        "message": "[Filtered]",
        "formatted": "[Filtered]",
    }

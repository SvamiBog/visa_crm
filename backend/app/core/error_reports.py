"""Error reports to Sentry (roadmap: stage 1 → «Инфраструктура», 3.7): the owner learns of
a failure in production without clients' data, passwords or keys leaving the server.

Nothing starts without SENTRY_DSN. A report is cut down to a list of what may leave
(docs/decisions.md): where it failed (the error's type, the frames with their file, line,
function and source line, the handler's name), the API's error code, the release and the
environment, the browser's user-agent. Everything else, whatever an integration adds today
or in a later SDK version, is not sent: the text of an error, local variables, the URL and
its query, cookies, the request's body and other headers, who was signed in, extras and
tags. A log line keeps its template and only the values that are ids, numbers or the kind
of a failure.

The SDK's own switches are set the same way (data_collection without the user, cookies, the
query, bodies or local variables; no automatic integrations, no performance traces), so the
scrubber is the second lock, not the only one.
"""

import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.transport import Transport

from app.core.config import Settings

Event = dict[str, Any]

FILTERED = "[Filtered]"
# Keys of an event that leave as they are: the SDK's and the server's facts, none from the
# request. «modules» lists the installed packages and their versions.
KEPT_AS_IS = (
    "event_id",
    "timestamp",
    "level",
    "platform",
    "logger",
    "server_name",
    "release",
    "environment",
    "dist",
    "sdk",
    "modules",
    "fingerprint",
    "type",
)
# What a browser says about itself and the size of what it sent; no cookie, no address, no
# referer (it may carry a page with a search), no authorization.
KEPT_HEADERS = frozenset({"user-agent", "content-type", "content-length"})
KEPT_FRAME = (
    "filename",
    "abs_path",
    "function",
    "module",
    "lineno",
    "colno",
    "in_app",
    # The site's own source around the line, read from the server's files.
    "context_line",
    "pre_context",
    "post_context",
)
KEPT_MECHANISM = (
    "type",
    "handled",
    "synthetic",
    "exception_id",
    "parent_id",
    "source",
    "is_exception_group",
)
KEPT_CONTEXTS = {"runtime": ("name", "version"), "trace": ("trace_id", "span_id")}
KEPT_CRUMB = ("type", "category", "level", "timestamp")
KEPT_HTTP_CRUMB = ("method", "status_code")
# The API answers with a code (CLAUDE.md: «Ошибки API — коды в detail»); the detail of an
# HTTPException is that code, the text of any other error may quote a value.
API_CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
CODED_ERRORS = frozenset({"HTTPException"})
# A handler's dotted name (transaction_style="endpoint"), never a path with ids or a query.
HANDLER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_<>][A-Za-z0-9_<>]*)+")
# The kind of a failure that a log line names: type(error).__name__.
ERROR_KIND = re.compile(r"[A-Z][A-Za-z0-9]*(?:Error|Exception)")
UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
# The log twin of an error that is reported as an exception (app/core/server_errors.py).
REPORTED_AS_EXCEPTIONS = ("app.core.server_errors",)


def _kept(source: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source}


def _log_value(value: object) -> object:
    """An id, a number or the kind of a failure stays; anything else may be a client's."""
    if value is None or isinstance(value, bool | int | float | UUID):
        return value
    if isinstance(value, str) and (UUID_TEXT.fullmatch(value) or ERROR_KIND.fullmatch(value)):
        return value
    return FILTERED


def _own(logger: object) -> bool:
    return isinstance(logger, str) and (logger == "app" or logger.startswith("app."))


def _log_text(template: object, args: object, logger: object) -> str:
    """The line as the log would print it, with only the values _log_value keeps. A line
    that does not format without its values (a «%d» of a filtered one) stays a template.
    A line without values is the site's own text only in the site's own loggers (ruff G
    keeps their values apart); another library may have formatted it with data in it."""
    text = str(template)
    if not args:
        return text if _own(logger) else FILTERED
    if isinstance(args, Mapping) and "%(" in text:
        # Named values: the names are the template's, the values are checked.
        values: object = {key: _log_value(value) for key, value in args.items()}
    elif isinstance(args, tuple | list):
        values = tuple(_log_value(value) for value in args)
    else:
        values = (_log_value(args),)
    try:
        return text % values
    except (TypeError, ValueError, KeyError):
        return text


def _logentry(entry: Mapping[str, Any], logger: object) -> dict[str, str]:
    template = entry.get("message")
    if template is None:
        return {}
    params = entry.get("params")
    if isinstance(params, list | tuple) and len(params) == 1 and isinstance(params[0], Mapping):
        params = params[0]
    formatted = _log_text(template, params, logger)
    # Without values the template is the text itself.
    return {"message": str(template) if params else formatted, "formatted": formatted}


def _frames(stacktrace: Mapping[str, Any]) -> dict[str, Any]:
    return {"frames": [_kept(frame, KEPT_FRAME) for frame in stacktrace.get("frames") or ()]}


def _exception(error: Mapping[str, Any]) -> dict[str, Any]:
    kept = _kept(error, ("type", "module"))
    value = error.get("value")
    if error.get("type") in CODED_ERRORS and isinstance(value, str) and API_CODE.fullmatch(value):
        kept["value"] = value
    if isinstance(mechanism := error.get("mechanism"), Mapping):
        kept["mechanism"] = _kept(mechanism, KEPT_MECHANISM)
    if isinstance(stacktrace := error.get("stacktrace"), Mapping):
        kept["stacktrace"] = _frames(stacktrace)
    return kept


def _request(request: Mapping[str, Any]) -> dict[str, Any]:
    kept = _kept(request, ("method",))
    headers = {
        name: value
        for name, value in (request.get("headers") or {}).items()
        if name.lower() in KEPT_HEADERS
    }
    if headers:
        kept["headers"] = headers
    return kept


def _contexts(contexts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: _kept(contexts[name], keys)
        for name, keys in KEPT_CONTEXTS.items()
        if isinstance(contexts.get(name), Mapping)
    }


def _crumb(crumb: Mapping[str, Any], message: str | None) -> dict[str, Any]:
    kept = _kept(crumb, KEPT_CRUMB)
    if message is not None:
        kept["message"] = message
    data = crumb.get("data")
    if crumb.get("type") == "http" and isinstance(data, Mapping):
        http = _kept(data, KEPT_HTTP_CRUMB)
        # The host only: a path names stored files, a query may carry keys.
        if isinstance(url := data.get("url"), str) and (parts := urlsplit(url)).hostname:
            http = {"url": f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}", **http}
        if http:
            kept["data"] = http
    return kept


def scrub_breadcrumb(crumb: Event, hint: dict[str, Any]) -> Event:
    """before_breadcrumb: a log line becomes its template with ids and numbers only, an
    outgoing request its host, method and status; any other breadcrumb keeps its kind."""
    record = (hint or {}).get("log_record")
    if isinstance(record, logging.LogRecord):
        return _crumb(crumb, _log_text(record.msg, record.args, record.name))
    return _crumb(crumb, None)


def scrub_event(event: Event, hint: dict[str, Any]) -> Event:
    """before_send: the report as it leaves for Sentry, built from what may leave."""
    kept = _kept(event, KEPT_AS_IS)
    if isinstance(exception := event.get("exception"), Mapping):
        kept["exception"] = {
            "values": [_exception(error) for error in exception.get("values") or ()]
        }
    entry = event.get("logentry")
    if isinstance(entry, Mapping) and (logentry := _logentry(entry, event.get("logger"))):
        kept["logentry"] = logentry
    if isinstance(transaction := event.get("transaction"), str) and HANDLER_NAME.fullmatch(
        transaction
    ):
        kept["transaction"] = transaction
        if isinstance(info := event.get("transaction_info"), Mapping):
            kept["transaction_info"] = _kept(info, ("source",))
    if isinstance(request := event.get("request"), Mapping):
        kept["request"] = _request(request)
    if isinstance(contexts := event.get("contexts"), Mapping):
        kept["contexts"] = _contexts(contexts)
    if isinstance(breadcrumbs := event.get("breadcrumbs"), Mapping):
        # Each one went through scrub_breadcrumb when it was recorded; its message is
        # already cut down, the rest is cut again here.
        kept["breadcrumbs"] = {
            "values": [
                _crumb(crumb, crumb.get("message") if crumb.get("type") == "log" else None)
                for crumb in breadcrumbs.get("values") or ()
            ]
        }
    return kept


def options(settings: Settings) -> dict[str, Any]:
    """The SDK's settings: errors only, cut down by the scrubbers."""
    return {
        "dsn": settings.sentry_dsn,
        "environment": settings.env,
        # Unset outside Railway: the SDK then finds none in a container and sends none.
        "release": settings.railway_git_commit_sha or None,
        # What the integrations collect at all (the SDK's replacement for the deprecated
        # send_default_pii): no user, cookies, query, bodies, queries' values or local
        # variables, and only the headers the scrubber keeps.
        "data_collection": {
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "allowlist", "terms": sorted(KEPT_HEADERS)}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "database_query_data": False,
            "queues": False,
            "stack_frame_variables": False,
            "gen_ai": {"inputs": False, "outputs": False},
        },
        "max_request_body_size": "never",
        # The bank and the file storage get no sentry-trace or baggage headers: the trace
        # id, environment and release are Sentry's business only.
        "trace_propagation_targets": [],
        # No release health sessions and no performance traces (traces_sample_rate unset):
        # a trace would carry every request's queries and timings (docs/decisions.md).
        "auto_session_tracking": False,
        # Only the integrations named here: none records queries or outgoing calls.
        "auto_enabling_integrations": False,
        "integrations": [
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            # Breadcrumbs from warnings up; an error line of the log is a report.
            LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
        ],
        "in_app_include": ["app"],
        "before_send": scrub_event,
        "before_breadcrumb": scrub_breadcrumb,
    }


def start_error_reports(settings: Settings, transport: Transport | None = None) -> bool:
    """Start the SDK when SENTRY_DSN is set; without it nothing is patched or sent. The
    tests pass a transport that collects the reports instead of sending them."""
    if not settings.sentry_dsn:
        return False
    extra = {"transport": transport} if transport is not None else {}
    sentry_sdk.init(**options(settings), **extra)
    for name in REPORTED_AS_EXCEPTIONS:
        ignore_logger(name)
    return True


def report(error: BaseException) -> None:
    """Report an error the site handled itself (a 500 it answered): the SDK sees only those
    that leave the app. Nothing happens while the SDK is not started."""
    sentry_sdk.capture_exception(error)


class ErrorReportCheck(Exception):
    """The test report of `python -m app.cli check-error-reports` (docs/operations.md)."""


def send_test_report(settings: Settings, transport: Transport | None = None) -> str | None:
    """Send one report from the server this runs on: SENTRY_DSN is set there and Sentry is
    reachable from it. None without a DSN."""
    if not start_error_reports(settings, transport):
        return None
    try:
        raise ErrorReportCheck
    except ErrorReportCheck as error:
        event_id = sentry_sdk.capture_exception(error)
    sentry_sdk.flush(timeout=10)
    return event_id

"""The client's address for the audit log and the login limit (backlog S17 and S22, audit
N1, N2): the X-Real-IP that Railway's edge writes, and only a real address."""

import pytest
from starlette.datastructures import Headers

from app.core.audit import client_address

PROXY = "100.64.0.9"  # Railway's proxy, as the app's connection shows it
NODE = "192.0.2.33"  # a Railway node, which adds itself last to X-Forwarded-For
REAL = "198.51.100.20"
FAKE = "203.0.113.7"


def headers(*lines: tuple[str, str]) -> Headers:
    return Headers(raw=[(name.encode(), value.encode()) for name, value in lines])


def test_the_client_is_the_address_railway_puts_in_x_real_ip():
    # What reaches the app on Railway: the client first in X-Forwarded-For, a node of
    # Railway's last (seen on staging, 24.11.2026), and the client in X-Real-IP.
    assert (
        client_address(headers(("x-real-ip", REAL), ("x-forwarded-for", f"{REAL}, {NODE}")), PROXY)
        == REAL
    )


@pytest.mark.parametrize(
    "forwarded",
    [f"{FAKE}, {REAL}, {NODE}", f"{REAL}, {FAKE}, {NODE}", FAKE, f"{NODE}, {FAKE}"],
)
def test_x_forwarded_for_does_not_change_the_address(forwarded: str):
    # Whatever ends up in X-Forwarded-For, in front of the node or instead of it.
    assert (
        client_address(headers(("x-forwarded-for", forwarded), ("x-real-ip", REAL)), PROXY) == REAL
    )


def test_x_forwarded_for_alone_is_not_taken():
    # Railway's edge always writes X-Real-IP; a request without it did not come through the
    # edge, and its X-Forwarded-For is anybody's.
    assert client_address(headers(("x-forwarded-for", f"{REAL}, {NODE}")), PROXY) == PROXY


def test_without_the_header_the_connection_is_the_client():
    # A developer's machine has no proxy in front.
    assert client_address(headers(), "127.0.0.1") == "127.0.0.1"
    assert client_address(headers(), None) is None


@pytest.mark.parametrize(
    "real_ip",
    [
        "a" * 46,  # longer than audit_events.ip
        "not-an-address",
        "",
        "999.1.2.3",
        f"{REAL}:51234",
        f"{REAL}, {FAKE}",
        f"{REAL},",
        "fe80::1%" + "a" * 40,  # an address, but 48 characters with its zone
        "1" * 10_000,
    ],
)
def test_a_value_that_is_not_an_address_gives_none(real_ip: str):
    # Neither the connection nor an entry of X-Forwarded-For is taken in its place.
    found = headers(("x-real-ip", real_ip), ("x-forwarded-for", f"{REAL}, {NODE}"))
    assert client_address(found, PROXY) is None


def test_two_x_real_ip_lines_give_none():
    # One of them is not Railway's, and nothing tells which.
    assert client_address(headers(("x-real-ip", FAKE), ("x-real-ip", REAL)), PROXY) is None
    assert client_address(headers(("x-real-ip", REAL), ("x-real-ip", REAL)), PROXY) is None


def test_a_connection_that_is_not_an_address_gives_none():
    assert client_address(headers(), "testclient") is None


def test_addresses_are_written_one_way():
    # One client, one entry in the login limit, however its address is spelled.
    assert client_address(headers(("x-real-ip", "2001:DB8:0:0:0:0:0:1")), PROXY) == "2001:db8::1"
    assert client_address(headers(("x-real-ip", f" {REAL} ")), PROXY) == REAL
    assert client_address(headers(), "0:0:0:0:0:0:0:1") == "::1"
    longest = "ffff:ffff:ffff:ffff:ffff:ffff:255.255.255.255"
    assert len(longest) == 45
    assert (
        client_address(headers(("x-real-ip", longest)), PROXY)
        == "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
    )

"""The client's address for the audit log and the login limit (backlog S17, audit N1, N2):
the last X-Forwarded-For entry, which Railway's proxy wrote, and only a real address."""

import pytest

from app.core.audit import client_address

PROXY = "100.64.0.9"  # Railway's proxy, as the connection shows it
REAL = "198.51.100.20"
FAKE = "203.0.113.7"


def test_the_last_forwarded_entry_is_the_client():
    # Whatever the client wrote comes first; the proxy adds the address it was called from.
    assert client_address([f"{FAKE}, {REAL}"], PROXY) == REAL
    assert client_address([f"{FAKE},{FAKE} ,  {REAL} "], PROXY) == REAL


def test_header_lines_count_in_order():
    # Several X-Forwarded-For lines are one list (RFC 9110, 5.3): the last line ends it.
    assert client_address([FAKE, REAL], PROXY) == REAL


def test_a_single_forwarded_address_is_the_client():
    assert client_address([REAL], PROXY) == REAL


def test_without_the_header_the_connection_is_the_client():
    # A developer's machine has no proxy in front.
    assert client_address([], "127.0.0.1") == "127.0.0.1"
    assert client_address([], None) is None


@pytest.mark.parametrize(
    "forwarded",
    [
        "a" * 46,  # longer than audit_events.ip
        "not-an-address",
        "",
        "999.1.2.3",
        f"{REAL}:51234",
        f"{REAL}, ",
        # An address the client wrote earlier is not taken in place of a bad last entry.
        f"{REAL}, nonsense",
        "fe80::1%" + "a" * 40,  # an address, but 48 characters with its zone
        "1" * 10_000,
    ],
)
def test_a_value_that_is_not_an_address_gives_none(forwarded: str):
    assert client_address([forwarded], PROXY) is None


def test_a_connection_that_is_not_an_address_gives_none():
    assert client_address([], "testclient") is None


def test_addresses_are_written_one_way():
    # One client, one entry in the login limit, however its address is spelled.
    assert client_address(["2001:DB8:0:0:0:0:0:1"], PROXY) == "2001:db8::1"
    assert client_address([], "0:0:0:0:0:0:0:1") == "::1"
    longest = "ffff:ffff:ffff:ffff:ffff:ffff:255.255.255.255"
    assert len(longest) == 45
    assert client_address([longest], PROXY) == "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"

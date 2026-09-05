"""Where a name resolves is checked, and the connection goes where it checked.

#969, found by the ai-jury panel on #958. `config.endpoint_issues` classifies
the host *as written*, so a hostname carries no address and both the metadata
and the private checks answer "no". With the remote opt-in set and the internal
one unset:

    refused   http://10.0.0.5/                       literal RFC1918
    allowed   http://<name resolving to 10.0.0.5>/   the same target

which is precisely the boundary the refusal text draws — *"permits reaching
out, not reaching in"*.

The half that is easy to get wrong is not the check, it is the pinning. A guard
that resolves, approves, and then hands the *name* to `connect` re-resolves, and
a name that answers a public address to the check and a private one to the
connection passes both. So the resolver-call count is asserted, not just the
outcome: a test that only checks the refusal would pass against a guard with no
rebinding protection at all.
"""

from __future__ import annotations

import socket
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from keel.api_delegate import GuardedAddressError, build_http_only_opener

REMOTE = {"KEEL_ALLOW_REMOTE_ENDPOINT": "1"}
INTERNAL = {"KEEL_ALLOW_REMOTE_ENDPOINT": "1", "KEEL_ALLOW_INTERNAL_ENDPOINT": "1"}

#: The host the pinning fixture below hands to an *unpinned* ``connect``.
FIXTURE_HOST = "unresolvable.invalid"


def one(ip: str):
    """A resolver returning a single address, whatever it is asked."""

    def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return resolve


def counting(*ips: str):
    """A resolver answering a different address on each call, and counting them."""
    calls = {"n": 0}

    def resolve(host, port, **kwargs):
        ip = ips[min(calls["n"], len(ips) - 1)]
        calls["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return resolve, calls


class OfflineResolver:
    """Stands in for ``socket.getaddrinfo``: :data:`FIXTURE_HOST` never resolves.

    RFC 2606 reserves ``.invalid`` so that it cannot resolve, and resolvers
    ignore that: home routers, captive portals, search-domain suffixing and
    ISPs with wildcard NXDOMAIN redirection all hand back an address for it.
    Asking the machine's own resolver therefore made this fixture depend on
    which network the suite ran on, and `make test` is the **offline** suite
    (#1079) — so the fixture host fails here, deterministically, without a
    query leaving the process.

    Every other host is delegated to the real ``getaddrinfo``, which the guard
    only ever asks about the IP literal it pinned — parsing a literal is not a
    lookup, so the delegation stays offline too.
    """

    def __init__(self):
        self.hosts: list[str] = []
        self._real = socket.getaddrinfo

    def __call__(self, host, port, *args, **kwargs):
        self.hosts.append(host)
        if host == FIXTURE_HOST:
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")
        return self._real(host, port, *args, **kwargs)


class RecordingConnector:
    """Stands in for ``socket.create_connection``: records, never connects.

    The guard's pinned connect is ``socket.create_connection((literal, port))``
    looked up in the ``socket`` module namespace, so patching it there is the
    narrowest seam that still sees the address the guard chose.

    Standing in for it is what makes the *allowed* cases offline. Reaching a
    real ``connect`` was how they used to end — a public literal such as
    ``93.184.216.34`` got an outbound SYN on every run and the suite waited out
    its timeout, and the failure was then read as "not a policy refusal"
    (#1084). Refusing here instead costs nothing and says more: the address is
    recorded, so the claim becomes *the connection goes where the check looked*
    rather than *it failed for some other reason*.
    """

    def __init__(self):
        self.addresses: list[tuple] = []

    def __call__(self, address, *args, **kwargs):
        self.addresses.append(tuple(address))
        raise ConnectionRefusedError("the offline suite opens no sockets")


def attempt(env, resolve, url="http://name.example/"):
    """Open ``url`` through the guard without letting a socket out.

    Returns ``(error, addresses)``: the GuardedAddressError the guard raised or
    None, and every address it handed to :class:`RecordingConnector`. A refusal
    never reaches the connector, so its ``addresses`` is empty; an allowed host
    reaches it exactly once, with the literal that was checked.
    """
    connector = RecordingConnector()
    with patch.object(socket, "create_connection", connector):
        opener = build_http_only_opener(_env=env, _resolve=resolve)
        try:
            opener.open(urllib.request.Request(url), timeout=1)
            error = None  # pragma: no cover - the connector always raises
        except urllib.error.URLError as exc:
            error = exc.reason if isinstance(exc.reason, GuardedAddressError) else None
        except OSError as exc:  # pragma: no cover - direct raise path
            error = exc if isinstance(exc, GuardedAddressError) else None
    return error, connector.addresses


def refusal(env, resolve, url="http://name.example/"):
    """The GuardedAddressError raised opening ``url``, or None if none was."""
    return attempt(env, resolve, url)[0]


class AHostnameCannotReachWhatItsLiteralCannot(unittest.TestCase):
    def test_a_name_resolving_into_rfc1918_is_refused(self):
        error = refusal(REMOTE, one("10.0.0.5"))
        self.assertIsNotNone(error, "a name resolving to 10.0.0.5 was connected to")
        self.assertIn("10.0.0.5", str(error))
        self.assertIn("reaching out, not reaching in", str(error))

    def test_a_name_resolving_to_the_metadata_service_is_refused(self):
        error = refusal(REMOTE, one("169.254.169.254"))
        self.assertIsNotNone(error)
        self.assertIn("link-local", str(error))

    def test_link_local_is_refused_even_with_the_internal_opt_in(self):
        """The internal opt-in is for your own network, not for the metadata service."""
        error = refusal(INTERNAL, one("169.254.169.254"))
        self.assertIsNotNone(error, "the internal opt-in must not unlock link-local")

    def test_the_internal_opt_in_permits_your_own_network(self):
        """Otherwise the guard would refuse the case the opt-in exists for."""
        error, addresses = attempt(INTERNAL, one("10.0.0.5"))
        self.assertIsNone(error)
        self.assertEqual(addresses, [("10.0.0.5", 80)])

    def test_a_literal_loopback_endpoint_still_connects(self):
        """`http://localhost:11434` is the default-allowed local model server.

        A guard that refused loopback outright would pass every test above and
        break the most common configuration keel has.
        """
        error, addresses = attempt(REMOTE, one("127.0.0.1"), url="http://localhost/")
        self.assertIsNone(error)
        self.assertEqual(addresses, [("127.0.0.1", 80)])

    def test_a_public_address_is_not_refused(self):
        error, addresses = attempt(REMOTE, one("93.184.216.34"))
        self.assertIsNone(error)
        self.assertEqual(addresses, [("93.184.216.34", 80)])


class TheConnectionGoesWhereTheCheckLooked(unittest.TestCase):
    def setUp(self):
        """Both tests below run against the same stand-in for the OS resolver.

        `socket.create_connection` — what an unpinned guard would reach for —
        looks `getaddrinfo` up in the `socket` module namespace, so patching it
        there is what puts this resolver on the path a rebinding connection
        would take.
        """
        self.resolver = OfflineResolver()
        patcher = patch.object(socket, "getaddrinfo", self.resolver)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_socket_goes_to_the_checked_address(self):
        """The rebinding case, asserted on where the socket actually went.

        A resolver call *count* is the obvious assertion and it is the wrong
        one: drop the pinning and `connect((host, port))` re-resolves through
        the **OS** resolver, which a fake never sees, so the count stays at one
        and the test passes against no protection at all. That mutation did
        exactly that here before this was rewritten.

        So the fixture uses a host name the stand-in resolver refuses and an
        injected resolver that answers with a real listening socket. Pinned, the
        connection reaches the listener and the resolver is never consulted about
        that name. Unpinned, `connect` is handed the unresolvable name and fails
        with `gaierror` — which is the signal this asserts against.
        """
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def resolve(host, _port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        opener = build_http_only_opener(_env=INTERNAL, _resolve=resolve)
        try:
            opener.open(urllib.request.Request(f"http://{FIXTURE_HOST}:{port}/"), timeout=0.3)
            reason: BaseException | None = None  # pragma: no cover - silent listener
        except urllib.error.URLError as exc:
            reason = exc.reason if isinstance(exc.reason, BaseException) else exc
        except OSError as exc:
            # The listener accepts and says nothing, so the *read* times out.
            # That is the proof: a socket cannot time out reading from a host
            # it never reached.
            reason = exc

        self.assertNotIsInstance(
            reason,
            socket.gaierror,
            "the connection resolved the host name itself, so the address that "
            "was checked is not the address it went to",
        )
        self.assertNotIn(
            FIXTURE_HOST,
            self.resolver.hosts,
            "the pinned connection still asked the resolver about the name",
        )

    def test_the_fixture_host_really_does_not_resolve(self):
        """Vacuity: the test above only means something if resolution would fail.

        Asked of the machine's own resolver this was not offline and not even
        reliably true — `.invalid` is reserved by RFC 2606 and resolvers answer
        for it anyway (#1079). Asked of the stand-in the test above runs
        against, it is both: `socket.create_connection` is exactly what an
        unpinned guard falls through to, and handing it the fixture host raises
        `gaierror` through the patched resolver — proved by the call this
        records, so the vacuity check cannot itself go vacuous by being patched
        out of the path.
        """
        with self.assertRaises(socket.gaierror):
            socket.create_connection((FIXTURE_HOST, 80), timeout=0.3)
        self.assertEqual(
            self.resolver.hosts,
            [FIXTURE_HOST],
            "the stand-in resolver is not on the path connect takes",
        )


class HttpsIsGuardedToo(unittest.TestCase):
    """The vendor endpoints are https, so guarding only http would guard nothing.

    Two handlers means two connection classes, and wiring one and forgetting the
    other is the obvious way to half-ship this.
    """

    def test_an_https_name_resolving_into_rfc1918_is_refused(self):
        error = refusal(REMOTE, one("10.0.0.5"), url="https://name.example/")
        self.assertIsNotNone(error, "https reached 10.0.0.5")
        self.assertIn("10.0.0.5", str(error))

    def test_an_https_name_resolving_to_the_metadata_service_is_refused(self):
        error = refusal(REMOTE, one("169.254.169.254"), url="https://name.example/")
        self.assertIsNotNone(error)
        self.assertIn("link-local", str(error))


class AnUnresolvableNameIsNotAPolicyRefusal(unittest.TestCase):
    def test_resolution_failure_says_so(self):
        """Reporting a typo as a security block sends the reader hunting."""

        def resolve(host, port, **kwargs):
            raise socket.gaierror(8, "nodename nor servname provided")

        error = refusal(REMOTE, resolve)
        self.assertIsNotNone(error)
        self.assertIn("cannot resolve", str(error))
        self.assertNotIn("reaching in", str(error))

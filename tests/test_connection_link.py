"""SolarmaxLink transport tests — real sockets against the emulator."""

import asyncio
import socket
import time
from unittest.mock import patch

import pytest

from custom_components.solarmax.connection import LinkClosed, LinkTimeout, SolarmaxLink
from custom_components.solarmax.protocol import build_request


async def test_persistent_connection_reused(emulator):
    link = SolarmaxLink(*emulator.addr)
    try:
        for _ in range(3):
            response = await link.request(build_request(1, ["PAC"]))
            assert "PAC=" in response
        assert link.attempts == 1  # one TCP connect served all three
    finally:
        await link.close()


async def test_peer_fin_reconnects_transparently(emulator):
    """Idle FIN between polls is routine, not an error (measured behaviour)."""
    emulator.idle_timeout = 0.5
    link = SolarmaxLink(*emulator.addr)
    try:
        await link.request(build_request(1, ["PAC"]))
        await asyncio.sleep(0.9)  # emulator FINs us
        response = await link.request(build_request(1, ["PAC"]))
        assert "PAC=" in response
        assert link.reconnects == 1
    finally:
        await link.close()


async def test_dark_device_times_out_within_budget(emulator):
    """Dark = swallowed requests; must raise LinkTimeout inside response_timeout."""
    link = SolarmaxLink(*emulator.addr, response_timeout=0.5)
    try:
        await link.request(build_request(1, ["PAC"]))
        emulator.begin_dusk(announce_seconds=0)
        await asyncio.sleep(0.3)
        start = time.monotonic()
        with pytest.raises(LinkTimeout):
            await link.request(build_request(1, ["PAC"]))
        assert time.monotonic() - start < 2.0
    finally:
        await link.close()


async def test_cancellation_mid_request_aborts_transport_no_stale_frame(emulator):
    """Cancelling a request mid-flight must abort synchronously, leaving the
    link deterministically closed rather than a live, desynchronised stream
    that would hand the NEXT request a leftover frame."""
    link = SolarmaxLink(*emulator.addr)
    try:
        first = await link.request(build_request(1, ["PAC"]))
        assert "PAC=" in first

        emulator.begin_dusk(announce_seconds=0)
        await asyncio.sleep(0.3)  # emulator now swallows requests silently

        # An outer asyncio.timeout shorter than response_timeout cancels the
        # in-flight request(); asyncio.timeout converts that CancelledError
        # to TimeoutError on its own __aexit__ once it reaches the caller.
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await link.request(build_request(1, ["PAC"]))
        assert link.connected is False

        emulator.wake()
        response = await link.request(build_request(1, ["PAC"]))
        assert "PAC=" in response
    finally:
        await link.close()


async def test_setsockopt_failure_after_connect_raises_link_closed(emulator):
    """A live-but-freshly-opened socket that fails setsockopt must be
    reachable by `close`/abort — never orphaned — and never leak a raw
    OSError. Targets SO_KEEPALIVE specifically: asyncio's own
    open_connection already sets TCP_NODELAY internally, so mocking that
    option would also trip on asyncio's own call before ours ever runs."""
    from custom_components.solarmax.connection import LinkClosed

    real_setsockopt = socket.socket.setsockopt

    def _boom(self, *args):
        if args[:2] == (socket.SOL_SOCKET, socket.SO_KEEPALIVE):
            raise OSError("simulated setsockopt failure")
        return real_setsockopt(self, *args)

    link = SolarmaxLink(*emulator.addr)
    try:
        with patch.object(socket.socket, "setsockopt", _boom):
            with pytest.raises(LinkClosed):
                await link.request(build_request(1, ["PAC"]))
        assert link.connected is False

        # The live socket must have been torn down, not orphaned: a fresh
        # link must be able to connect and get a real answer right after.
        response = await link.request(build_request(1, ["PAC"]))
        assert "PAC=" in response
    finally:
        await link.close()


async def test_refused_connection_raises_link_closed(emulator):
    """Connect-side OSErrors must surface as LinkClosed, never escape raw."""
    import pytest as _pytest

    from custom_components.solarmax.connection import LinkClosed

    dead_port_link = SolarmaxLink("127.0.0.1", 1)  # nothing listens on port 1
    with _pytest.raises(LinkClosed):
        await dead_port_link.request(build_request(1, ["PAC"]))
    await dead_port_link.close()


async def test_close_is_idempotent_and_deterministic(emulator):
    link = SolarmaxLink(*emulator.addr)
    await link.request(build_request(1, ["PAC"]))
    await link.close()
    await link.close()
    assert link.connected is False


async def test_close_during_in_flight_request_does_not_reopen(emulator):
    """A terminal close must prevent in-flight peer recovery from reconnecting."""
    link = SolarmaxLink(*emulator.addr, response_timeout=30.0)

    # A payload with no ':' makes emulator.parse_request() return [], so the
    # emulator sends nothing back and keeps the socket open — a genuine
    # in-flight poll awaiting a response that never comes.
    task = asyncio.create_task(link.request("{no-colon-unparseable}"))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if link.connected:
            break
    await asyncio.sleep(0.25)  # let it settle into the blocking read
    assert not task.done(), "request completed; it is not genuinely in flight"
    attempts_before = link.attempts

    await link.close()  # e.g. HA unload
    await asyncio.sleep(0.4)  # let the in-flight poll unwind

    assert link.attempts == attempts_before, "close() must not trigger a reconnect"
    assert link.connected is False

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await link.close()


async def test_close_waits_for_pending_connect_and_rejects_publication(emulator):
    """A connect that completes after close starts must not publish its socket."""
    real_open_connection = asyncio.open_connection
    connect_started = asyncio.Event()
    release_connect = asyncio.Event()

    async def delayed_open_connection(*args, **kwargs):
        connect_started.set()
        await release_connect.wait()
        return await real_open_connection(*args, **kwargs)

    link = SolarmaxLink(*emulator.addr)
    with patch(
        "custom_components.solarmax.connection.asyncio.open_connection",
        side_effect=delayed_open_connection,
    ):
        request_task = asyncio.create_task(link.request(build_request(1, ["PAC"])))
        await asyncio.wait_for(connect_started.wait(), timeout=1)
        close_task = asyncio.create_task(link.close())
        await asyncio.sleep(0)
        close_returned_before_connect = close_task.done()

        release_connect.set()
        outcome = (await asyncio.gather(request_task, return_exceptions=True))[0]
        await asyncio.wait_for(close_task, timeout=1)
        await link.close()

    assert close_returned_before_connect is False
    assert isinstance(outcome, LinkClosed)
    assert link.connected is False


async def test_close_is_terminal(emulator):
    """A deliberately closed link must reject every later request."""
    link = SolarmaxLink(*emulator.addr)
    try:
        await link.close()
        with pytest.raises(LinkClosed):
            await link.request(build_request(1, ["PAC"]))
    finally:
        await link.close()

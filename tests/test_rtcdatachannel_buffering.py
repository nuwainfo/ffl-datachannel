from __future__ import annotations

import asyncio
import os
import time

import pytest

from ffl_datachannel.rtcdatachannel import RTCDataChannel


CHANNEL_ID = 7


class FakeNative:
    """Minimal native backend used to test RTCDataChannel buffering semantics."""

    def __init__(self) -> None:
        self.buffered_amount = 0
        self.send_count = 0
        self.get_buffered_amount_calls = 0
        self.threshold_calls: list[tuple[int, int]] = []
        self.fail_send = False
        self.last_send_object = None

    def send(self, channel_id: int, data) -> None:
        assert channel_id == CHANNEL_ID
        self.send_count += 1
        self.last_send_object = data
        if self.fail_send:
            raise RuntimeError("simulated native send failure")

    def get_buffered_amount(self, channel_id: int) -> int:
        assert channel_id == CHANNEL_ID
        self.get_buffered_amount_calls += 1
        return self.buffered_amount

    def set_buffered_amount_low_threshold(self, channel_id: int, amount: int) -> None:
        assert channel_id == CHANNEL_ID
        self.threshold_calls.append((channel_id, amount))

    def get_data_channel_stream(self, channel_id: int) -> int:
        assert channel_id == CHANNEL_ID
        return 3

    def close_channel(self, channel_id: int) -> None:
        assert channel_id == CHANNEL_ID


class FakePeerConnection:
    def __init__(self, loop: asyncio.AbstractEventLoop, native: FakeNative) -> None:
        self._loop = loop
        self._native = native


def make_open_channel(loop: asyncio.AbstractEventLoop, native: FakeNative) -> RTCDataChannel:
    channel = RTCDataChannel(FakePeerConnection(loop, native), CHANNEL_ID, "test")
    # These tests target DataChannel send-buffer semantics, not negotiation/opening.
    channel._ready_state = "open"
    return channel


async def drain_loop(turns: int = 3) -> None:
    """Give call_soon/call_soon_threadsafe callbacks enough turns to run."""
    for _ in range(turns):
        await asyncio.sleep(0)


def test_send_clear_wait_works_when_native_direct_send_stays_at_zero() -> None:
    """
    Reproduce the FileShare pattern which deadlocked with raw libdatachannel:

        event is set -> send() -> event.clear() -> await low event

    A direct native send may leave rtcGetBufferedAmount() at zero for the whole
    operation.  The compatibility layer must still expose an aiortc-style
    logical >0 -> 0 transition on a later event-loop turn.
    """

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        native = FakeNative()
        native.buffered_amount = 0  # libdatachannel direct-send fast path
        channel = make_open_channel(loop, native)

        flushed = asyncio.Event()
        flushed.set()
        channel.on("bufferedamountlow", flushed.set)

        payload = b"x" * (256 * 1024)

        await flushed.wait()
        channel.send(payload)

        # send() must synchronously expose the logical buffered amount.
        assert channel.bufferedAmount == len(payload)
        assert native.get_buffered_amount_calls == 0

        # This is the exact ordering used by WebRTC.py.
        flushed.clear()
        await asyncio.wait_for(flushed.wait(), timeout=1.0)

        assert channel.bufferedAmount == 0
        assert native.get_buffered_amount_calls == 1

    asyncio.run(scenario())


def test_burst_send_is_coalesced_to_one_reconcile() -> None:
    """
    Performance/architecture guard: a burst must not schedule one reconciliation
    per message.  All sends in the same event-loop turn should share one native
    bufferedAmount reconciliation.
    """

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        native = FakeNative()
        native.buffered_amount = 0
        channel = make_open_channel(loop, native)

        payload = b"x" * 1024
        send_count = 4096
        low_event_count = 0

        def on_low() -> None:
            nonlocal low_event_count
            low_event_count += 1

        channel.on("bufferedamountlow", on_low)

        for _ in range(send_count):
            channel.send(payload)

        # No event-loop yield yet: logical accounting accumulates synchronously,
        # while native reconciliation has not run at all.
        assert native.send_count == send_count
        assert native.get_buffered_amount_calls == 0
        assert channel.bufferedAmount == send_count * len(payload)
        assert native.last_send_object is payload

        await drain_loop()

        assert native.get_buffered_amount_calls == 1
        assert channel.bufferedAmount == 0
        assert low_event_count == 1

    asyncio.run(scenario())


def test_native_queue_low_edge_reconciles_logical_amount() -> None:
    """When libdatachannel really queues data, preserve its low-edge wakeup."""

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        native = FakeNative()
        channel = make_open_channel(loop, native)
        channel.bufferedAmountLowThreshold = 1024

        low = asyncio.Event()
        channel.on("bufferedamountlow", low.set)

        payload = b"x" * 1024

        # Four sends create 4096 bytes of logical outstanding data.  At the
        # scheduled reconciliation, pretend libdatachannel still has 2048 bytes
        # in its own queue, which is above the low threshold.
        native.buffered_amount = 2048
        for _ in range(4):
            channel.send(payload)

        await drain_loop()

        assert channel.bufferedAmount == 2048
        assert not low.is_set()
        assert native.threshold_calls == [(CHANNEL_ID, 1024)]

        # Now emulate rtcSetBufferedAmountLowCallback after the native queue
        # crosses from above 1024 down to 512.
        native.buffered_amount = 512
        channel._handle_buffered_amount_low()
        await asyncio.wait_for(low.wait(), timeout=1.0)

        assert channel.bufferedAmount == 512

    asyncio.run(scenario())


def test_native_low_before_scheduled_reconcile_does_not_duplicate_event() -> None:
    """A native edge racing with the scheduled reconcile must emit low once."""

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        native = FakeNative()
        native.buffered_amount = 0
        channel = make_open_channel(loop, native)

        low_event_count = 0

        def on_low() -> None:
            nonlocal low_event_count
            low_event_count += 1

        channel.on("bufferedamountlow", on_low)

        channel.send(b"payload")
        assert channel.bufferedAmount > 0

        # Deliver the native low edge before the call_soon reconciliation runs.
        channel._handle_buffered_amount_low()
        await drain_loop()

        assert channel.bufferedAmount == 0
        assert low_event_count == 1
        # One read from the native edge and one from the already-scheduled
        # reconciliation.  The second one must not generate a duplicate edge.
        assert native.get_buffered_amount_calls == 2

    asyncio.run(scenario())


def test_failed_native_send_rolls_back_accounting_without_low_event() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        native = FakeNative()
        native.fail_send = True
        channel = make_open_channel(loop, native)

        low_event_count = 0

        def on_low() -> None:
            nonlocal low_event_count
            low_event_count += 1

        channel.on("bufferedamountlow", on_low)

        with pytest.raises(RuntimeError, match="simulated native send failure"):
            channel.send(b"payload")

        assert channel.bufferedAmount == 0
        assert not channel._buffered_amount_reconcile_scheduled

        await drain_loop()

        assert native.get_buffered_amount_calls == 0
        assert low_event_count == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", 1),
        ("", 1),
        ("A", 1),
        ("€", 3),
        (bytearray(b"abc"), 3),
        (memoryview(b"abcd"), 4),
    ],
)
def test_data_size_matches_aiortc_accounting(data, expected: int) -> None:
    assert RTCDataChannel._data_size(data) == expected


def test_negative_low_threshold_is_rejected_before_native_call() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        native = FakeNative()
        channel = make_open_channel(loop, native)

        with pytest.raises(ValueError, match="cannot be negative"):
            channel.bufferedAmountLowThreshold = -1

        assert native.threshold_calls == []

    asyncio.run(scenario())


def test_burst_send_performance_guard() -> None:
    """
    Coarse wall-clock regression guard for the compatibility accounting.

    This intentionally uses a generous, configurable limit because CI machine
    speed varies.  The stronger performance guarantee is the deterministic
    assertion that N sends perform zero native bufferedAmount reads before the
    loop yields and exactly one reconciliation afterwards.

    Environment overrides:
      FFL_DATACHANNEL_PERF_SEND_COUNT       (default: 50000)
      FFL_DATACHANNEL_PERF_MAX_SECONDS      (default: 3.0)
    """

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        native = FakeNative()
        native.buffered_amount = 0
        channel = make_open_channel(loop, native)

        send_count = int(os.environ.get("FFL_DATACHANNEL_PERF_SEND_COUNT", "50000"))
        max_seconds = float(os.environ.get("FFL_DATACHANNEL_PERF_MAX_SECONDS", "3.0"))
        payload = b"x" * 256

        start = time.perf_counter()
        for _ in range(send_count):
            channel.send(payload)
        elapsed = time.perf_counter() - start

        # Structural performance guarantees: no per-send native polling and no
        # payload replacement/copy at the Python facade boundary.
        assert native.send_count == send_count
        assert native.get_buffered_amount_calls == 0
        assert native.last_send_object is payload

        await drain_loop()

        assert native.get_buffered_amount_calls == 1
        assert elapsed < max_seconds, (
            f"{send_count} burst sends took {elapsed:.3f}s, exceeding the "
            f"{max_seconds:.3f}s guard. Override FFL_DATACHANNEL_PERF_MAX_SECONDS "
            "for unusually slow CI hardware."
        )

    asyncio.run(scenario())

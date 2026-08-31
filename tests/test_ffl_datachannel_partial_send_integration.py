"""End-to-end regression for partial SCTP writes in ffl_datachannel.

Run after rebuilding ffl_datachannel against the patched libdatachannel.

The test deliberately sends far more data than the SCTP send buffer without
application-level backpressure. Every RTCDataChannel.send() must nevertheless
arrive as exactly one receiver "message" event, in order and byte-for-byte
identical. This catches the dangerous failure mode where an unfinished suffix
is merged with or overtaken by the next DataChannel user message.

Recommended to run this test in a fresh Python process because the wrapper
applies rtcSetSctpSettings only before libdatachannel's runtime is preloaded.
"""

import asyncio
import os

# These are honored by the ffl native wrapper version used for the throughput
# experiments. A small send buffer makes positive short writes / backpressure
# much easier to exercise. The 8 MiB burst below still exceeds libdatachannel's
# default send space even if these overrides are unavailable.
os.environ.setdefault("FFL_DATACHANNEL_SCTP_SEND_BUFFER_SIZE", str(256 * 1024))
os.environ.setdefault("FFL_DATACHANNEL_SCTP_RECV_BUFFER_SIZE", str(4 * 1024 * 1024))

from ffl_datachannel import RTCPeerConnection, RTCSessionDescription  # noqa: E402


def _collect_candidates(pc):
    candidates = []
    complete = asyncio.Event()

    @pc.on("icecandidate")
    def _candidate(event):
        if event.candidate is None:
            complete.set()
        else:
            candidates.append(event.candidate)

    if pc.iceGatheringState == "complete":
        complete.set()

    return candidates, complete


async def _connect_pair(pc1, pc2):
    candidates1, gathering1 = _collect_candidates(pc1)
    candidates2, gathering2 = _collect_candidates(pc2)

    offer = await pc1.createOffer()
    await pc1.setLocalDescription(offer)
    await pc2.setRemoteDescription(
        RTCSessionDescription(
            sdp=pc1.localDescription.sdp,
            type=pc1.localDescription.type,
        )
    )

    answer = await pc2.createAnswer()
    await pc2.setLocalDescription(answer)
    await pc1.setRemoteDescription(
        RTCSessionDescription(
            sdp=pc2.localDescription.sdp,
            type=pc2.localDescription.type,
        )
    )

    await asyncio.wait_for(gathering1.wait(), 10)
    await asyncio.wait_for(gathering2.wait(), 10)

    for candidate in candidates1:
        await pc2.addIceCandidate(candidate)
    for candidate in candidates2:
        await pc1.addIceCandidate(candidate)


async def _test_large_binary_messages_keep_boundaries_under_backpressure():
    pc1 = RTCPeerConnection()
    pc2 = RTCPeerConnection()

    remote_channel_future = asyncio.get_running_loop().create_future()

    @pc2.on("datachannel")
    def _datachannel(channel):
        if not remote_channel_future.done():
            remote_channel_future.set_result(channel)

    sender = pc1.createDataChannel("partial-send-regression", ordered=True)
    sender_open = asyncio.Event()

    @sender.on("open")
    def _sender_open():
        sender_open.set()

    message_size = 256 * 1024
    count = 32
    payloads = [
        i.to_bytes(4, "big")
        + bytes([(17 + i) % 251]) * (message_size - 4)
        for i in range(count)
    ]

    try:
        await _connect_pair(pc1, pc2)

        receiver = await asyncio.wait_for(remote_channel_future, 10)
        await asyncio.wait_for(sender_open.wait(), 10)

        received = []
        all_received = asyncio.Event()

        @receiver.on("message")
        def _message(data):
            received.append(data)
            if len(received) == len(payloads):
                all_received.set()

        # 32 x 256 KiB = 8 MiB. No await/gating between send() calls.
        for payload in payloads:
            sender.send(payload)

        await asyncio.wait_for(all_received.wait(), 20)

        assert len(received) == count
        assert all(isinstance(item, bytes) for item in received)
        assert [len(item) for item in received] == [message_size] * count
        assert received == payloads

        deadline = asyncio.get_running_loop().time() + 10
        while sender.bufferedAmount and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert sender.bufferedAmount == 0
    finally:
        await pc1.close()
        await pc2.close()


def test_large_binary_messages_keep_boundaries_under_backpressure():
    asyncio.run(_test_large_binary_messages_keep_boundaries_under_backpressure())

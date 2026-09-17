#!/usr/bin/env python3
"""Direct, signaling-free-on-the-wire DataChannel stress endpoint.

A coordinator transports the JSON SDP emitted on stdout between two processes.
The WebRTC payload itself uses only ICE/DTLS/SCTP; no APE or HTTP tunnel is
involved.  The offerer accepts the answer JSON as one line on stdin.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from ffl_datachannel import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription


def description(desc: RTCSessionDescription) -> str:
    return json.dumps({"type": desc.type, "sdp": desc.sdp}, separators=(",", ":"))


def parse_description(raw: str) -> RTCSessionDescription:
    item = json.loads(raw)
    return RTCSessionDescription(type=item["type"], sdp=item["sdp"])


async def wait_open(channel, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while channel.readyState != "open":
        if time.monotonic() >= deadline:
            raise TimeoutError("DataChannel did not open")
        await asyncio.sleep(0.01)


async def read_answer(args: argparse.Namespace) -> str:
    if not args.answer_file:
        answer = await asyncio.to_thread(sys.stdin.readline)
        if not answer:
            raise RuntimeError("answerer closed stdin before sending SDP")
        return answer

    answer_path = Path(args.answer_file)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        try:
            answer = answer_path.read_text()
        except FileNotFoundError:
            answer = ""
        if answer.strip():
            return answer
        await asyncio.sleep(0.05)
    raise TimeoutError(f"timed out waiting for answer file: {answer_path}")


async def offerer(args: argparse.Namespace) -> None:
    config = RTCConfiguration([RTCIceServer(args.stun)]) if args.stun else RTCConfiguration()
    pc = RTCPeerConnection(config)
    channel = pc.createDataChannel("ffl-direct-stress", ordered=True)
    try:
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        print(description(pc.localDescription), flush=True)
        await pc.setRemoteDescription(parse_description(await read_answer(args)))
        await wait_open(channel, args.timeout)

        chunk = b"\0" * args.chunk_bytes
        digest = hashlib.sha256()
        sent = 0
        started = time.monotonic()
        while sent < args.bytes:
            size = min(len(chunk), args.bytes - sent)
            payload = sent.to_bytes(8, "big") + chunk[: size - 8]
            channel.send(payload)
            digest.update(payload)
            sent += size
            # Keep enough SCTP pressure to exercise ICE backpressure, without
            # retaining an unbounded Python allocation queue.
            if channel.bufferedAmount >= args.high_water:
                wait_until = time.monotonic() + args.timeout
                while channel.bufferedAmount > args.low_water:
                    if time.monotonic() >= wait_until:
                        raise TimeoutError(f"bufferedAmount stalled at {channel.bufferedAmount}")
                    await asyncio.sleep(0.001)
        print(json.dumps({"event": "sent", "bytes": sent, "sha256": digest.hexdigest(),
                          "seconds": time.monotonic() - started}), file=sys.stderr, flush=True)
        await asyncio.sleep(args.drain_seconds)
    finally:
        await pc.close()


async def answerer(args: argparse.Namespace) -> None:
    config = RTCConfiguration([RTCIceServer(args.stun)]) if args.stun else RTCConfiguration()
    pc = RTCPeerConnection(config)
    finished = asyncio.Event()
    digest = hashlib.sha256()
    expected = 0
    received = 0
    started = time.monotonic()

    @pc.on("datachannel")
    def incoming(channel):
        @channel.on("message")
        def message(data):
            nonlocal expected, received
            if not isinstance(data, bytes) or len(data) < 8:
                raise RuntimeError("invalid stress frame")
            offset = int.from_bytes(data[:8], "big")
            if offset != expected:
                raise RuntimeError(f"out-of-order frame: expected {expected}, got {offset}")
            expected += len(data)
            received += len(data)
            digest.update(data)
            if received == args.bytes:
                finished.set()

    try:
        raw_offer = base64.b64decode(args.offer_b64).decode()
        await pc.setRemoteDescription(parse_description(raw_offer))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        print(description(pc.localDescription), flush=True)
        await asyncio.wait_for(finished.wait(), args.timeout)
        print(json.dumps({"event": "received", "bytes": received, "sha256": digest.hexdigest(),
                          "seconds": time.monotonic() - started}), file=sys.stderr, flush=True)
    finally:
        await pc.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("role", choices=("offerer", "answerer"))
    p.add_argument("--offer-b64")
    p.add_argument("--answer-file", help="read the answer SDP from this file instead of stdin")
    p.add_argument("--bytes", type=int, default=1024 * 1024 * 1024)
    p.add_argument("--chunk-bytes", type=int, default=256 * 1024)
    p.add_argument("--high-water", type=int, default=16 * 1024 * 1024)
    p.add_argument("--low-water", type=int, default=8 * 1024 * 1024)
    p.add_argument("--timeout", type=float, default=180)
    p.add_argument("--drain-seconds", type=float, default=2)
    p.add_argument("--stun")
    args = p.parse_args()
    if args.role == "answerer" and not args.offer_b64:
        p.error("--offer-b64 is required for answerer")
    if args.chunk_bytes < 9 or args.bytes < args.chunk_bytes:
        p.error("--bytes must be at least --chunk-bytes, which must be >= 9")
    asyncio.run(offerer(args) if args.role == "offerer" else answerer(args))


if __name__ == "__main__":
    main()

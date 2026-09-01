#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
#
# FastFileLink CLI - Fast, no-fuss file sharing
# Copyright (C) 2025-2026 FastFileLink contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio

import pytest

from ffl_datachannel import RTCPeerConnection


pytestmark = pytest.mark.native


@pytest.mark.asyncio
async def test_set_local_description_waits_for_complete_gathering_and_exposes_candidates():
    peer = RTCPeerConnection()
    peer.createDataChannel("gathering-check")
    candidates = []

    @peer.on("icecandidate")
    def local_candidate(event):
        if event.candidate is not None:
            candidates.append(event.candidate)

    try:
        offer = await peer.createOffer()
        await asyncio.wait_for(peer.setLocalDescription(offer), timeout=10)

        assert peer.iceGatheringState == "complete"
        assert candidates
        assert peer.localDescription is not None
        assert "a=candidate:" in peer.localDescription.sdp
    finally:
        await peer.close()


@pytest.mark.asyncio
async def test_native_ping_pong():
    peerA = RTCPeerConnection()
    peerB = RTCPeerConnection()
    pendingForA = []
    pendingForB = []
    pongReceived = asyncio.Event()

    async def add_or_queue(target, pending, event):
        if event.candidate is None:
            return
        if target.remoteDescription is None:
            pending.append(event.candidate)
            return
        await target.addIceCandidate(event.candidate)

    @peerA.on("icecandidate")
    async def candidate_from_a(event):
        await add_or_queue(peerB, pendingForB, event)

    @peerB.on("icecandidate")
    async def candidate_from_b(event):
        await add_or_queue(peerA, pendingForA, event)

    @peerB.on("datachannel")
    def incoming_channel(channel):
        @channel.on("message")
        def message_from_a(message):
            if message == "PING":
                channel.send("PONG")

    channelA = peerA.createDataChannel("smoke")

    @channelA.on("message")
    def message_from_b(message):
        if message == "PONG":
            pongReceived.set()

    offer = await peerA.createOffer()
    await peerA.setLocalDescription(offer)
    await peerB.setRemoteDescription(peerA.localDescription)
    for candidate in pendingForB:
        await peerB.addIceCandidate(candidate)
    pendingForB.clear()

    answer = await peerB.createAnswer()
    await peerB.setLocalDescription(answer)
    await peerA.setRemoteDescription(peerB.localDescription)
    for candidate in pendingForA:
        await peerA.addIceCandidate(candidate)
    pendingForA.clear()

    deadline = asyncio.get_running_loop().time() + 10
    while channelA.readyState != "open":
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("DataChannel did not open")
        await asyncio.sleep(0.01)

    channelA.send("PING")
    await asyncio.wait_for(pongReceived.wait(), timeout=10)

    await peerA.close()
    await peerB.close()

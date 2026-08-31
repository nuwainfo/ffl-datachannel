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

from __future__ import annotations

import asyncio
import inspect
import unittest

from ffl_datachannel import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from ffl_datachannel import _ffl_datachannel as native
from ffl_datachannel._events import EventEmitter
from ffl_datachannel.sdp import RTCIceCandidate, candidate_from_sdp, candidate_to_sdp


class CompatibilityTest(unittest.TestCase):
    def testCandidateRoundTripMatchesAiortcSignalingShape(self):
        candidateLine = "1 1 UDP 2122260223 192.0.2.1 54321 typ host"
        candidate = candidate_from_sdp(candidateLine)

        self.assertEqual(candidateLine, candidate.to_sdp())
        self.assertEqual(candidateLine, candidate_to_sdp(candidate))
        self.assertEqual(f"candidate:{candidateLine}", candidate._native_sdp())

    def testCandidateNormalizesBrowserPrefixes(self):
        candidate = RTCIceCandidate("a=candidate:1 1 UDP 1 192.0.2.1 9 typ host")

        self.assertTrue(candidate.to_sdp().startswith("1 1 UDP"))

    def testInvalidCandidateFailsFast(self):
        with self.assertRaises(ValueError):
            candidate_from_sdp("not enough fields")

    def testICEServerConfiguration(self):
        configuration = RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:a.example", "stun:b.example"])])
        turnServer = RTCIceServer(
            urls="turn:relay.example:3478",
            username="a:b",
            credential="c@d",
            credentialType="password",
        )

        self.assertEqual(["stun:a.example", "stun:b.example"], configuration._native_urls())
        self.assertEqual(["turn:a%3Ab:c%40d@relay.example:3478"], turnServer._native_urls())
        with self.assertRaises(ValueError):
            RTCIceServer(urls="turn:relay.example:3478", credentialType="token")

    def testPublicNamesMatchFastFileLinkCalls(self):
        configurationSignature = inspect.signature(RTCConfiguration)
        channelSignature = inspect.signature(RTCPeerConnection.createDataChannel)
        description = RTCSessionDescription(sdp="v=0", type="offer")

        self.assertIn("iceServers", configurationSignature.parameters)
        for parameterName in ("maxPacketLifeTime", "maxRetransmits", "ordered", "protocol", "negotiated", "id"):
            self.assertIn(parameterName, channelSignature.parameters)
        self.assertEqual("v=0", description.sdp)
        self.assertEqual("offer", description.type)


class EventEmitterTest(unittest.IsolatedAsyncioTestCase):
    async def testDecoratorAndDirectHandlersShareOneDispatchPath(self):
        eventEmitter = EventEmitter(asyncio.get_running_loop())
        values = []
        done = asyncio.Event()

        @eventEmitter.on("value")
        async def handleAsyncValue(value):
            values.append(("async", value))
            if len(values) == 2:
                done.set()

        def handleSyncValue(value):
            values.append(("sync", value))
            if len(values) == 2:
                done.set()

        eventEmitter.on("value", handleSyncValue)
        eventEmitter._emit_threadsafe("value", 7)
        await asyncio.wait_for(done.wait(), timeout=1)

        self.assertEqual([("async", 7), ("sync", 7)], sorted(values))


class NativeLoopbackTest(unittest.IsolatedAsyncioTestCase):
    async def testBufferedAmountLowNotifiesForDirectSctpSend(self):
        peerA = RTCPeerConnection()
        peerB = RTCPeerConnection()
        pendingForA = []
        pendingForB = []
        channelOpened = asyncio.Event()
        bufferDrained = asyncio.Event()

        async def addOrQueue(target, pendingCandidates, event):
            if event.candidate is None:
                return
            if target.remoteDescription is None:
                pendingCandidates.append(event.candidate)
                return
            await target.addIceCandidate(event.candidate)

        @peerA.on("icecandidate")
        async def handleCandidateFromA(event):
            await addOrQueue(peerB, pendingForB, event)

        @peerB.on("icecandidate")
        async def handleCandidateFromB(event):
            await addOrQueue(peerA, pendingForA, event)

        channelA = peerA.createDataChannel("buffered-amount")

        @channelA.on("open")
        def handleChannelOpen():
            channelOpened.set()

        @channelA.on("bufferedamountlow")
        def handleBufferDrained():
            bufferDrained.set()

        try:
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

            await asyncio.wait_for(channelOpened.wait(), timeout=10)
            channelA.bufferedAmountLowThreshold = 0
            channelA.send(b"x" * (256 * 1024))

            self.assertEqual(0, channelA.bufferedAmount)
            await asyncio.wait_for(bufferDrained.wait(), timeout=10)
        finally:
            await peerA.close()
            await peerB.close()

    async def testNativeBinaryPingPong(self):
        self.assertTrue(native.__file__.lower().endswith(".pyd"), native.__file__)

        peerA = RTCPeerConnection()
        peerB = RTCPeerConnection()
        pendingForA = []
        pendingForB = []
        channelOpened = asyncio.Event()
        pongReceived = asyncio.Event()

        async def addOrQueue(target, pendingCandidates, event):
            if event.candidate is None:
                return
            if target.remoteDescription is None:
                pendingCandidates.append(event.candidate)
                return
            await target.addIceCandidate(event.candidate)

        @peerA.on("icecandidate")
        async def handleCandidateFromA(event):
            await addOrQueue(peerB, pendingForB, event)

        @peerB.on("icecandidate")
        async def handleCandidateFromB(event):
            await addOrQueue(peerA, pendingForA, event)

        @peerB.on("datachannel")
        def handleIncomingChannel(channel):
            @channel.on("message")
            def handleMessageFromA(message):
                if message == b"PING":
                    channel.send(b"PONG")

        channelA = peerA.createDataChannel("native-loopback")

        @channelA.on("open")
        def handleChannelOpen():
            channelOpened.set()

        @channelA.on("message")
        def handleMessageFromB(message):
            if message == b"PONG":
                pongReceived.set()

        try:
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

            await asyncio.wait_for(channelOpened.wait(), timeout=10)
            channelA.send(b"PING")
            await asyncio.wait_for(pongReceived.wait(), timeout=10)
        finally:
            await peerA.close()
            await peerB.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

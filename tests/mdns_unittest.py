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

import struct
import unittest
from unittest.mock import AsyncMock, patch

from ffl_datachannel import _mdns
from ffl_datachannel.rtcpeerconnection import RTCPeerConnection
from ffl_datachannel.sdp import RTCIceCandidate


class MDNSUnitTest(unittest.IsolatedAsyncioTestCase):
    def testExtractsMatchingIPv4Answer(self) -> None:
        hostname = "browser-candidate.local"
        question = _mdns._encode_name(hostname) + struct.pack("!HH", 1, 1)
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 120, 4) + bytes((192, 168, 1, 20))
        packet = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + question + answer

        self.assertEqual(_mdns._extract_address(packet, hostname, 1), "192.168.1.20")

    def testIgnoresAnswerForAnotherName(self) -> None:
        hostname = "browser-candidate.local"
        question = _mdns._encode_name(hostname) + struct.pack("!HH", 1, 1)
        answer = _mdns._encode_name("other.local") + struct.pack("!HHIH", 1, 1, 120, 4) + bytes((192, 168, 1, 20))
        packet = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + question + answer

        self.assertIsNone(_mdns._extract_address(packet, hostname, 1))

    async def testCandidateHostIsReplacedAfterMDNSResolution(self) -> None:
        candidate = RTCIceCandidate(
            "candidate:1 1 udp 1 browser-candidate.local 5000 typ host",
            sdpMid="0",
            sdpMLineIndex=0,
        )
        with patch("ffl_datachannel.rtcpeerconnection._mdns.resolve", new=AsyncMock(return_value="192.168.1.20")):
            resolved = await RTCPeerConnection._resolve_mdns_candidate(candidate)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.candidate.split()[4], "192.168.1.20")
        self.assertEqual(resolved.sdpMid, "0")


if __name__ == "__main__":
    unittest.main()

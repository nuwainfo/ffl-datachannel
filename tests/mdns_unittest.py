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

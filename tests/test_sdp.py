import pytest

from ffl_datachannel.sdp import RTCIceCandidate, candidate_from_sdp


def test_candidate_round_trip_matches_aiortc_signaling_shape():
    line = "1 1 UDP 2122260223 192.0.2.1 54321 typ host"
    candidate = candidate_from_sdp(line)
    assert candidate.to_sdp() == line
    assert candidate._native_sdp() == f"candidate:{line}"


def test_candidate_normalizes_browser_prefixes():
    candidate = RTCIceCandidate("a=candidate:1 1 UDP 1 192.0.2.1 9 typ host")
    assert candidate.to_sdp().startswith("1 1 UDP")


def test_invalid_candidate_fails_fast():
    with pytest.raises(ValueError):
        candidate_from_sdp("not enough fields")

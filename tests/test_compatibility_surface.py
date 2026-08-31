import inspect

from ffl_datachannel import RTCConfiguration, RTCPeerConnection, RTCSessionDescription


def test_public_names_match_fastfilelink_aiortc_calls():
    config_signature = inspect.signature(RTCConfiguration)
    assert "iceServers" in config_signature.parameters

    channel_signature = inspect.signature(RTCPeerConnection.createDataChannel)
    for name in (
        "maxPacketLifeTime",
        "maxRetransmits",
        "ordered",
        "protocol",
        "negotiated",
        "id",
    ):
        assert name in channel_signature.parameters

    description = RTCSessionDescription(sdp="v=0", type="offer")
    assert description.sdp == "v=0"
    assert description.type == "offer"

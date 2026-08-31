from ffl_datachannel import RTCConfiguration, RTCIceServer


def test_stun_urls_are_preserved():
    config = RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:a.example", "stun:b.example"])])
    assert config._native_urls() == ["stun:a.example", "stun:b.example"]


def test_turn_credentials_are_encoded_into_native_uri():
    server = RTCIceServer(
        urls="turn:relay.example:3478",
        username="a:b",
        credential="c@d",
    )
    assert server._native_urls() == ["turn:a%3Ab:c%40d@relay.example:3478"]

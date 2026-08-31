# ffl-datachannel

`ffl-datachannel` is a focused Python WebRTC DataChannel transport. It exposes
the aiortc-compatible subset used by FastFileLink, backed by libdatachannel,
libjuice, usrsctp, and Mbed TLS. Native wheels statically link that transport
and crypto stack; OpenSSL is not used.

## Compatibility scope

This is not an aiortc fork. Media, RTP, tracks, transceivers, codecs, and
WebSocket support are intentionally out of scope.

The aiortc-compatible API intentionally mixes `camelCase` and `snake_case`.
This package preserves those public spellings for compatibility rather than
normalizing them to one convention.

Supported peer-connection API: `RTCPeerConnection`, `RTCConfiguration`,
`RTCIceServer`, `RTCSessionDescription`, `RTCIceCandidate`, offer/answer,
trickle ICE, `createDataChannel()`, `addIceCandidate()`, `close()`, and the
connection, ICE, gathering, signaling, and incoming-DataChannel events.

`RTCDataChannel` supports labels, IDs, reliability settings, state,
`bufferedAmount`, `bufferedAmountLowThreshold`, `send()`, `close()`, and the
`open`, `close`, `error`, `message`, and `bufferedamountlow` events. Both
decorator and direct callback registration are available:

```python
@channel.on("message")
def on_message(data):
    ...

channel.on("bufferedamountlow", on_buffer_low)
```

`candidate_from_sdp()` and `RTCIceCandidate.to_sdp()` use aiortc's signaling
form without the `candidate:` prefix. `addIceCandidate()` accepts candidates,
FastFileLink-style candidate dictionaries, or `None` for end-of-candidates.

## Build native wheels

Pinned dependencies are libdatachannel v0.24.5, Mbed TLS 3.6.7, and the
libdatachannel-pinned libjuice, usrsctp, and plog submodules. All build scripts
fetch missing sources, build static Mbed TLS with `MBEDTLS_SSL_DTLS_SRTP`, and
verify that the final extension does not dynamically link to the third-party
transport or Mbed TLS libraries.

### Windows

Requirements: CPython 3.10+, CMake 3.24+, Visual Studio 2022 C++ Build Tools,
Ninja, and Git. From a PowerShell prompt:

```powershell
.\scripts\build-native.ps1
python -m pip install --force-reinstall --no-deps .\out\native\wheel\ffl_datachannel-*.whl
```

The script initializes a Visual Studio x64 environment, writes generated files
to `out\native`, and verifies dependencies with `dumpbin /DEPENDENTS`. Use
`-Clean` to recreate that output directory. Do not invoke `python -m build
--wheel` directly unless you have already installed Mbed TLS and explicitly
provide its `MbedTLS_DIR` package-config directory.

### Linux and macOS

Run the platform script from the repository root:

```bash
bash ./scripts/build-linux.sh
# or
bash ./scripts/build-macos.sh
```

Wheels are written to `out/native-linux/wheel/` or
`out/native-macos/wheel/`. The macOS script accepts `ARCH` (default:
`uname -m`) and respects `MACOSX_DEPLOYMENT_TARGET`. Both scripts locate the
installed `MbedTLSConfig.cmake` automatically, supporting either `lib/` or
`lib64/` layouts.

On Linux, `auditwheel` is optional. If `MANYLINUX_PLAT`, `AUDITWHEEL_PLAT`, or
the local auditwheel/glibc policy identifies a supported manylinux target, the
script repairs the raw wheel for that policy. Otherwise it produces a native
Linux wheel unchanged.

## Test and validate

Install facade-test dependencies and run the Python-only suite:

```bash
python -m pip install pytest pytest-asyncio
PYTHONPATH=src pytest -q -m 'not native'
```

After installing a native wheel, run the native pytest suite:

```bash
FFL_DATACHANNEL_REQUIRE_NATIVE=1 pytest -q -m native
```

On Windows, the equivalent standard-library wheel test and browser interop
tests are:

```powershell
.\scripts\test-native.ps1
.\scripts\test-browser.ps1 -Browser all
```

The browser suite drives Chrome and Firefox over localhost and checks a
256 KiB binary DataChannel exchange. Choose `-Browser chrome` or
`-Browser firefox` to run one browser.

To inspect a wheel manually, use `ldd` on Linux, `otool -L` on macOS, or
`dumpbin /DEPENDENTS` on Windows. None should report dynamic libdatachannel,
libjuice, usrsctp, mbedtls, mbedcrypto, or mbedx509 libraries.

## FastFileLink integration checks

The native transport resolves remote `*.local` ICE host candidates before
passing them to libdatachannel, which is required for Chrome and Firefox LAN
interoperability. It preserves aiortc-style SCTP backpressure: direct sends
schedule `bufferedamountlow` on the next asyncio turn, while queued sends use
the native buffered-amount callback.

For a FileShare checkout, run relevant suites with both backends:

```powershell
foreach ($backend in 'aiortc', 'ffl') {
  $env:FFL_WEBRTC_BACKEND = $backend
  python -m unittest -v tests.FFLTest
}
```

Run the remaining FileShare WebRTC, E2EE, download, resume, and performance
suites as appropriate for the change.

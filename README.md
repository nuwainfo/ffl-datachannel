# ffl-datachannel

`ffl-datachannel` is a focused Python WebRTC DataChannel transport. It exposes
the aiortc-compatible subset used by FastFileLink, backed by libdatachannel,
libjuice, usrsctp, and GnuTLS. Native wheels always statically link
libdatachannel, libjuice, and usrsctp; OpenSSL is not used. GnuTLS itself is
statically linked (via vcpkg) on Windows. On macOS the extension links
GnuTLS dynamically against Homebrew, but `delocate` bundles that dylib (and
its own dependencies) into the wheel at build time and rewrites its load
path, so the shipped wheel has no external GnuTLS dependency at runtime —
the same pattern used by `ffl-p2p`. On Linux the same bundling happens only
when building a manylinux-targeted wheel (via `auditwheel repair`); a plain
native Linux build still depends on the build host's system GnuTLS.

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

## SCTP retransmit timeout

`ffl_datachannel` defaults SCTP's minimum retransmit timeout to 1000ms
(libdatachannel's own optimized default is ~200ms). On networks where
round-trip variance regularly exceeds 200ms, that short timeout makes SCTP
mistake ordinary delay for packet loss, collapsing the congestion window and
stalling large transfers even though nothing was lost — a delivery-policy
choice, not a fix to an SCTP/WebRTC algorithm bug. The default is applied in
Python (`src/ffl_datachannel/_native_backend.py`, at import time via
`os.environ.setdefault`) rather than hardcoded natively, so it can be
adjusted without a rebuild. Override it with
`FFL_DATACHANNEL_SCTP_MIN_RETRANSMIT_TIMEOUT_MS` (set it to `0` or negative
to restore libdatachannel's own optimized default); it's read once, at first
`RTCPeerConnection` construction.

`FFL_DATACHANNEL_BIND_ADDRESS` optionally pins libjuice's ICE gathering to
one local address — useful for constraining gathering to IPv4 or IPv6 while
diagnosing a path-specific issue. It's opt-in and unset by default: don't set
it globally to something like `0.0.0.0` as a standing workaround, since that
excludes every IPv6 ICE candidate and would break direct connectivity on
IPv6-only networks.

## Native logging

`set_log_level(level)` configures libdatachannel's native logger (silent by
default), writing directly to stderr as `[ffl_datachannel] LEVEL message`:

```python
import logging
import ffl_datachannel
ffl_datachannel.set_log_level(logging.DEBUG)
```

`level` is a standard `logging` module level (`logging.DEBUG`, `.INFO`,
`.WARNING`, `.ERROR`, `.CRITICAL`, ...); anything at or below a threshold
maps to the nearest native level (`CRITICAL` maps to native `fatal`; above
`CRITICAL` maps to `none`). This mirrors `ffl_p2p.Native.setNativeLoggingLevel`.
Can also be set at process startup via `FFL_DATACHANNEL_LOG_LEVEL` (a native
level name — `verbose`/`debug`/`info`/`warning`/`error`/`fatal`/`none` — read
once, at first `RTCPeerConnection` construction). Both are process-wide and
safe to call repeatedly to change the level at runtime.

## Build native wheels

Pinned dependencies are libdatachannel v0.24.5 and the libdatachannel-pinned
libjuice, usrsctp, and plog submodules. GnuTLS 3.8.x is not vendored: Windows
provisions a static build via vcpkg (see below), while Linux and macOS expect
a system GnuTLS development package. All build scripts fetch missing sources
and verify that the final extension does not dynamically link to the vendored
transport libraries (libdatachannel, libjuice, usrsctp).

### Windows

Requirements: CPython 3.10+, CMake 3.24+, Visual Studio 2022 C++ Build Tools,
Ninja, and Git. From a PowerShell prompt:

```powershell
.\scripts\build-native.ps1
python -m pip install --force-reinstall --no-deps .\out\native\wheel\ffl_datachannel-*.whl
```

The script initializes a Visual Studio x64 environment, writes generated files
to `out\native`, and verifies dependencies with `dumpbin /DEPENDENTS`. Use
`-Clean` to recreate that output directory. GnuTLS has no native MSVC build of
its own, so the script provisions it from a pinned vcpkg checkout
(`third_party/vcpkg`) using the repository-owned
`vcpkg-overlays/shiftmedia-libgnutls` overlay port, which restores a Windows
GnuTLS port that upstream vcpkg has since delisted. That installs a static
`gnutls.lib` (triplet `x64-windows-static-md`) plus `pkgconf`, which the CMake
configure step uses to resolve GnuTLS's full static closure (Nettle, GMP,
libtasn1, zlib). Do not invoke `python -m build --wheel` directly unless you
have already provisioned that toolchain and pass its `pkgconf.exe` via
`-DPKG_CONFIG_EXECUTABLE`.

### Linux and macOS

Install a GnuTLS 3.8.x (or newer) development package first:

```bash
# Debian/Ubuntu
sudo apt install libgnutls28-dev pkg-config

# macOS
brew install cmake git pkg-config gnutls python
```

Then run the platform script from the repository root:

```bash
bash ./scripts/build-linux.sh
# or
bash ./scripts/build-macos.sh
```

Wheels are written to `out/native-linux/wheel/` or `out/native-macos/wheel/`.
The macOS script accepts `ARCH` (default: `uname -m`) and respects
`MACOSX_DEPLOYMENT_TARGET`. Both scripts locate GnuTLS via `pkg-config`; set
`FFL_DATACHANNEL_GNUTLS_ROOT` to point at an alternate prefix (for example a
Conda environment on Linux, or a non-default Homebrew prefix on macOS) when
the default `pkg-config` search does not find it. Unlike the vendored
transport libraries, the extension itself still links GnuTLS dynamically on
these platforms.

The macOS script always installs `delocate` and repairs the raw wheel with
it, bundling `libgnutls` (and its own dependency closure) into the wheel and
rewriting the load path — the shipped wheel does not depend on the target
machine having Homebrew's GnuTLS installed. On Linux, `auditwheel` is
optional: if `MANYLINUX_PLAT`, `AUDITWHEEL_PLAT`, or the local
auditwheel/glibc policy identifies a supported manylinux target, the script
repairs the raw wheel for that policy the same way (bundling GnuTLS).
Otherwise it produces a native Linux wheel that depends on the build host's
system GnuTLS at runtime — there is no Linux equivalent of delocate's
"bundle for whatever this machine has" mode outside a manylinux/musllinux
policy target.

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
libjuice, or usrsctp libraries. On Windows, GnuTLS and its closure (Nettle,
GMP, libtasn1, zlib) must also be static. On macOS and manylinux-repaired
Linux wheels, GnuTLS is bundled inside the wheel (`.dylibs/libgnutls*.dylib`
on macOS; a hash-suffixed `libgnutls*.so` on Linux) rather than statically
linked, and the extension should reference neither a Homebrew path nor the
build host's raw system `libgnutls`; a plain (non-manylinux) native Linux
wheel still depends on the build host's system GnuTLS at runtime.

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

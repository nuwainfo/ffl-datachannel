#!/usr/bin/env bash
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
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
OUT="$ROOT/out/native-linux"
PREFIX="$OUT/prefix"
MBED_BUILD="$OUT/mbedtls"
RAW_WHEEL="$OUT/raw-wheel"
WHEEL_DIR="$OUT/wheel"
WHEEL_EXTRACT="$OUT/wheel-extract"

for command in cmake git ldd "$PYTHON"; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

detect_manylinux_plat() {
    local plat="${MANYLINUX_PLAT:-${AUDITWHEEL_PLAT:-}}"
    local arch glibc candidate supported

    command -v auditwheel >/dev/null 2>&1 || return 1
    if [[ "$plat" == manylinux_* ]]; then
        printf '%s\n' "$plat"
        return 0
    fi

    arch="$(uname -m)"
    glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}' || true)"
    if [[ "$glibc" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
        candidate="manylinux_${BASH_REMATCH[1]}_${BASH_REMATCH[2]}_${arch}"
        supported="$(auditwheel repair --help 2>&1 || true)"
        if grep -Fq "$candidate" <<<"$supported"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi
    return 1
}

apply_dependency_patch() {
    local repo="$ROOT/third_party/libdatachannel"
    local patch="$ROOT/patches/libdatachannel_partial_send.patch"

    # Vendored source may have whitespace normalized by the checkout host.
    # Ignore only whitespace differences; still require every patch hunk to
    # match the pinned libdatachannel revision.
    if git -C "$repo" apply --reverse --check --ignore-space-change --ignore-whitespace "$patch" >/dev/null 2>&1; then
        echo "Dependency patch already applied"
    elif git -C "$repo" apply --check --ignore-space-change --ignore-whitespace "$patch"; then
        git -C "$repo" apply --ignore-space-change --ignore-whitespace "$patch"
        echo "Dependency patch applied"
    else
        echo "Dependency patch does not apply cleanly to $(git -C "$repo" rev-parse --short HEAD): $patch" >&2
        exit 1
    fi
}

MANYLINUX=""
if MANYLINUX="$(detect_manylinux_plat)"; then
    echo "manylinux     : enabled ($MANYLINUX)"
else
    echo "manylinux     : not detected; building native Linux wheel"
fi
echo "python        : $($PYTHON -V 2>&1)"

if [[ ! -f "$ROOT/third_party/libdatachannel/CMakeLists.txt" || ! -f "$ROOT/third_party/mbedtls/CMakeLists.txt" ]]; then
    "$PYTHON" "$ROOT/scripts/bootstrap.py"
fi
apply_dependency_patch

if ! "$PYTHON" -c 'import build, scikit_build_core' >/dev/null 2>&1; then
    "$PYTHON" -m pip install --disable-pip-version-check build scikit-build-core
fi

rm -rf "$MBED_BUILD" "$PREFIX" "$RAW_WHEEL" "$WHEEL_DIR" "$WHEEL_EXTRACT"
mkdir -p "$RAW_WHEEL" "$WHEEL_DIR"

MBED_CONFIG="$ROOT/third_party/mbedtls/include/mbedtls/mbedtls_config.h"
if ! grep -Eq '^[[:space:]]*#define[[:space:]]+MBEDTLS_SSL_DTLS_SRTP[[:space:]]*$' "$MBED_CONFIG"; then
    sed -i.bak -E 's|^([[:space:]]*)//[[:space:]]*#define[[:space:]]+MBEDTLS_SSL_DTLS_SRTP[[:space:]]*$|#define MBEDTLS_SSL_DTLS_SRTP|' "$MBED_CONFIG"
    rm -f "$MBED_CONFIG.bak"
fi
grep -Eq '^[[:space:]]*#define[[:space:]]+MBEDTLS_SSL_DTLS_SRTP[[:space:]]*$' "$MBED_CONFIG" || {
    echo "MBEDTLS_SSL_DTLS_SRTP is required for the Mbed TLS backend" >&2
    exit 1
}

cmake -S "$ROOT/third_party/mbedtls" -B "$MBED_BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DENABLE_PROGRAMS=OFF \
    -DENABLE_TESTING=OFF \
    -DUSE_STATIC_MBEDTLS_LIBRARY=ON \
    -DUSE_SHARED_MBEDTLS_LIBRARY=OFF
cmake --build "$MBED_BUILD" --target install --parallel

MBEDTLS_CONFIG="$(find "$PREFIX" -type f -path '*/cmake/MbedTLS/MbedTLSConfig.cmake' -print -quit)"
[[ -n "$MBEDTLS_CONFIG" ]] || {
    echo "Mbed TLS installed without its CMake package configuration below: $PREFIX" >&2
    exit 1
}
MBEDTLS_DIR="$(dirname "$MBEDTLS_CONFIG")"

# CMAKE_ARGS is consumed directly by scikit-build-core's CMake invocation.
# This avoids relying on the build frontend to forward a PEP 517 setting.
export CMAKE_ARGS="${CMAKE_ARGS:+$CMAKE_ARGS }-DMbedTLS_DIR=$MBEDTLS_DIR"
"$PYTHON" -m build --wheel --no-isolation --outdir "$RAW_WHEEL" "$ROOT"

raw_wheels=("$RAW_WHEEL"/*.whl)
[[ -f "${raw_wheels[0]}" ]] || { echo "Wheel was not created." >&2; exit 1; }
[[ ${#raw_wheels[@]} -eq 1 ]] || { echo "Expected one raw wheel." >&2; exit 1; }

if [[ -n "$MANYLINUX" ]]; then
    auditwheel show "${raw_wheels[0]}"
    auditwheel repair --plat "$MANYLINUX" --wheel-dir "$WHEEL_DIR" "${raw_wheels[0]}"
else
    cp "${raw_wheels[0]}" "$WHEEL_DIR/"
fi

wheels=("$WHEEL_DIR"/*.whl)
[[ -f "${wheels[0]}" && ${#wheels[@]} -eq 1 ]] || { echo "Expected one final wheel." >&2; exit 1; }

"$PYTHON" - "$WHEEL_EXTRACT" "${wheels[0]}" <<'PY'
import shutil
import sys
import zipfile
from pathlib import Path

destination, wheel = map(Path, sys.argv[1:])
shutil.rmtree(destination, ignore_errors=True)
with zipfile.ZipFile(wheel) as archive:
    archive.extractall(destination)
extensions = list(destination.glob("ffl_datachannel/_ffl_datachannel*.so"))
if len(extensions) != 1:
    raise SystemExit(f"Expected one native extension, found {len(extensions)}")
print(extensions[0])
PY

extension="$(find "$WHEEL_EXTRACT/ffl_datachannel" -maxdepth 1 -name '_ffl_datachannel*.so' -print -quit)"
dependencies="$(ldd "$extension")"
printf '%s\n' "$dependencies"
if grep -Eiq '(libdatachannel|libjuice|libusrsctp|libmbedtls|libmbedcrypto|libmbedx509)\.so' <<<"$dependencies"; then
    echo "The extension has dynamically linked third-party dependencies." >&2
    exit 1
fi

echo "[PASS] Native Linux wheel build completed: ${wheels[0]}"

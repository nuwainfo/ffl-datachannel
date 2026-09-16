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
ARCH="${ARCH:-$(uname -m)}"
OUT="$ROOT/out/native-macos"
WHEEL_DIR="$OUT/wheel"
WHEEL_EXTRACT="$OUT/wheel-extract"

for command in cmake git otool pkg-config "$PYTHON"; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

apply_dependency_patch() {
    local patch="$1"
    local repo="$ROOT/third_party/libdatachannel"

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

echo "python        : $($PYTHON -V 2>&1)"
echo "architecture  : $ARCH"

if [[ ! -f "$ROOT/third_party/libdatachannel/CMakeLists.txt" ]]; then
    "$PYTHON" "$ROOT/scripts/bootstrap.py"
fi
apply_dependency_patch "$ROOT/patches/libdatachannel_partial_send.patch"
apply_dependency_patch "$ROOT/patches/libdatachannel_gnutls_dtls_diagnostics.patch"

if ! "$PYTHON" -c 'import build, scikit_build_core' >/dev/null 2>&1; then
    "$PYTHON" -m pip install --disable-pip-version-check build scikit-build-core
fi

rm -rf "$WHEEL_DIR" "$WHEEL_EXTRACT"
mkdir -p "$WHEEL_DIR"

export ARCHFLAGS="-arch $ARCH"

GNUTLS_ROOT="${FFL_DATACHANNEL_GNUTLS_ROOT:-}"
if [[ -z "$GNUTLS_ROOT" ]] && command -v brew >/dev/null 2>&1; then
    GNUTLS_ROOT="$(brew --prefix gnutls 2>/dev/null || true)"
fi

for pkgConfigDirectory in "$GNUTLS_ROOT/lib/pkgconfig" "$GNUTLS_ROOT/share/pkgconfig"; do
    if [[ -f "$pkgConfigDirectory/gnutls.pc" ]]; then
        export PKG_CONFIG_PATH="$pkgConfigDirectory${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
        echo "Using GnuTLS pkg-config metadata: $pkgConfigDirectory/gnutls.pc"
        break
    fi
done

if ! pkg-config --atleast-version=3.8 gnutls; then
    cat >&2 <<'EOF'
ffl-datachannel requires GnuTLS 3.8.x or newer.

Install the macOS prerequisites and rerun the build:

  brew install cmake git pkg-config gnutls python

Set FFL_DATACHANNEL_GNUTLS_ROOT=/path/to/prefix when GnuTLS is installed
outside Homebrew's default prefix.
EOF
    exit 1
fi

cmake_args=(
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_OSX_ARCHITECTURES="$ARCH"
)
if [[ -n "${MACOSX_DEPLOYMENT_TARGET:-}" ]]; then
    cmake_args+=("-DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET")
fi

# The build links GnuTLS dynamically here: Homebrew only ships a shared
# libgnutls, and it owns its own transitive crypto dependencies (Nettle,
# GMP, libtasn1). CMAKE_ARGS is consumed directly by scikit-build-core's
# CMake invocation, which resolves GnuTLS itself via pkg-config using the
# PKG_CONFIG_PATH exported above.
export CMAKE_ARGS="${CMAKE_ARGS:+$CMAKE_ARGS }${cmake_args[*]}"
"$PYTHON" -m build --wheel --no-isolation --outdir "$WHEEL_DIR" "$ROOT"

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
dependencies="$(otool -L "$extension")"
printf '%s\n' "$dependencies"
if grep -Eiq '(libdatachannel|libjuice|libusrsctp)\.(dylib|so)' <<<"$dependencies"; then
    echo "The extension has dynamically linked vendored third-party dependencies." >&2
    exit 1
fi
# A dynamic Homebrew libgnutls dependency is expected here, unlike the
# vendored libraries checked above, which must always stay statically linked.

echo "[PASS] Native macOS wheel build completed: ${wheels[0]}"

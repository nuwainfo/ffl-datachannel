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

import argparse
import re
import subprocess
from pathlib import Path


class Bootstrapper:
    LIBDATACHANNEL_TAG = "v0.24.5"

    # vcpkg commit immediately before it upgraded Nettle to 4.0 and delisted
    # shiftmedia-libgnutls. The checked-in overlay requires Nettle 3.10.
    VCPKG_TAG = "5812244ec0caf8f5ab9f71cac42d98aea6cc53b8"
    VCPKG_URL = "https://github.com/microsoft/vcpkg.git"

    def __init__(self, root: Path):
        self.root = root
        self.third_party = root / "third_party"

    def run(self) -> None:
        self.third_party.mkdir(exist_ok=True)
        self._clone_repository(
            "https://github.com/paullouisageneau/libdatachannel.git",
            self.third_party / "libdatachannel",
            self.LIBDATACHANNEL_TAG,
            recursive=True,
        )

    def bootstrap_windows_vcpkg(self, force: bool = False) -> None:
        """Provision the pinned Windows GnuTLS toolchain from source.

        Official vcpkg no longer supplies the historical MSVC GnuTLS port.
        The repository-owned overlay in vcpkg-overlays/ restores that port,
        but it must be used with the last compatible vcpkg baseline rather
        than current vcpkg.
        """
        self.third_party.mkdir(exist_ok=True)
        vcpkgDirectory = self.third_party / "vcpkg"
        if force:
            import shutil

            shutil.rmtree(vcpkgDirectory, ignore_errors=True)

        if not vcpkgDirectory.exists():
            subprocess.run(
                ["git", "clone", "--filter=blob:none", "--no-checkout", self.VCPKG_URL, str(vcpkgDirectory)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(vcpkgDirectory), "checkout", "--detach", self.VCPKG_TAG],
                check=True,
            )
        elif not (vcpkgDirectory / ".git").exists():
            raise RuntimeError(f"Destination exists but is not a git checkout: {vcpkgDirectory}")
        else:
            currentRef = subprocess.check_output(
                ["git", "-C", str(vcpkgDirectory), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            if currentRef != self.VCPKG_TAG:
                subprocess.run(
                    ["git", "-C", str(vcpkgDirectory), "fetch", "--depth", "1", "origin", self.VCPKG_TAG],
                    check=True,
                )
                subprocess.run(
                    ["git", "-C", str(vcpkgDirectory), "checkout", "--detach", "FETCH_HEAD"],
                    check=True,
                )

        overlay = self.root / "vcpkg-overlays"
        port = overlay / "shiftmedia-libgnutls" / "portfile.cmake"
        if not port.is_file():
            raise RuntimeError(f"Windows GnuTLS overlay is missing: {port}")

        executable = vcpkgDirectory / "vcpkg.exe"
        if not executable.is_file():
            # An absolute path avoids relying on cmd.exe's current-directory
            # command search, which is disabled by the common
            # NoDefaultCurrentDirectoryInExePath=1 security hardening setting.
            subprocess.run(
                ["cmd.exe", "/c", str(vcpkgDirectory / "bootstrap-vcpkg.bat"), "-disableMetrics"],
                cwd=vcpkgDirectory,
                check=True,
            )

        if self._has_incompatible_nettle(vcpkgDirectory):
            print("Removing Nettle 4 packages from the previous vcpkg baseline")
            subprocess.run(
                [
                    str(executable), "remove", "--recurse",
                    "nettle:x64-windows", "nettle:x64-windows-static-md",
                ],
                cwd=vcpkgDirectory,
                check=True,
            )

        subprocess.run(
            [
                str(executable), "install",
                "shiftmedia-libgnutls:x64-windows-static-md",
                "pkgconf:x64-windows",
                f"--overlay-ports={overlay}",
            ],
            cwd=vcpkgDirectory,
            check=True,
        )

        expectedPaths = [
            vcpkgDirectory / "installed" / "x64-windows-static-md" / "lib" / "gnutls.lib",
            vcpkgDirectory / "installed" / "x64-windows" / "tools" / "pkgconf" / "pkgconf.exe",
        ]
        missingPaths = [str(path) for path in expectedPaths if not path.is_file()]
        if missingPaths:
            raise RuntimeError(
                "vcpkg completed without the required Windows GnuTLS toolchain: "
                + ", ".join(missingPaths)
            )
        print("Windows vcpkg GnuTLS toolchain ready")

    @staticmethod
    def _has_incompatible_nettle(vcpkgDirectory: Path) -> bool:
        status = vcpkgDirectory / "installed" / "vcpkg" / "status"
        if not status.is_file():
            return False
        packages = status.read_text(encoding="utf-8")
        nettleVersions = re.findall(
            r"^Package: nettle\r?\nVersion: ([^\r\n]+)", packages, re.MULTILINE,
        )
        return any(version != "3.10" for version in nettleVersions)

    def _clone_repository(self, url: str, destination: Path, tag: str, *, recursive: bool) -> None:
        if destination.exists():
            if not (destination / ".git").exists():
                raise RuntimeError(f"Destination exists but is not a git checkout: {destination}")
            print(f"Using existing dependency checkout: {destination}")
            return

        command = ["git", "clone", "--depth", "1", "--branch", tag]
        if recursive:
            command.append("--recurse-submodules")
        command.extend([url, str(destination)])
        subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch pinned ffl-datachannel native dependencies")
    parser.add_argument("--windows-vcpkg", action="store_true", dest="windowsVcpkg")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bootstrapper = Bootstrapper(root)
    if args.windowsVcpkg:
        bootstrapper.bootstrap_windows_vcpkg(force=args.force)
    else:
        bootstrapper.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
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
import subprocess
from pathlib import Path


class Bootstrapper:
    LIBDATACHANNEL_TAG = "v0.24.5"
    MBEDTLS_TAG = "mbedtls-3.6.7"

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
        self._clone_repository(
            "https://github.com/Mbed-TLS/mbedtls.git",
            self.third_party / "mbedtls",
            self.MBEDTLS_TAG,
            recursive=True,
        )

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
    parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    Bootstrapper(root).run()


if __name__ == "__main__":
    main()

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

from dataclasses import dataclass, field
from urllib.parse import quote


@dataclass(slots=True)
class RTCIceServer:
    urls: str | list[str]
    username: str | None = None
    credential: str | None = None
    credentialType: str = "password"

    def __post_init__(self) -> None:
        if self.credentialType != "password":
            raise ValueError("ffl-datachannel supports only password ICE credentials")

    def _native_urls(self) -> list[str]:
        urls = [self.urls] if isinstance(self.urls, str) else list(self.urls)
        if self.username is None and self.credential is None:
            return urls

        if self.username is None or self.credential is None:
            raise ValueError("TURN username and credential must be supplied together")

        return [self._with_credentials(url) for url in urls]

    def _with_credentials(self, url: str) -> str:
        if not url.lower().startswith(("turn:", "turns:")):
            return url

        scheme, remainder = url.split(":", 1)
        remainder = remainder.removeprefix("//")
        if "@" in remainder:
            return url

        username = quote(self.username, safe="")
        credential = quote(self.credential, safe="")
        return f"{scheme}:{username}:{credential}@{remainder}"


@dataclass(slots=True)
class RTCConfiguration:
    iceServers: list[RTCIceServer] = field(default_factory=list)

    def _native_urls(self) -> list[str]:
        native_urls: list[str] = []
        for ice_server in self.iceServers:
            native_urls.extend(ice_server._native_urls())

        return native_urls

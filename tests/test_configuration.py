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

from ffl_datachannel import RTCConfiguration, RTCIceServer


def test_native_urls_match_aiortc_single_stun_server_selection():
    config = RTCConfiguration(iceServers=[RTCIceServer(urls=["stun:a.example", "stun:b.example"])])
    assert config._native_urls() == ["stun:a.example"]


def test_turn_credentials_are_encoded_into_native_uri():
    server = RTCIceServer(
        urls="turn:relay.example:3478",
        username="a:b",
        credential="c@d",
    )
    assert server._native_urls() == ["turn:a%3Ab:c%40d@relay.example:3478"]

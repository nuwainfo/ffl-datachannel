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

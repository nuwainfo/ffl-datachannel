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

import pytest

from ffl_datachannel.sdp import RTCIceCandidate, candidate_from_sdp


def test_candidate_round_trip_matches_aiortc_signaling_shape():
    line = "1 1 UDP 2122260223 192.0.2.1 54321 typ host"
    candidate = candidate_from_sdp(line)
    assert candidate.to_sdp() == line
    assert candidate._native_sdp() == f"candidate:{line}"


def test_candidate_normalizes_browser_prefixes():
    candidate = RTCIceCandidate("a=candidate:1 1 UDP 1 192.0.2.1 9 typ host")
    assert candidate.to_sdp().startswith("1 1 UDP")


def test_invalid_candidate_fails_fast():
    with pytest.raises(ValueError):
        candidate_from_sdp("not enough fields")

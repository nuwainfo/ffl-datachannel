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

from dataclasses import dataclass


@dataclass(slots=True)
class RTCIceCandidate:
    candidate: str
    sdpMid: str | None = None
    sdpMLineIndex: int | None = None

    def __post_init__(self):
        self.candidate = self._normalize(self.candidate)

    @staticmethod
    def _normalize(candidate: str) -> str:
        value = candidate.strip()
        if value.startswith("a="):
            value = value[2:]
        if value.startswith("candidate:"):
            value = value[len("candidate:"):]
        return value

    def to_sdp(self) -> str:
        return self.candidate

    def _native_sdp(self) -> str:
        return f"candidate:{self.candidate}"


@dataclass(frozen=True, slots=True)
class RTCIceCandidateEvent:
    candidate: RTCIceCandidate | None


def candidate_to_sdp(candidate: RTCIceCandidate) -> str:
    """Serialize an ICE candidate using aiortc's module-level API shape."""
    if not isinstance(candidate, RTCIceCandidate):
        raise TypeError("candidate_to_sdp expects RTCIceCandidate")

    return candidate.to_sdp()


def candidate_from_sdp(sdp: str) -> RTCIceCandidate:
    candidate = RTCIceCandidate(sdp)
    if len(candidate.candidate.split()) < 8:
        raise ValueError("Invalid ICE candidate SDP")

    return candidate

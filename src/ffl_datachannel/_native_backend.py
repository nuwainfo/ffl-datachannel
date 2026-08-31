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

from enum import IntEnum
from typing import Any, Callable

try:
    from . import _ffl_datachannel as native
except ImportError:
    # Some embedded builds expose the extension as a top-level CPython builtin
    # rather than as an installed ffl_datachannel submodule.
    import _ffl_datachannel as native


class NativeEventType(IntEnum):
    LOCAL_DESCRIPTION = native.EVENT_LOCAL_DESCRIPTION
    LOCAL_CANDIDATE = native.EVENT_LOCAL_CANDIDATE
    CONNECTION_STATE = native.EVENT_CONNECTION_STATE
    ICE_STATE = native.EVENT_ICE_STATE
    GATHERING_STATE = native.EVENT_GATHERING_STATE
    SIGNALING_STATE = native.EVENT_SIGNALING_STATE
    DATA_CHANNEL = native.EVENT_DATA_CHANNEL
    CHANNEL_OPEN = native.EVENT_CHANNEL_OPEN
    CHANNEL_CLOSED = native.EVENT_CHANNEL_CLOSED
    CHANNEL_ERROR = native.EVENT_CHANNEL_ERROR
    CHANNEL_TEXT = native.EVENT_CHANNEL_TEXT
    CHANNEL_BINARY = native.EVENT_CHANNEL_BINARY
    CHANNEL_BUFFERED_AMOUNT_LOW = native.EVENT_CHANNEL_BUFFERED_AMOUNT_LOW
    INTERNAL_ERROR = native.EVENT_INTERNAL_ERROR


class NativePeerConnection:
    def __init__(self, ice_servers: list[str], callback: Callable[[int, tuple[Any, ...]], None]):
        self._handle = native.create_peer_connection(ice_servers, callback)
        self._closed = False

    def create_offer(self) -> str:
        return native.create_offer(self._handle)

    def create_answer(self) -> str:
        return native.create_answer(self._handle)

    def set_local_description(self, description_type: str) -> None:
        native.set_local_description(self._handle, description_type)

    def set_remote_description(self, sdp: str, description_type: str) -> None:
        native.set_remote_description(self._handle, sdp, description_type)

    def add_remote_candidate(self, candidate: str, mid: str) -> None:
        native.add_remote_candidate(self._handle, candidate, mid)

    def create_data_channel(
        self,
        label: str,
        ordered: bool,
        protocol: str,
        negotiated: bool,
        max_packet_lifetime: int | None,
        max_retransmits: int | None,
        stream_id: int | None,
    ) -> int:
        return native.create_data_channel(
            self._handle,
            label,
            ordered,
            protocol,
            negotiated,
            -1 if max_packet_lifetime is None else max_packet_lifetime,
            -1 if max_retransmits is None else max_retransmits,
            -1 if stream_id is None else stream_id,
        )

    def send(self, channel_id: int, data: str | bytes | bytearray | memoryview) -> None:
        native.send_message(self._handle, channel_id, data)

    def close_channel(self, channel_id: int) -> None:
        native.close_channel(self._handle, channel_id)

    def get_data_channel_stream(self, channel_id: int) -> int:
        return native.get_data_channel_stream(self._handle, channel_id)

    def get_buffered_amount(self, channel_id: int) -> int:
        return native.get_buffered_amount(self._handle, channel_id)

    def set_buffered_amount_low_threshold(self, channel_id: int, amount: int) -> None:
        native.set_buffered_amount_low_threshold(self._handle, channel_id, amount)

    def close(self) -> None:
        if self._closed:
            return
        native.close_peer_connection(self._handle)
        self._closed = True


NativeError = native.NativeError

RTC_NEW = native.RTC_NEW
RTC_CONNECTING = native.RTC_CONNECTING
RTC_CONNECTED = native.RTC_CONNECTED
RTC_DISCONNECTED = native.RTC_DISCONNECTED
RTC_FAILED = native.RTC_FAILED
RTC_CLOSED = native.RTC_CLOSED

RTC_ICE_NEW = native.RTC_ICE_NEW
RTC_ICE_CHECKING = native.RTC_ICE_CHECKING
RTC_ICE_CONNECTED = native.RTC_ICE_CONNECTED
RTC_ICE_COMPLETED = native.RTC_ICE_COMPLETED
RTC_ICE_FAILED = native.RTC_ICE_FAILED
RTC_ICE_DISCONNECTED = native.RTC_ICE_DISCONNECTED
RTC_ICE_CLOSED = native.RTC_ICE_CLOSED

RTC_GATHERING_NEW = native.RTC_GATHERING_NEW
RTC_GATHERING_INPROGRESS = native.RTC_GATHERING_INPROGRESS
RTC_GATHERING_COMPLETE = native.RTC_GATHERING_COMPLETE

RTC_SIGNALING_STABLE = native.RTC_SIGNALING_STABLE
RTC_SIGNALING_HAVE_LOCAL_OFFER = native.RTC_SIGNALING_HAVE_LOCAL_OFFER
RTC_SIGNALING_HAVE_REMOTE_OFFER = native.RTC_SIGNALING_HAVE_REMOTE_OFFER
RTC_SIGNALING_HAVE_LOCAL_PRANSWER = native.RTC_SIGNALING_HAVE_LOCAL_PRANSWER
RTC_SIGNALING_HAVE_REMOTE_PRANSWER = native.RTC_SIGNALING_HAVE_REMOTE_PRANSWER

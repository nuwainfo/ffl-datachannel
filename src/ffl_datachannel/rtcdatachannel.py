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

from typing import TYPE_CHECKING

from ._events import EventEmitter

if TYPE_CHECKING:
    from .rtcpeerconnection import RTCPeerConnection


class RTCDataChannel(EventEmitter):
    def __init__(
        self,
        peer_connection: RTCPeerConnection,
        channel_id: int,
        label: str,
        *,
        ordered: bool = True,
        protocol: str = "",
        negotiated: bool = False,
        max_packet_lifetime: int | None = None,
        max_retransmits: int | None = None,
        stream_id: int | None = None,
    ):
        super().__init__(peer_connection._loop)
        self._peer_connection = peer_connection
        self._channel_id = channel_id
        self._stream_id = stream_id
        self._ready_state = "connecting"
        self._buffered_amount = 0
        self._buffered_amount_low_threshold = 0
        self._buffered_amount_reconcile_scheduled = False

        self.label = label
        self.ordered = ordered
        self.protocol = protocol
        self.negotiated = negotiated
        self.maxPacketLifeTime = max_packet_lifetime
        self.maxRetransmits = max_retransmits

    @property
    def id(self) -> int | None:
        return self._stream_id

    @property
    def readyState(self) -> str:
        return self._ready_state

    @property
    def bufferedAmount(self) -> int:
        return self._buffered_amount

    @property
    def bufferedAmountLowThreshold(self) -> int:
        return self._buffered_amount_low_threshold

    @bufferedAmountLowThreshold.setter
    def bufferedAmountLowThreshold(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("bufferedAmountLowThreshold cannot be negative")

        self._peer_connection._native.set_buffered_amount_low_threshold(self._channel_id, amount)
        self._buffered_amount_low_threshold = amount

    def send(self, data: str | bytes | bytearray | memoryview) -> None:
        if self.readyState != "open":
            raise RuntimeError("DataChannel is not open")

        dataSize = self._data_size(data)
        self._buffered_amount += dataSize
        try:
            # Keep the native fast path synchronous.  The compatibility
            # accounting above only affects what Python observers see.
            self._peer_connection._native.send(self._channel_id, data)
        except Exception:
            # A failed send was never queued, so do not expose it through
            # bufferedAmount.  Do not emit bufferedamountlow for rollback.
            self._buffered_amount -= dataSize
            raise

        self._schedule_buffered_amount_reconcile()

    def close(self) -> None:
        if self.readyState in ("closing", "closed"):
            return

        self._ready_state = "closing"
        self._peer_connection._native.close_channel(self._channel_id)

    def _handle_open(self) -> None:
        if self._ready_state == "open":
            return

        if self._stream_id is None:
            self._stream_id = self._peer_connection._native.get_data_channel_stream(self._channel_id)

        self._ready_state = "open"
        self._emit_threadsafe("open")

    def _handle_closed(self) -> None:
        if self._ready_state == "closed":
            return

        self._ready_state = "closed"
        self._emit_threadsafe("close")

    def _handle_error(self, message: str) -> None:
        self._emit_threadsafe("error", RuntimeError(message))

    def _handle_message(self, data: str | bytes) -> None:
        self._emit_threadsafe("message", data)

    @staticmethod
    def _data_size(data: str | bytes | bytearray | memoryview) -> int:
        # Match aiortc's accounting: strings are measured as UTF-8 bytes and
        # empty DataChannel messages consume one byte in the SCTP user-data
        # representation used by aiortc.
        if isinstance(data, str):
            size = len(data.encode("utf8"))
        elif isinstance(data, memoryview):
            size = data.nbytes
        else:
            size = len(data)
            
        return size if size else 1

    def _schedule_buffered_amount_reconcile(self) -> None:
        # Coalesce an arbitrary burst of send() calls into one event-loop
        # reconciliation.  This preserves the native direct-send fast path and
        # avoids one Python callback / wakeup per message.
        if self._buffered_amount_reconcile_scheduled:
            return

        self._buffered_amount_reconcile_scheduled = True
        self._peer_connection._loop.call_soon(self._reconcile_buffered_amount)

    def _set_buffered_amount(self, amount: int) -> None:
        previous = self._buffered_amount
        self._buffered_amount = amount
        if (
            previous > self._buffered_amount_low_threshold
            and amount <= self._buffered_amount_low_threshold
        ):
            self._emit_threadsafe("bufferedamountlow")

    def _reconcile_buffered_amount(self) -> None:
        self._buffered_amount_reconcile_scheduled = False
        if self._ready_state == "closed":
            return

        nativeAmount = self._peer_connection._native.get_buffered_amount(self._channel_id)
        self._set_buffered_amount(nativeAmount)

    def _handle_buffered_amount_low(self) -> None:
        if self._ready_state == "closed":
            return

        # libdatachannel reports an edge when its own channel queue crosses
        # the native threshold.  Reconcile the compatibility counter instead
        # of forwarding that edge blindly, so direct-send and queued-send
        # paths share the same aiortc-style observable semantics.
        nativeAmount = self._peer_connection._native.get_buffered_amount(self._channel_id)
        self._set_buffered_amount(nativeAmount)

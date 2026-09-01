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

import asyncio
import weakref

from dataclasses import dataclass
from typing import Any

from pyee.asyncio import AsyncIOEventEmitter

from . import _mdns
from ._native_backend import (
    NativeError,
    NativeEventType,
    NativePeerConnection,
    RTC_CLOSED,
    RTC_CONNECTED,
    RTC_CONNECTING,
    RTC_DISCONNECTED,
    RTC_FAILED,
    RTC_GATHERING_COMPLETE,
    RTC_GATHERING_INPROGRESS,
    RTC_GATHERING_NEW,
    RTC_ICE_CHECKING,
    RTC_ICE_CLOSED,
    RTC_ICE_COMPLETED,
    RTC_ICE_CONNECTED,
    RTC_ICE_DISCONNECTED,
    RTC_ICE_FAILED,
    RTC_ICE_NEW,
    RTC_NEW,
    RTC_SIGNALING_HAVE_LOCAL_OFFER,
    RTC_SIGNALING_HAVE_LOCAL_PRANSWER,
    RTC_SIGNALING_HAVE_REMOTE_OFFER,
    RTC_SIGNALING_HAVE_REMOTE_PRANSWER,
    RTC_SIGNALING_STABLE,
)
from .rtcconfiguration import RTCConfiguration
from .rtcdatachannel import RTCDataChannel
from .rtcsessiondescription import RTCSessionDescription
from .sdp import RTCIceCandidate, RTCIceCandidateEvent


@dataclass(slots=True)
class _CompatibilityTransport:
    _browser_hint: str | None = None


@dataclass(slots=True)
class _CompatibilitySctp:
    transport: _CompatibilityTransport


class RTCPeerConnection(AsyncIOEventEmitter):
    _CONNECTION_STATES = {
        RTC_NEW: "new",
        RTC_CONNECTING: "connecting",
        RTC_CONNECTED: "connected",
        RTC_DISCONNECTED: "disconnected",
        RTC_FAILED: "failed",
        RTC_CLOSED: "closed",
    }
    _ICE_STATES = {
        RTC_ICE_NEW: "new",
        RTC_ICE_CHECKING: "checking",
        RTC_ICE_CONNECTED: "connected",
        RTC_ICE_COMPLETED: "completed",
        RTC_ICE_FAILED: "failed",
        RTC_ICE_DISCONNECTED: "disconnected",
        RTC_ICE_CLOSED: "closed",
    }
    _GATHERING_STATES = {
        RTC_GATHERING_NEW: "new",
        RTC_GATHERING_INPROGRESS: "gathering",
        RTC_GATHERING_COMPLETE: "complete",
    }
    _SIGNALING_STATES = {
        RTC_SIGNALING_STABLE: "stable",
        RTC_SIGNALING_HAVE_LOCAL_OFFER: "have-local-offer",
        RTC_SIGNALING_HAVE_REMOTE_OFFER: "have-remote-offer",
        RTC_SIGNALING_HAVE_LOCAL_PRANSWER: "have-local-pranswer",
        RTC_SIGNALING_HAVE_REMOTE_PRANSWER: "have-remote-pranswer",
    }

    def __init__(self, configuration: RTCConfiguration | None = None):
        loop = asyncio.get_running_loop()
        super().__init__()
        self._loop = loop
        self._configuration = configuration or RTCConfiguration()
        self._channels: dict[int, RTCDataChannel] = {}
        self._pendingChannelEvents: dict[int, list[tuple[NativeEventType, tuple[Any, ...]]]] = {}
        self._localDescriptionWaiter: asyncio.Future[RTCSessionDescription] | None = None
        self._gatheringCompleteWaiter: asyncio.Future[None] | None = None
        self._closed = False
        self.localDescription: RTCSessionDescription | None = None
        self.remoteDescription: RTCSessionDescription | None = None
        self.connectionState = "new"
        self.iceConnectionState = "new"
        self.iceGatheringState = "new"
        self.signalingState = "stable"
        self.sctp = _CompatibilitySctp(_CompatibilityTransport())
        self._eventHandlers = {
            NativeEventType.LOCAL_DESCRIPTION: self._handle_local_description,
            NativeEventType.LOCAL_CANDIDATE: self._handle_local_candidate,
            NativeEventType.CONNECTION_STATE: self._handle_connection_state,
            NativeEventType.ICE_STATE: self._handle_ice_state,
            NativeEventType.GATHERING_STATE: self._handle_gathering_state,
            NativeEventType.SIGNALING_STATE: self._handle_signaling_state,
            NativeEventType.DATA_CHANNEL: self._handle_data_channel,
            NativeEventType.CHANNEL_OPEN: self._handle_channel_open,
            NativeEventType.CHANNEL_CLOSED: self._handle_channel_closed,
            NativeEventType.CHANNEL_ERROR: self._handle_channel_error,
            NativeEventType.CHANNEL_TEXT: self._handle_channel_text,
            NativeEventType.CHANNEL_BINARY: self._handle_channel_binary,
            NativeEventType.CHANNEL_BUFFERED_AMOUNT_LOW: self._handle_channel_buffered_amount_low,
            NativeEventType.INTERNAL_ERROR: self._handle_internal_error,
        }
        self._nativeCallback = self._create_native_callback()
        self._native = NativePeerConnection(self._configuration._native_urls(), self._nativeCallback)

    def _create_native_callback(self):
        selfReference = weakref.ref(self)

        def native_callback(event_type: int, payload: tuple[Any, ...]) -> None:
            peerConnection = selfReference()
            if peerConnection is not None:
                peerConnection._handle_native_event(event_type, payload)

        return native_callback

    async def createOffer(self) -> RTCSessionDescription:
        return RTCSessionDescription(sdp=self._native.create_offer(), type="offer")

    async def createAnswer(self) -> RTCSessionDescription:
        return RTCSessionDescription(sdp=self._native.create_answer(), type="answer")

    async def setLocalDescription(self, description: RTCSessionDescription) -> None:
        if self._localDescriptionWaiter is not None and not self._localDescriptionWaiter.done():
            raise RuntimeError("A local description operation is already pending")

        waiter = self._loop.create_future()
        self._localDescriptionWaiter = waiter
        try:
            self._native.set_local_description(description.type)
            await waiter

            # Match aiortc: complete ICE gathering before exposing the local
            # SDP, so callers which exchange only offer/answer SDP receive all
            # candidates without requiring a separate trickle-ICE path.
            if self.iceGatheringState != "complete":
                gatheringCompleteWaiter = self._loop.create_future()
                self._gatheringCompleteWaiter = gatheringCompleteWaiter
                await gatheringCompleteWaiter

            self._refresh_local_description()
        finally:
            self._localDescriptionWaiter = None
            self._gatheringCompleteWaiter = None

    async def setRemoteDescription(self, description: RTCSessionDescription) -> None:
        self._native.set_remote_description(description.sdp, description.type)
        self.remoteDescription = description

    async def addIceCandidate(self, candidate: RTCIceCandidate | dict[str, Any] | None) -> None:
        if candidate is None:
            return

        resolvedCandidate = self._coerce_candidate(candidate)
        if not resolvedCandidate.candidate:
            return

        resolvedCandidate = await self._resolve_mdns_candidate(resolvedCandidate)
        if resolvedCandidate is None:
            return

        self._native.add_remote_candidate(
            resolvedCandidate._native_sdp(),
            resolvedCandidate.sdpMid or "",
        )

    @staticmethod
    async def _resolve_mdns_candidate(candidate: RTCIceCandidate) -> RTCIceCandidate | None:
        fields = candidate.candidate.split()
        if len(fields) < 6 or not fields[4].lower().endswith(".local"):
            return candidate

        address = await _mdns.resolve(fields[4])
        if address is None:
            return None

        fields[4] = address
        return RTCIceCandidate(
            " ".join(fields),
            sdpMid=candidate.sdpMid,
            sdpMLineIndex=candidate.sdpMLineIndex,
        )

    def createDataChannel(
        self,
        label: str,
        maxPacketLifeTime: int | None = None,
        maxRetransmits: int | None = None,
        ordered: bool = True,
        protocol: str = "",
        negotiated: bool = False,
        id: int | None = None,
    ) -> RTCDataChannel:
        if maxPacketLifeTime is not None and maxRetransmits is not None:
            raise ValueError("Cannot specify both maxPacketLifeTime and maxRetransmits")
        if maxPacketLifeTime is not None and maxPacketLifeTime < 0:
            raise ValueError("maxPacketLifeTime cannot be negative")
        if maxRetransmits is not None and maxRetransmits < 0:
            raise ValueError("maxRetransmits cannot be negative")
        if id is not None and not 0 <= id <= 65534:
            raise ValueError("DataChannel id must be between 0 and 65534")
        if negotiated and id is None:
            raise ValueError("A negotiated DataChannel requires an explicit id")

        channelId = self._native.create_data_channel(
            label,
            ordered,
            protocol,
            negotiated,
            maxPacketLifeTime,
            maxRetransmits,
            id,
        )
        channel = RTCDataChannel(
            self,
            channelId,
            label,
            ordered=ordered,
            protocol=protocol,
            negotiated=negotiated,
            max_packet_lifetime=maxPacketLifeTime,
            max_retransmits=maxRetransmits,
            stream_id=id,
        )
        self._channels[channelId] = channel
        return channel

    async def close(self) -> None:
        if self.connectionState == "closed":
            return
        self._closed = True
        self._native.close()
        self.connectionState = "closed"
        self.iceConnectionState = "closed"
        for channel in self._channels.values():
            channel._handle_closed()
            
        self.emit("connectionstatechange")
        self.remove_all_listeners()

    @staticmethod
    def _coerce_candidate(candidate: RTCIceCandidate | dict[str, Any]) -> RTCIceCandidate:
        if isinstance(candidate, RTCIceCandidate):
            return candidate
        if not isinstance(candidate, dict):
            raise TypeError("candidate must be RTCIceCandidate, dict, or None")

        candidateLine = candidate.get("candidate")
        if not candidateLine or candidateLine == "end-of-candidates":
            return RTCIceCandidate("")
        return RTCIceCandidate(
            candidateLine,
            sdpMid=candidate.get("sdpMid"),
            sdpMLineIndex=candidate.get("sdpMLineIndex"),
        )

    def _handle_native_event(self, event_type: int, payload: tuple[Any, ...]) -> None:
        self._loop.call_soon_threadsafe(self._dispatch_native_event, NativeEventType(event_type), payload)

    def _dispatch_native_event(self, eventType: NativeEventType, payload: tuple[Any, ...]) -> None:
        if self._closed:
            return

        if eventType in (
            NativeEventType.CHANNEL_OPEN,
            NativeEventType.CHANNEL_CLOSED,
            NativeEventType.CHANNEL_ERROR,
            NativeEventType.CHANNEL_TEXT,
            NativeEventType.CHANNEL_BINARY,
            NativeEventType.CHANNEL_BUFFERED_AMOUNT_LOW,
        ):
            channelId = int(payload[0])
            if channelId not in self._channels:
                self._pendingChannelEvents.setdefault(channelId, []).append((eventType, payload))
                return

        self._eventHandlers[eventType](*payload)

    def _handle_local_description(self, sdp: str, descriptionType: str) -> None:
        description = RTCSessionDescription(sdp=sdp, type=descriptionType)
        self.localDescription = description
        waiter = self._localDescriptionWaiter
        if waiter is not None and not waiter.done():
            waiter.set_result(description)

    def _refresh_local_description(self) -> None:
        if self.localDescription is None:
            return

        self.localDescription = RTCSessionDescription(
            sdp=self._native.get_local_description(),
            type=self.localDescription.type,
        )

    def _handle_local_candidate(self, candidate: str, mid: str) -> None:
        self._refresh_local_description()
        iceCandidate = RTCIceCandidate(candidate, sdpMid=mid or None, sdpMLineIndex=0)
        self.emit("icecandidate", RTCIceCandidateEvent(iceCandidate))

    def _handle_connection_state(self, state: int) -> None:
        self.connectionState = self._CONNECTION_STATES[state]
        self.emit("connectionstatechange")

    def _handle_ice_state(self, state: int) -> None:
        self.iceConnectionState = self._ICE_STATES[state]
        self.emit("iceconnectionstatechange")

    def _handle_gathering_state(self, state: int) -> None:
        self.iceGatheringState = self._GATHERING_STATES[state]
        self.emit("icegatheringstatechange")
        
        if state == RTC_GATHERING_COMPLETE:
            self._refresh_local_description()
            waiter = self._gatheringCompleteWaiter
            if waiter is not None and not waiter.done():
                waiter.set_result(None)
                
            self.emit("icecandidate", RTCIceCandidateEvent(None))

    def _handle_signaling_state(self, state: int) -> None:
        self.signalingState = self._SIGNALING_STATES[state]
        self.emit("signalingstatechange")

    def _handle_data_channel(
        self,
        channelId: int,
        streamId: int,
        label: str,
        protocol: str,
        ordered: int,
        unreliable: int,
        maxPacketLifeTime: int,
        maxRetransmits: int,
    ) -> None:
        if channelId in self._channels:
            return

        packetLifetime = maxPacketLifeTime if unreliable and maxPacketLifeTime > 0 else None
        retransmits = maxRetransmits if unreliable and packetLifetime is None else None
        channel = RTCDataChannel(
            self,
            channelId,
            label,
            ordered=bool(ordered),
            protocol=protocol,
            negotiated=False,
            max_packet_lifetime=packetLifetime,
            max_retransmits=retransmits,
            stream_id=streamId,
        )
        self._channels[channelId] = channel
        self.emit("datachannel", channel)

        pendingEvents = self._pendingChannelEvents.pop(channelId, ())
        for eventType, payload in pendingEvents:
            self._eventHandlers[eventType](*payload)

    def _channel(self, channelId: int) -> RTCDataChannel:
        try:
            return self._channels[channelId]
        except KeyError as error:
            raise RuntimeError(f"Unknown native DataChannel id {channelId}") from error

    def _handle_channel_open(self, channelId: int) -> None:
        self._channel(channelId)._handle_open()

    def _handle_channel_closed(self, channelId: int) -> None:
        self._channel(channelId)._handle_closed()

    def _handle_channel_error(self, channelId: int, message: str) -> None:
        self._channel(channelId)._handle_error(message)

    def _handle_channel_text(self, channelId: int, message: str) -> None:
        self._channel(channelId)._handle_message(message)

    def _handle_channel_binary(self, channelId: int, data: bytes) -> None:
        self._channel(channelId)._handle_message(data)

    def _handle_channel_buffered_amount_low(self, channelId: int) -> None:
        self._channel(channelId)._handle_buffered_amount_low()

    def _handle_internal_error(self, message: str) -> None:
        raise NativeError(message)

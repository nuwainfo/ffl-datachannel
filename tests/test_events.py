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

import asyncio
from types import SimpleNamespace

import pytest

from pyee.asyncio import AsyncIOEventEmitter

from ffl_datachannel._native_backend import NativeEventType, RTC_CONNECTED
from ffl_datachannel.rtcdatachannel import RTCDataChannel
from ffl_datachannel.rtcpeerconnection import RTCPeerConnection


@pytest.mark.asyncio
async def test_data_channel_supports_sync_async_and_once_handlers():
    channel = RTCDataChannel(
        SimpleNamespace(_loop=asyncio.get_running_loop()),
        1,
        "events",
        stream_id=1,
    )
    received = []
    async_received = asyncio.Event()

    @channel.on("message")
    def sync_handler(value):
        received.append(("sync", value))

    @channel.on("message")
    async def async_handler(value):
        await asyncio.sleep(0)
        received.append(("async", value))
        async_received.set()

    opened = []
    channel.once("open", lambda: opened.append("open"))

    channel._handle_open()
    channel._handle_open()
    channel._handle_message("hello")

    # Synchronous callbacks run as part of emit(), matching aiortc / pyee.
    assert received == [("sync", "hello")]
    await asyncio.wait_for(async_received.wait(), timeout=1)
    assert sorted(received) == [("async", "hello"), ("sync", "hello")]
    assert opened == ["open"]


@pytest.mark.asyncio
async def test_native_events_are_emitted_on_the_owning_event_loop():
    loop = asyncio.get_running_loop()
    peer = object.__new__(RTCPeerConnection)
    AsyncIOEventEmitter.__init__(peer)
    peer._loop = loop
    peer._eventHandlers = {
        NativeEventType.CONNECTION_STATE: peer._handle_connection_state,
    }

    handled = asyncio.Event()
    observed_loop = None

    @peer.on("connectionstatechange")
    def on_connection_state_change():
        nonlocal observed_loop
        observed_loop = asyncio.get_running_loop()
        assert peer.connectionState == "connected"
        handled.set()

    await asyncio.to_thread(
        peer._handle_native_event,
        NativeEventType.CONNECTION_STATE,
        (RTC_CONNECTED,),
    )
    await asyncio.wait_for(handled.wait(), timeout=1)

    assert observed_loop is loop


@pytest.mark.asyncio
async def test_closed_data_channel_releases_listeners_after_close_event():
    channel = RTCDataChannel(
        SimpleNamespace(_loop=asyncio.get_running_loop()),
        1,
        "events",
        stream_id=1,
    )
    closed = []
    messages = []
    channel.on("close", lambda: closed.append(True))
    channel.on("message", messages.append)

    channel._handle_closed()
    channel._handle_message("ignored")

    assert closed == [True]
    assert messages == []
    assert channel.event_names() == set()

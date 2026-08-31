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

import os
import sys
import types


def _install_fake_native() -> None:
    native = types.ModuleType("ffl_datachannel._ffl_datachannel")
    native.NativeError = RuntimeError

    constantNames = [
        "EVENT_LOCAL_DESCRIPTION",
        "EVENT_LOCAL_CANDIDATE",
        "EVENT_CONNECTION_STATE",
        "EVENT_ICE_STATE",
        "EVENT_GATHERING_STATE",
        "EVENT_SIGNALING_STATE",
        "EVENT_DATA_CHANNEL",
        "EVENT_CHANNEL_OPEN",
        "EVENT_CHANNEL_CLOSED",
        "EVENT_CHANNEL_ERROR",
        "EVENT_CHANNEL_TEXT",
        "EVENT_CHANNEL_BINARY",
        "EVENT_CHANNEL_BUFFERED_AMOUNT_LOW",
        "EVENT_INTERNAL_ERROR",
    ]
    for index, name in enumerate(constantNames, start=1):
        setattr(native, name, index)

    stateValues = {
        "RTC_NEW": 0,
        "RTC_CONNECTING": 1,
        "RTC_CONNECTED": 2,
        "RTC_DISCONNECTED": 3,
        "RTC_FAILED": 4,
        "RTC_CLOSED": 5,
        "RTC_ICE_NEW": 0,
        "RTC_ICE_CHECKING": 1,
        "RTC_ICE_CONNECTED": 2,
        "RTC_ICE_COMPLETED": 3,
        "RTC_ICE_FAILED": 4,
        "RTC_ICE_DISCONNECTED": 5,
        "RTC_ICE_CLOSED": 6,
        "RTC_GATHERING_NEW": 0,
        "RTC_GATHERING_INPROGRESS": 1,
        "RTC_GATHERING_COMPLETE": 2,
        "RTC_SIGNALING_STABLE": 0,
        "RTC_SIGNALING_HAVE_LOCAL_OFFER": 1,
        "RTC_SIGNALING_HAVE_REMOTE_OFFER": 2,
        "RTC_SIGNALING_HAVE_LOCAL_PRANSWER": 3,
        "RTC_SIGNALING_HAVE_REMOTE_PRANSWER": 4,
    }
    for name, value in stateValues.items():
        setattr(native, name, value)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Compiled _ffl_datachannel extension is required for this operation")

    for name in (
        "create_peer_connection",
        "close_peer_connection",
        "create_offer",
        "create_answer",
        "set_local_description",
        "set_remote_description",
        "add_remote_candidate",
        "create_data_channel",
        "send_message",
        "close_channel",
        "get_data_channel_stream",
        "get_buffered_amount",
        "set_buffered_amount_low_threshold",
    ):
        setattr(native, name, unavailable)

    sys.modules[native.__name__] = native


if os.environ.get("FFL_DATACHANNEL_REQUIRE_NATIVE") != "1":
    _install_fake_native()

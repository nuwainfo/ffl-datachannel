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

import pytest

from ffl_datachannel._events import EventEmitter


@pytest.mark.asyncio
async def test_decorator_and_direct_handlers_share_one_dispatch_path():
    loop = asyncio.get_running_loop()
    emitter = EventEmitter(loop)
    values = []
    done = asyncio.Event()

    @emitter.on("value")
    async def async_handler(value):
        values.append(("async", value))
        if len(values) == 2:
            done.set()

    def sync_handler(value):
        values.append(("sync", value))
        if len(values) == 2:
            done.set()

    emitter.on("value", sync_handler)
    emitter._emit_threadsafe("value", 7)
    await asyncio.wait_for(done.wait(), timeout=1)

    assert sorted(values) == [("async", 7), ("sync", 7)]

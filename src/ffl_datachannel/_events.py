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
import inspect

from collections import defaultdict
from collections.abc import Callable
from typing import Any


class EventEmitter:
    """Dispatches callbacks onto the asyncio loop that owns the object."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def on(self, event: str, callback: Callable[..., Any] | None = None):
        if callback is None:
            def decorator(handler: Callable[..., Any]):
                self._handlers[event].append(handler)
                return handler

            return decorator

        self._handlers[event].append(callback)
        return callback

    def _emit_threadsafe(self, event: str, *args: Any) -> None:
        for handler in tuple(self._handlers.get(event, ())):
            self._loop.call_soon_threadsafe(self._invoke_handler, handler, args)

    def _invoke_handler(self, handler: Callable[..., Any], args: tuple[Any, ...]) -> None:
        result = handler(*args)
        if inspect.isawaitable(result):
            self._loop.create_task(result)

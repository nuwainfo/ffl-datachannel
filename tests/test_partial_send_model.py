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

"""Model regression tests for libdatachannel partial SCTP send semantics.

These tests exercise the queue-head/offset invariants without requiring a
native libdatachannel build. The separate integration test verifies the real
rebuilt ffl_datachannel backend end-to-end.
"""

from collections import deque
from dataclasses import dataclass

BLOCK = object()


@dataclass(frozen=True)
class Message:
    ident: str
    payload: bytes


class FakeSendv:
    def __init__(self, results):
        self.results = deque(results)
        self.calls = []

    def __call__(self, message, offset):
        remaining = len(message.payload) - offset
        self.calls.append((message.ident, offset, remaining))
        result = self.results.popleft()
        if result is BLOCK:
            return 0
        if result < 0 or result > remaining:
            raise AssertionError(
                f"invalid fake send result {result} for remaining={remaining}"
            )
        return result


class PartialSendQueue:
    """Small model of the patched SctpTransport queue-head offset contract."""

    def __init__(self, sendv):
        self.sendv = sendv
        self.queue = deque()
        self.head_offset = 0
        self.buffered = 0
        self.completed = []

    def _try_message(self, message, offset):
        if offset > len(message.payload):
            raise AssertionError("offset exceeds message size")

        accepted = self.sendv(message, offset)
        if accepted == 0:
            return False, offset

        offset += accepted
        return offset == len(message.payload), offset

    def flush(self):
        while self.queue:
            message = self.queue[0]
            before = self.head_offset
            complete, self.head_offset = self._try_message(
                message, self.head_offset
            )
            self.buffered -= self.head_offset - before

            if not complete:
                return False

            self.completed.append(message.ident)
            self.head_offset = 0
            self.queue.popleft()

        return True

    def send(self, message):
        direct_offset = 0
        queue_empty = self.flush()

        if queue_empty:
            complete, direct_offset = self._try_message(message, 0)
            if complete:
                self.completed.append(message.ident)
                return True

        self.queue.append(message)

        if queue_empty:
            self.head_offset = direct_offset
            self.buffered += len(message.payload) - direct_offset
        else:
            self.buffered += len(message.payload)

        return False


def test_direct_short_write_queues_only_unsent_suffix():
    sendv = FakeSendv([4])
    q = PartialSendQueue(sendv)

    assert q.send(Message("A", b"abcdefghij")) is False
    assert q.head_offset == 4
    assert q.buffered == 6
    assert [m.ident for m in q.queue] == ["A"]
    assert sendv.calls == [("A", 0, 10)]


def test_block_then_short_write_updates_buffered_by_actual_accepted_bytes():
    sendv = FakeSendv([4, BLOCK, 3])
    q = PartialSendQueue(sendv)
    q.send(Message("A", b"abcdefghij"))

    assert q.flush() is False
    assert q.head_offset == 4
    assert q.buffered == 6

    assert q.flush() is False
    assert q.head_offset == 7
    assert q.buffered == 3


def test_next_message_never_overtakes_unfinished_head():
    # A accepts 4 bytes. Sending B first tries to flush A and hits backpressure.
    # The next flush finishes A before B is ever passed to sendv.
    sendv = FakeSendv([4, BLOCK, 6, 5])
    q = PartialSendQueue(sendv)

    q.send(Message("A", b"A" * 10))
    q.send(Message("B", b"B" * 5))

    assert [m.ident for m in q.queue] == ["A", "B"]
    assert all(call[0] == "A" for call in sendv.calls)

    assert q.flush() is True
    assert q.completed == ["A", "B"]
    assert q.buffered == 0
    assert sendv.calls[-2:] == [("A", 4, 6), ("B", 0, 5)]


def test_multiple_short_writes_preserve_exact_suffix_offsets():
    sendv = FakeSendv([3, 2, 4, 1])
    q = PartialSendQueue(sendv)
    q.send(Message("A", b"0123456789"))

    assert q.head_offset == 3
    assert q.buffered == 7

    assert q.flush() is False
    assert q.head_offset == 5
    assert q.buffered == 5

    assert q.flush() is False
    assert q.head_offset == 9
    assert q.buffered == 1

    assert q.flush() is True
    assert q.completed == ["A"]
    assert q.buffered == 0
    assert [offset for _, offset, _ in sendv.calls] == [0, 3, 5, 9]


def test_full_direct_write_does_not_enter_queue_or_buffered_amount():
    sendv = FakeSendv([8])
    q = PartialSendQueue(sendv)

    assert q.send(Message("A", b"12345678")) is True
    assert list(q.queue) == []
    assert q.buffered == 0
    assert q.completed == ["A"]


def test_buffered_amount_includes_full_later_message_while_head_is_partial():
    sendv = FakeSendv([4, BLOCK])
    q = PartialSendQueue(sendv)

    q.send(Message("A", b"A" * 10))
    q.send(Message("B", b"B" * 5))

    # A has 6 unsent bytes; B has not been attempted and contributes all 5.
    assert q.head_offset == 4
    assert q.buffered == 11
    assert [m.ident for m in q.queue] == ["A", "B"]

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
import socket
import struct
import time


_MDNS_ADDRESS = "224.0.0.251"
_MDNS_PORT = 5353
_DNS_CLASS_IN = 1
_DNS_TYPE_A = 1
_DNS_TYPE_AAAA = 28


def _encode_name(hostname: str) -> bytes:
    labels = hostname.rstrip(".").split(".")
    if not hostname or any(not label or len(label.encode("idna")) > 63 for label in labels):
        raise ValueError(f"Invalid mDNS hostname: {hostname!r}")
    return b"".join(bytes((len(label.encode("idna")),)) + label.encode("idna") for label in labels) + b"\0"


def _skip_name(packet: bytes, offset: int) -> int:
    while True:
        if offset >= len(packet):
            raise ValueError("Truncated DNS name")
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("Truncated DNS compression pointer")
            return offset + 2
        offset += 1
        if length == 0:
            return offset
        if length & 0xC0 or offset + length > len(packet):
            raise ValueError("Invalid DNS label")
        offset += length


def _decode_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    result_offset: int | None = None
    visited: set[int] = set()
    while True:
        if offset >= len(packet) or offset in visited:
            raise ValueError("Invalid DNS name")
        visited.add(offset)
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("Truncated DNS compression pointer")
            if result_offset is None:
                result_offset = offset + 2
            offset = ((length & 0x3F) << 8) | packet[offset + 1]
            continue
        offset += 1
        if length == 0:
            return ".".join(labels), result_offset or offset
        if length & 0xC0 or offset + length > len(packet):
            raise ValueError("Invalid DNS label")
        labels.append(packet[offset:offset + length].decode("idna"))
        offset += length


def _extract_address(packet: bytes, hostname: str, record_type: int) -> str | None:
    if len(packet) < 12:
        return None

    _, flags, questions, answers, authorities, additionals = struct.unpack_from("!HHHHHH", packet)
    if flags & 0x8000 == 0:
        return None

    try:
        offset = 12
        for _ in range(questions):
            offset = _skip_name(packet, offset) + 4

        for _ in range(answers + authorities + additionals):
            record_name, offset = _decode_name(packet, offset)
            if offset + 10 > len(packet):
                return None
            dns_type, _, _, data_size = struct.unpack_from("!HHIH", packet, offset)
            offset += 10
            if offset + data_size > len(packet):
                return None
            data = packet[offset:offset + data_size]
            offset += data_size
            if (
                record_name.lower() == hostname.rstrip(".").lower()
                and dns_type == record_type
                and data_size == (4 if record_type == _DNS_TYPE_A else 16)
            ):
                family = socket.AF_INET if record_type == _DNS_TYPE_A else socket.AF_INET6
                return socket.inet_ntop(family, data)
    except (ValueError, struct.error, OSError):
        return None
    return None


def _resolve(hostname: str, record_type: int, timeout: float) -> str | None:
    query = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    query += _encode_name(hostname)
    query += struct.pack("!HH", record_type, _DNS_CLASS_IN)

    tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        for sock in (tx_sock, rx_sock):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        tx_sock.bind(("", _MDNS_PORT))
        rx_sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            socket.inet_aton(_MDNS_ADDRESS) + socket.inet_aton("0.0.0.0"),
        )
        rx_sock.bind(("", _MDNS_PORT))
        rx_sock.settimeout(min(timeout, 0.2))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            tx_sock.sendto(query, (_MDNS_ADDRESS, _MDNS_PORT))
            while time.monotonic() < deadline:
                try:
                    packet, _ = rx_sock.recvfrom(4096)
                except TimeoutError:
                    break
                address = _extract_address(packet, hostname, record_type)
                if address:
                    return address
    except OSError:
        return None
    finally:
        tx_sock.close()
        rx_sock.close()
    return None


async def resolve(hostname: str, timeout: float = 1.0) -> str | None:
    """Resolve a browser ICE mDNS hostname without an external dependency."""
    if not hostname.lower().endswith(".local"):
        return None

    for record_type in (_DNS_TYPE_A, _DNS_TYPE_AAAA):
        address = await asyncio.to_thread(_resolve, hostname, record_type, timeout)
        if address:
            return address
    return None

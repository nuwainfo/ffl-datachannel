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

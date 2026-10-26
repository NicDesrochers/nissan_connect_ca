"""Tests de la protection contre les commandes dupliquées."""
import asyncio

from custom_components.nissan_connect_ca.command_guard import guard_command


class _Dummy:
    def __init__(self):
        self.calls = 0

    @guard_command(cooldown=60)
    async def press(self):
        self.calls += 1
        await asyncio.sleep(0)


class _NoCooldown:
    def __init__(self):
        self.calls = 0

    @guard_command(cooldown=0)
    async def run(self):
        self.calls += 1
        await asyncio.sleep(0.01)


async def test_second_call_within_cooldown_is_ignored():
    dummy = _Dummy()
    await dummy.press()
    await dummy.press()
    assert dummy.calls == 1


async def test_concurrent_calls_run_once():
    obj = _NoCooldown()
    await asyncio.gather(obj.run(), obj.run(), obj.run())
    assert obj.calls == 1


async def test_sequential_calls_without_cooldown_all_run():
    obj = _NoCooldown()
    await obj.run()
    await obj.run()
    assert obj.calls == 2


async def test_guard_is_per_instance():
    a, b = _Dummy(), _Dummy()
    await a.press()
    await b.press()
    assert a.calls == 1 and b.calls == 1

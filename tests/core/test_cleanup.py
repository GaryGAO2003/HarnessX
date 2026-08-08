"""Block 2 — cleanup shielded state machine.

Verifies the L2.3b rule 4 behaviour: first call starts the underlying impl
task; caller cancellation cancels only the await, the impl keeps running, and
a retry awaits the SAME task; done() rebuilds on task-level death; resources
(_sandbox) are cleared inside the impl's finally, never before the release
await ends.
"""

import asyncio

import pytest

from harnessx.core.harness import Harness


class _StubSandboxProvider:
    def __init__(self):
        self.releases = 0
        self.block = False

    async def acquire(self):
        return object()

    async def release(self, sandbox):
        self.releases += 1
        if self.block:
            await asyncio.sleep(0.2)  # hold the await so cancellation lands on it


class _StubPlugin:
    name = "stub"

    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


class _StubSubHarness:
    def __init__(self):
        self.cleaned = 0

    async def cleanup(self):
        self.cleaned += 1


def _make_harness(*, block_sandbox=False, plugins=None, sub_harnesses=None):
    """Build a Harness via the real __init__ with stubbed runtime bits.

    Harness.__init__ constructs sub-harnesses and binds processors; we keep the
    config minimal and pre-populate the runtime to control the cleanup surface.
    """
    from harnessx.core.harness import HarnessConfig, _HarnessRuntime
    from harnessx.core.model_config import ModelConfig
    from harnessx.providers.unconfigured import UnConfiguredProvider

    config = HarnessConfig()
    harness = Harness(ModelConfig(main=UnConfiguredProvider()), config)

    # Overwrite the runtime pieces we drive in cleanup tests.
    sp = _StubSandboxProvider()
    sp.block = block_sandbox
    rt = _HarnessRuntime(
        tool_registry=None,
        tracer=None,
        processors={},
        workspace=None,
        sandbox_provider=sp,
        plugins=plugins or [],
    )
    harness._rt = rt
    harness._sandbox = object()
    harness._sub_harnesses = {k: v for k, v in (sub_harnesses or {}).items()}
    return harness


# ── ① normal completion ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_normal_releases_everything():
    from harnessx.core.runtime import _OWNERS

    h = _make_harness(plugins=[_StubPlugin()], sub_harnesses={"s": _StubSubHarness()})
    # Simulate a claimed owner so release_owners actually clears it.
    # (__hx_owner_token is name-mangled to _Harness__hx_owner_token outside the class.)
    from harnessx.core.runtime import claim_owners

    claim_owners([_StubPlugin()], h._Harness__hx_owner_token)

    await h.cleanup()

    assert h._closed
    assert h._sandbox is None
    assert h._rt.sandbox_provider.releases == 1
    assert h._rt.plugins[0].stops == 1
    assert h._sub_harnesses["s"].cleaned == 1
    # owner released: no claims remain for this token
    with _OWNERS_lock():
        assert not any(t is h.__hx_owner_token for _, t in _OWNERS.values())


# helper: read _OWNERS under the lock (module-private, test-only)
def _OWNERS_lock():
    from harnessx.core.runtime import _OWNER_LOCK

    return _OWNER_LOCK


# ── ② caller cancellation → impl continues + retry same task ────────────────


@pytest.mark.asyncio
async def test_cleanup_cancel_then_retry_same_task():
    h = _make_harness(block_sandbox=True)

    task = asyncio.ensure_future(h.cleanup())
    await asyncio.sleep(0.02)          # land inside the blocked release await
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    impl_task = h._cleanup_task
    assert impl_task is not None
    assert not impl_task.cancelled()   # impl keeps running

    await asyncio.sleep(0.25)          # let the impl finish
    assert impl_task.done()

    # Retry awaits the SAME task to completion.
    await h.cleanup()
    assert h._cleanup_task is impl_task
    assert h._closed
    assert h._rt.sandbox_provider.releases == 1


# ── ③ task-level death (impl exception) → done() rebuilds ───────────────────


@pytest.mark.asyncio
async def test_cleanup_task_death_rebuilds():
    h = _make_harness()

    async def boom(self):
        raise RuntimeError("boom")

    h._cleanup_impl = boom.__get__(h, type(h))  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        await h.cleanup()

    dead_task = h._cleanup_task
    assert dead_task.done()

    # Restore real impl; retry rebuilds and succeeds.
    import harnessx.core.harness as harness_mod

    h._cleanup_impl = harness_mod.Harness._cleanup_impl.__get__(h, type(h))
    await h.cleanup()
    assert h._closed
    assert h._cleanup_task is not dead_task  # rebuilt


# ── ④ sandbox reference cleared in impl finally (not before release) ────────


@pytest.mark.asyncio
async def test_cleanup_sandbox_ref_cleared_after_release():
    h = _make_harness(block_sandbox=True)

    task = asyncio.ensure_future(h.cleanup())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # While the impl is still blocked, the reference must NOT be cleared.
    assert h._sandbox is not None

    await asyncio.sleep(0.25)
    assert h._sandbox is None          # cleared only after the release await
    assert h._rt.sandbox_provider.releases == 1


# ── ⑤ tail isolation: release_owners raising still closes ───────────────────


@pytest.mark.asyncio
async def test_cleanup_tail_isolation_owner_raise_still_closes():
    from unittest import mock

    h = _make_harness()
    with mock.patch("harnessx.core.harness.release_owners",
                    side_effect=RuntimeError("release boom")):
        with pytest.raises(RuntimeError):
            await h.cleanup()

    # Even though release_owners raised, _closed / discard still ran.
    assert h._closed
    assert h not in h._active if hasattr(h, "_active") else True


# ── idempotent: second cleanup short-circuits ───────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_idempotent_second_call_short_circuits():
    h = _make_harness(plugins=[_StubPlugin()])
    await h.cleanup()
    first_task = h._cleanup_task
    await h.cleanup()
    assert h._rt.plugins[0].stops == 1   # not re-run
    assert h._cleanup_task is first_task  # no rebuild

"""
Small helpers for driving Kit-CAE's async viz operators from code, reused
from the working pattern already validated in
CAE_Examples/AhmedML/run_1/open_ahmed_cae_scene.py.
"""

import asyncio

import omni.kit.app
from omni.cae.viz.controller import EVT_OPERATOR_COMPLETE


async def wait_frames(n):
    app = omni.kit.app.get_app()
    for _ in range(n):
        await app.next_update_async()


async def wait_for_operator(loop, dispatcher, prim_path, operator, action_coro):
    """Runs `action_coro` (e.g. an execute_command(...) call) and waits for
    the CAE viz pipeline to report that `operator` finished on `prim_path`."""
    future = loop.create_future()

    def on_event(event):
        if future.done():
            return
        payload = dict(event.payload)
        if operator is not None and payload.get("operator") != operator:
            return
        future.set_result(payload)

    sub = dispatcher.observe_event(
        observer_name=f"domino_predict:{prim_path}:{operator}",
        event_name=f"{EVT_OPERATOR_COMPLETE}:immediate",
        filter={"prim_path": prim_path},
        on_event=on_event,
    )
    await action_coro
    app = omni.kit.app.get_app()
    for _ in range(1000):
        if future.done():
            break
        await app.next_update_async()
        await asyncio.sleep(0.02)
    sub.reset()
    return future.result() if future.done() else None

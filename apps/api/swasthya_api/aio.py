"""One event-loop choice for every entry point.

psycopg's async mode cannot run on Windows' default ProactorEventLoop, so the
API, the worker and the dev tools all run on a SelectorEventLoop — which is
also the default on Linux and macOS, so nothing differs between machines.

    uvicorn --loop swasthya_api.aio:new_event_loop ...
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


def new_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro, loop_factory=new_event_loop)

import asyncio
import functools

if not hasattr(asyncio, "to_thread"):

    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        bound = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(None, bound)

    asyncio.to_thread = _to_thread  # type: ignore[attr-defined]

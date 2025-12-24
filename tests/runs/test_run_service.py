import asyncio
from collections import defaultdict
from datetime import date

import pytest

from retailcheck.config import TemplateDefaults
from retailcheck.runs.models import RunRecord
from retailcheck.runs.service import (
    RoleAlreadyTakenError,
    RunNotFoundError,
    RunService,
    RunUser,
)


class InMemoryRunsRepository:
    def __init__(self) -> None:
        self.records: dict[str, RunRecord] = {}

    async def get_run(self, shop_id: str, date: str):
        return self.records.get((shop_id, date))

    async def save_run(self, record: RunRecord):
        self.records[(record.shop_id, record.date)] = record


class InMemoryRedis:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, name: str, timeout: int):
        lock = self._locks[name]

        class _Wrapper:
            def __init__(self, inner: asyncio.Lock):
                self._inner = inner

            async def __aenter__(self):
                await self._inner.acquire()
                return self

            async def __aexit__(self, exc_type, exc, tb):
                self._inner.release()

        return _Wrapper(lock)

    async def close(self) -> None:
        return None


@pytest.fixture
def run_service(event_loop):
    repo = InMemoryRunsRepository()
    redis = InMemoryRedis()
    templates = TemplateDefaults(
        {
            "open": "opening_v1",
            "check_1100": "closing_v1",
            "check_1600": "closing_v1",
            "check_1900": "closing_v1",
            "close": "closing_v1",
            "finance": "closing_v1",
        }
    )
    return RunService(repo, redis, templates, lock_ttl=1)


@pytest.mark.asyncio
async def test_assign_opener_creates_run(run_service: RunService):
    user = RunUser(user_id=1, username="tester", full_name="Tester")
    result = await run_service.assign_role("shop_1", "open", user)
    assert result.state == "assigned"
    assert result.run.opener_user_id == "1"
    assert result.run.current_active_user_id == "1"
    assert result.run.status == "in_progress"


@pytest.mark.asyncio
async def test_assign_opener_same_user(run_service: RunService):
    user = RunUser(user_id=1, username="tester", full_name="Tester")
    await run_service.assign_role("shop_1", "open", user)
    result = await run_service.assign_role("shop_1", "open", user)
    assert result.state == "already_holder"
    assert result.run.current_active_user_id == "1"
    assert result.run.status == "in_progress"


@pytest.mark.asyncio
async def test_assign_opener_other_user_conflict(run_service: RunService):
    user1 = RunUser(user_id=1, username="tester1", full_name="Tester 1")
    user2 = RunUser(user_id=2, username="tester2", full_name="Tester 2")
    await run_service.assign_role("shop_1", "open", user1)
    with pytest.raises(RoleAlreadyTakenError):
        await run_service.assign_role("shop_1", "open", user2)


@pytest.mark.asyncio
async def test_assign_closer_requires_run(run_service: RunService):
    user = RunUser(user_id=1, username="tester", full_name="Tester")
    with pytest.raises(RunNotFoundError):
        await run_service.assign_role("shop_1", "close", user)


@pytest.mark.asyncio
async def test_assign_closer(run_service: RunService):
    opener = RunUser(user_id=1, username="tester1", full_name="Tester 1")
    closer = RunUser(user_id=2, username="tester2", full_name="Tester 2")
    await run_service.assign_role("shop_1", "open", opener)
    result = await run_service.assign_role("shop_1", "close", closer)
    assert result.state == "assigned"
    assert result.run.closer_user_id == "2"
    assert result.run.current_active_user_id == "2"
    assert result.run.status == "in_progress"


@pytest.mark.asyncio
async def test_assign_opener_after_return_resets_status(run_service: RunService):
    repo = run_service._repository  # type: ignore[attr-defined]
    today = date.today().isoformat()
    record = RunRecord(
        run_id="run_returned",
        date=today,
        shop_id="shop_1",
        status="returned",
    )
    await repo.save_run(record)
    user = RunUser(user_id=5, username="tester", full_name="Tester")
    result = await run_service.assign_role("shop_1", "open", user)
    assert result.state == "assigned"
    assert result.run.status == "in_progress"
    assert result.run.current_active_user_id == "5"


@pytest.mark.asyncio
async def test_return_run_clears_finish_and_active(run_service: RunService):
    repo = run_service._repository  # type: ignore[attr-defined]
    today = date.today().isoformat()
    record = RunRecord(
        run_id="run_closed",
        date=today,
        shop_id="shop_1",
        status="closed",
        opener_user_id="1",
        closer_user_id="2",
        delta_rub="50.0",
        finished_at="2025-02-01T22:00:00Z",
        current_active_user_id="2",
    )
    await repo.save_run(record)
    actor = RunUser(user_id=99, username="manager", full_name="Manager")
    returned = await run_service.return_run("shop_1", actor, "Нет Z")
    assert returned.status == "returned"
    assert returned.delta_rub is None
    assert returned.finished_at is None
    assert returned.current_active_user_id is None
    assert returned.comment == "Нет Z"

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from loguru import logger
from redis.asyncio import Redis

from retailcheck.audit.models import AuditRecord
from retailcheck.audit.repository import AuditRepository
from retailcheck.config import TemplateDefaults
from retailcheck.runs.models import RunRecord, now_iso
from retailcheck.runs.repository import RunsRepository


class RunNotFoundError(Exception):
    pass


class RunAlreadyExistsError(Exception):
    pass


class RoleAlreadyTakenError(Exception):
    def __init__(self, role: str, username: str | None = None) -> None:
        super().__init__(f"{role} already taken")
        self.role = role
        self.username = username


@dataclass(frozen=True)
class RunUser:
    user_id: int
    username: str | None
    full_name: str


@dataclass
class RoleAssignmentResult:
    run: RunRecord
    role: str
    state: str  # assigned | already_holder


class RunService:
    def __init__(
        self,
        repository: RunsRepository,
        redis: Redis,
        template_defaults: TemplateDefaults,
        lock_ttl: int = 10,
        audit_repository: AuditRepository | None = None,
        run_scope: str = "shop_id_date",
    ) -> None:
        self._repository = repository
        self._redis = redis
        self._templates = template_defaults
        self._lock_ttl = lock_ttl
        self._audit_repo = audit_repository
        self._run_scope = run_scope

    async def assign_role(self, shop_id: str, role: str, user: RunUser) -> RoleAssignmentResult:
        if role not in ("open", "close"):
            raise ValueError(f"Unknown role: {role}")

        today = date.today().isoformat()
        lock_key = self._build_lock_key(shop_id, today, role)
        lock = self._redis.lock(lock_key, timeout=self._lock_ttl)
        async with lock:
            await self._log_lock_acquired(lock)
            run = await self._repository.get_run(shop_id, today)
            if role == "open":
                result = await self._assign_opener(run, shop_id, today, user)
            else:
                result = await self._assign_closer(run, shop_id, today, user)
        return result

    async def _assign_opener(
        self, run: RunRecord | None, shop_id: str, today: str, user: RunUser
    ) -> RoleAssignmentResult:
        if run is None:
            phase_map = self._new_phase_map()
            run = RunRecord(
                run_id=str(uuid4()),
                date=today,
                shop_id=shop_id,
                status="opened",
                template_open_id=self._templates.opening_template_id,
                template_close_id=self._templates.closing_template_id,
                template_phase_map=phase_map,
            )
            run.created_at = now_iso()
            run.with_opener(str(user.user_id), user.username)
            self._set_active_user(run, user)
            await self._repository.save_run(run)
            await self._log_role_assignment("start_open", run, user)
            return RoleAssignmentResult(run=run, role="open", state="assigned")

        self._ensure_phase_map(run)
        if run.opener_user_id == str(user.user_id):
            self._set_active_user(run, user)
            await self._repository.save_run(run)
            return RoleAssignmentResult(run=run, role="open", state="already_holder")

        if run.opener_user_id:
            raise RoleAlreadyTakenError("open", run.opener_username)

        run.with_opener(str(user.user_id), user.username)
        self._set_active_user(run, user)
        await self._repository.save_run(run)
        await self._log_role_assignment("start_open", run, user)
        return RoleAssignmentResult(run=run, role="open", state="assigned")

    async def _assign_closer(
        self, run: RunRecord | None, shop_id: str, today: str, user: RunUser
    ) -> RoleAssignmentResult:
        if run is None:
            raise RunNotFoundError(f"No run for shop {shop_id} at {today}")

        self._ensure_phase_map(run)
        if run.closer_user_id == str(user.user_id):
            self._set_active_user(run, user)
            await self._repository.save_run(run)
            return RoleAssignmentResult(run=run, role="close", state="already_holder")

        if run.closer_user_id:
            raise RoleAlreadyTakenError("close", run.closer_username)

        run.with_closer(str(user.user_id), user.username)
        self._set_active_user(run, user)
        await self._repository.save_run(run)
        await self._log_role_assignment("start_close", run, user)
        return RoleAssignmentResult(run=run, role="close", state="assigned")

    async def get_today_run(self, shop_id: str) -> RunRecord | None:
        today = date.today().isoformat()
        return await self._repository.get_run(shop_id, today)

    async def finalize_run(self, run_id: str, delta_total: float) -> RunRecord:
        runs = await self._repository.list_runs()
        run = next((r for r in runs if r.run_id == run_id), None)
        if not run:
            raise RunNotFoundError(run_id)
        self._ensure_phase_map(run)
        run.status = "closed"
        run.delta_rub = f"{delta_total:.2f}"
        run.finished_at = now_iso()
        run.current_active_user_id = None
        await self._repository.save_run(run)
        return run

    async def handover_role(self, shop_id: str, role: str, user: RunUser) -> RunRecord:
        if role not in ("open", "close"):
            raise ValueError(f"Unknown role: {role}")
        today = date.today().isoformat()
        lock_key = self._build_lock_key(shop_id, today, role)
        lock = self._redis.lock(lock_key, timeout=self._lock_ttl)
        async with lock:
            await self._log_lock_acquired(lock)
            run = await self._repository.get_run(shop_id, today)
            if not run:
                raise RunNotFoundError(f"No run for shop {shop_id} at {today}")
            self._ensure_phase_map(run)
            if role == "open":
                run.with_opener(str(user.user_id), user.username, preserve_status=True)
            else:
                run.with_closer(str(user.user_id), user.username)
            self._set_active_user(run, user)
            await self._repository.save_run(run)
            return run

    async def create_run(self, shop_id: str, run_date: str | None = None) -> RunRecord:
        target_date = run_date or date.today().isoformat()
        lock_key = self._build_lock_key(shop_id, target_date, "create")
        lock = self._redis.lock(lock_key, timeout=self._lock_ttl)
        async with lock:
            await self._log_lock_acquired(lock)
            existing = await self._repository.get_run(shop_id, target_date)
            if existing:
                raise RunAlreadyExistsError(f"Run already exists for {shop_id} on {target_date}")
            phase_map = self._new_phase_map()
            run = RunRecord(
                run_id=str(uuid4()),
                date=target_date,
                shop_id=shop_id,
                status="opened",
                template_open_id=self._templates.opening_template_id,
                template_close_id=self._templates.closing_template_id,
                template_phase_map=phase_map,
            )
            await self._repository.save_run(run)
            return run

    async def return_run(
        self,
        shop_id: str,
        actor: RunUser,
        reason: str,
        run_date: str | None = None,
    ) -> RunRecord:
        target_date = run_date or date.today().isoformat()
        run = await self._repository.get_run(shop_id, target_date)
        if not run:
            raise RunNotFoundError(f"No run for shop {shop_id} at {target_date}")
        self._ensure_phase_map(run)
        run.status = "returned"
        run.current_active_user_id = None
        if reason:
            run.comment = reason
        await self._repository.save_run(run)
        await self._append_audit(
            action="return_run",
            run=run,
            details=f"returned by {actor.username or actor.full_name}: {reason}",
            user_id=str(actor.user_id),
        )
        return run

    async def _log_lock_acquired(self, lock) -> None:
        lock_name = getattr(lock, "name", None)
        redis_key = (
            lock_name
            if isinstance(lock_name, (bytes, str))  # noqa: UP038 - keep Python 3.8 support
            else getattr(lock, "key", None)
        )
        if isinstance(redis_key, bytes):
            redis_key = redis_key.decode()
        ttl_target = lock_name or redis_key
        ttl_ms = await self._redis.pttl(ttl_target) if ttl_target else -1
        ttl_sec = ttl_ms / 1000 if ttl_ms and ttl_ms > 0 else -1
        logger.info("Lock %s acquired (ttl %.1fs)", redis_key or "unknown", ttl_sec)

    def _build_lock_key(self, shop_id: str, date_value: str, suffix: str) -> str:
        if self._run_scope == "shop_id_only":
            return f"lock:run:{shop_id}:{suffix}"
        return f"lock:run:{shop_id}:{date_value}:{suffix}"

    def _new_phase_map(self) -> dict[str, str]:
        return dict(self._templates.phase_map)

    def _ensure_phase_map(self, run: RunRecord) -> None:
        if not run.template_phase_map:
            run.template_phase_map = self._new_phase_map()
        for phase, template_id in self._templates.phase_map.items():
            run.template_phase_map.setdefault(phase, template_id)
        run.template_open_id = run.template_phase_map.get(
            "open",
            self._templates.opening_template_id,
        )
        run.template_close_id = run.template_phase_map.get(
            "close",
            self._templates.closing_template_id,
        )

    def _set_active_user(self, run: RunRecord, user: RunUser) -> None:
        run.current_active_user_id = str(user.user_id)

    async def _log_role_assignment(self, action: str, run: RunRecord, user: RunUser) -> None:
        await self._append_audit(
            action=action,
            run=run,
            details=f"{action} by {user.username or user.full_name}",
            user_id=str(user.user_id),
        )

    async def _append_audit(
        self,
        action: str,
        run: RunRecord,
        details: str,
        user_id: str | None,
    ) -> None:
        if not self._audit_repo:
            return
        record = AuditRecord.create(
            action=action,
            entity="run",
            entity_id=run.run_id,
            details=details,
            user_id=user_id,
        )
        await self._audit_repo.append(record)

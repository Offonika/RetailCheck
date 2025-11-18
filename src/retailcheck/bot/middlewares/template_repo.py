from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware

from retailcheck.attachments.repository import AttachmentRepository
from retailcheck.audit.repository import AuditRepository
from retailcheck.export.repository import ExportRepository
from retailcheck.runs.repository import RunsRepository
from retailcheck.runsteps.repository import RunStepsRepository
from retailcheck.templates.repository import TemplateRepository


class TemplateRepositoryMiddleware(BaseMiddleware):
    def __init__(
        self,
        template_repo: TemplateRepository,
        runs_repo: RunsRepository,
        runsteps_repo: RunStepsRepository,
        attachments_repo: AttachmentRepository,
        audit_repo: AuditRepository,
        export_repo: ExportRepository,
    ) -> None:
        super().__init__()
        self._template_repo = template_repo
        self._runs_repo = runs_repo
        self._runsteps_repo = runsteps_repo
        self._attachments_repo = attachments_repo
        self._audit_repo = audit_repo
        self._export_repo = export_repo

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data["template_repository"] = self._template_repo
        data["runs_repository"] = self._runs_repo
        data["runsteps_repository"] = self._runsteps_repo
        data["attachments_repository"] = self._attachments_repo
        data["audit_repository"] = self._audit_repo
        data["export_repository"] = self._export_repo
        return await handler(event, data)

from __future__ import annotations

from dataclasses import dataclass

from retailcheck.sheets.client import SheetsClient
from retailcheck.templates.models import TemplateDefinition, TemplateStepDefinition


@dataclass(frozen=True)
class TemplateCache:
    templates: dict[str, TemplateDefinition]


class TemplateRepository:
    """
    Reads template metadata from Google Sheets and caches it in memory.

    Templates sheet layout expected per docs/DataModel.md.
    """

    def __init__(self, sheets: SheetsClient) -> None:
        self._sheets = sheets
        self._cache: TemplateCache | None = None

    def _ensure_cache(self) -> TemplateCache:
        if self._cache:
            return self._cache
        templates = self._load_templates()
        self._cache = TemplateCache(templates=templates)
        return self._cache

    def _load_templates(self) -> dict[str, TemplateDefinition]:
        template_rows = self._sheets.read("Templates!A2:F")
        step_rows = self._sheets.read("TemplateSteps!A2:J")
        steps_by_template: dict[str, list[TemplateStepDefinition]] = {}
        for row in step_rows:
            if not row or not row[0]:
                continue
            padded = row + [""] * (10 - len(row))
            try:
                order = int(padded[1])
            except ValueError:
                continue
            template_id = padded[0]
            steps_by_template.setdefault(template_id, []).append(
                TemplateStepDefinition(
                    step_order=order,
                    code=padded[2],
                    title=padded[3],
                    type=padded[4],
                    required=padded[5].upper() == "TRUE",
                    validators_json=padded[6] or None,
                    norm_rule=padded[7] or None,
                    hint=padded[8] or None,
                    owner_role=padded[9] or "shared",
                )
            )
        templates: dict[str, TemplateDefinition] = {}
        for row in template_rows:
            if not row or not row[0]:
                continue
            template_id = row[0]
            templates[template_id] = TemplateDefinition(
                template_id=template_id,
                name=row[1],
                version=int(row[2]),
                phase=row[3],
                description=row[5] if len(row) > 5 else "",
                steps=sorted(steps_by_template.get(template_id, []), key=lambda s: s.step_order),
            )
        return templates

    def refresh(self) -> None:
        """Invalidate cache (call when Templates sheet changed)."""
        self._cache = None

    def get(self, template_id: str) -> TemplateDefinition:
        cache = self._ensure_cache()
        try:
            return cache.templates[template_id]
        except KeyError as err:
            raise KeyError(f"Template '{template_id}' not found in Google Sheet") from err

    def list_by_phase(self, phase: str) -> list[TemplateDefinition]:
        cache = self._ensure_cache()
        return [tmpl for tmpl in cache.templates.values() if tmpl.phase == phase]

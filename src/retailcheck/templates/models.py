from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TemplateStepDefinition:
    step_order: int
    code: str
    title: str
    type: str
    required: bool
    validators_json: str | None = None
    norm_rule: str | None = None
    hint: str | None = None
    owner_role: str = "shared"

    def to_row(self, template_id: str) -> list[str]:
        return [
            template_id,
            str(self.step_order),
            self.code,
            self.title,
            self.type,
            "TRUE" if self.required else "FALSE",
            self.validators_json or "",
            self.norm_rule or "",
            self.hint or "",
            self.owner_role,
        ]


@dataclass(frozen=True)
class TemplateDefinition:
    template_id: str
    name: str
    version: int
    phase: str
    description: str
    steps: Sequence[TemplateStepDefinition]

    def template_row(self) -> list[str]:
        return [
            self.template_id,
            self.name,
            str(self.version),
            self.phase,
            "TRUE",
            self.description,
        ]


def load_template_definition(path: Path) -> TemplateDefinition:
    payload = json.loads(path.read_text(encoding="utf-8"))
    template_data = payload["template"]
    steps = [
        TemplateStepDefinition(
            step_order=step["step_order"],
            code=step["code"],
            title=step["title"],
            type=step["type"],
            required=step.get("required", True),
            validators_json=step.get("validators_json"),
            norm_rule=step.get("norm_rule"),
            hint=step.get("hint"),
            owner_role=step.get("owner_role", "shared"),
        )
        for step in payload["steps"]
    ]
    return TemplateDefinition(
        template_id=template_data["template_id"],
        name=template_data["name"],
        version=template_data["version"],
        phase=template_data["phase"],
        description=template_data.get("description", ""),
        steps=steps,
    )

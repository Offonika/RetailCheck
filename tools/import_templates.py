"""
Import template definitions (open/close) into Google Sheets.

Usage:
    poetry run python tools/import_templates.py templates/opening_v1.json templates/closing_v1.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from retailcheck.config import get_google_config
from retailcheck.sheets.client import SheetsClient
from retailcheck.templates.models import TemplateDefinition, load_template_definition

TEMPLATES_HEADER = ["template_id", "name", "version", "phase", "is_active", "description"]
TEMPLATE_STEPS_HEADER = [
    "template_id",
    "step_order",
    "code",
    "title",
    "type",
    "required",
    "validators_json",
    "norm_rule",
    "hint",
    "owner_role",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import template definitions into Sheets.")
    parser.add_argument(
        "files",
        nargs="+",
        help="Path(s) to template JSON files (see templates/*.json).",
    )
    return parser.parse_args()


def write_templates(client: SheetsClient, templates: list[TemplateDefinition]) -> None:
    _validate_templates(templates)
    rows = [TEMPLATES_HEADER]
    for tmpl in templates:
        rows.append(tmpl.template_row())
    client.clear("Templates")
    client.write("Templates!A1", rows)

    step_rows = [TEMPLATE_STEPS_HEADER]
    for tmpl in templates:
        step_rows.extend(step.to_row(tmpl.template_id) for step in tmpl.steps)
    client.clear("TemplateSteps")
    client.write("TemplateSteps!A1", step_rows)


def _validate_templates(templates: list[TemplateDefinition]) -> None:
    phases = {"open", "close"}
    seen_ids: set[str] = set()
    for tmpl in templates:
        if tmpl.template_id in seen_ids:
            raise ValueError(f"Duplicate template_id detected: {tmpl.template_id}")
        seen_ids.add(tmpl.template_id)
        if tmpl.phase not in phases:
            raise ValueError(f"Template {tmpl.template_id} has invalid phase {tmpl.phase}")
    if "open" not in {tmpl.phase for tmpl in templates}:
        raise ValueError("At least one template with phase='open' is required")
    if "close" not in {tmpl.phase for tmpl in templates}:
        raise ValueError("At least one template with phase='close' is required")


def main() -> None:
    args = parse_args()
    google_cfg = get_google_config()
    client = SheetsClient(google_cfg.sheets_id, google_cfg.service_account_json)

    templates = [load_template_definition(Path(file)) for file in args.files]
    write_templates(client, templates)
    for tmpl in templates:
        print(f"[OK] Imported template {tmpl.template_id} ({tmpl.phase})")


if __name__ == "__main__":
    main()

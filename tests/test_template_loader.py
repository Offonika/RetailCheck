from pathlib import Path

from retailcheck.templates.models import load_template_definition

FIXTURES = Path("templates")


def test_load_template_definition_opening() -> None:
    tmpl = load_template_definition(FIXTURES / "opening_v1.json")
    assert tmpl.template_id == "opening_v1"
    assert tmpl.phase == "open"
    assert len(tmpl.steps) == 3
    assert tmpl.steps[0].code == "cash_float_open"

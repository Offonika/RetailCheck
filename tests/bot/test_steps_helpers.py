import pytest

from retailcheck.bot.handlers import steps


def test_extract_shop_id():
    assert steps._extract_shop_id("/open shop_1") == "shop_1"  # noqa: SLF001
    assert steps._extract_shop_id("/open") is None  # noqa: SLF001


def test_render_step_prompt_contains_title():
    step_data = {
        "title": "Касса",
        "type": "number",
        "hint": "Введите сумму",
        "code": "cash",
        "required": True,
    }
    text = steps._render_step_prompt(step_data, 0, 3)  # noqa: SLF001
    assert "Касса" in text
    assert "3" in text


def test_serialize_step():
    class Dummy:
        code = "cash"
        title = "Касса"
        type = "number"
        hint = "Введите сумму"
        required = True
        validators_json = '{"min": 0}'

    data = steps._serialize_step(Dummy())  # noqa: SLF001
    assert data["code"] == "cash"
    assert data["hint"] == "Введите сумму"
    assert data["validators"]["min"] == 0


def test_parse_number_value():
    value, comment_required, delta = steps._parse_number_value(
        "10",
        {"min": 0, "max": 20, "norm": 5},
    )  # noqa: SLF001
    assert value == "10"
    assert not comment_required
    assert delta == "5.00"
    with pytest.raises(ValueError):
        steps._parse_number_value("-1", {"min": 0})  # noqa: SLF001


def test_parse_bool_value():
    assert steps._parse_bool_value("да") is True  # noqa: SLF001
    assert steps._parse_bool_value("No") is False  # noqa: SLF001
    with pytest.raises(ValueError):
        steps._parse_bool_value("maybe")  # noqa: SLF001

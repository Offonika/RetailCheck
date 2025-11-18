from retailcheck.localization import gettext


def test_gettext_returns_russian_strings():
    assert gettext("start.button.open") == "🟢 Открыть смену"
    assert "Магазин" in gettext("start.choose_action", shop="Магазин 1")
    assert gettext("steps.button.back").startswith("⬅️")

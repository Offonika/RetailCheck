from retailcheck.bot.handlers import steps


def test_normalize_terminal_choice():
    assert steps._normalize_terminal_choice("Т-Банк") == "T-Bank"
    assert steps._normalize_terminal_choice("сбербанк") == "Sberbank"
    assert steps._normalize_terminal_choice("неизвестно") is None

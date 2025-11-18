from retailcheck.bot.handlers import status
from retailcheck.runs.models import RunRecord
from retailcheck.runsteps.models import RunStepRecord


def make_run():
    return RunRecord(
        run_id="run",
        date="2025-02-01",
        shop_id="shop_1",
        status="opened",
        template_open_id="opening_v1",
        template_close_id="closing_v1",
    )


def test_format_status():
    run = make_run()
    run.opener_username = "user1"
    text = status._format_status(run, 3)  # noqa: SLF001
    assert "user1" in text
    assert "3" in text


def test_format_summary():
    run = make_run()
    run.opener_username = "user1"
    steps = [
        RunStepRecord(
            run_id="run",
            phase="close",
            step_code="cash",
            value_number="100",
            delta_number="5",
        ),
        RunStepRecord(
            run_id="run",
            phase="close",
            step_code="z_report_photo",
            value_text="photo:id",
        ),
    ]
    attachments = []
    summary = status._format_summary(run, steps, attachments, dual_mode=False)  # noqa: SLF001
    assert "100" in summary
    assert "5.00" in summary


def test_missing_comments():
    steps = [
        RunStepRecord(
            run_id="run",
            phase="close",
            step_code="cash",
            delta_number="5",
            comment=None,
        ),
        RunStepRecord(run_id="run", phase="close", step_code="pos", delta_number=None),
    ]
    missing = status._missing_comments(steps)  # noqa: SLF001
    assert missing == ["cash"]

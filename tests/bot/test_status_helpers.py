from retailcheck.attachments.models import AttachmentRecord
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
            comment="Комментарий",
        ),
        RunStepRecord(
            run_id="run",
            phase="close",
            step_code="z_report_photo",
            value_text="photo:id",
        ),
    ]
    attachments = [
        AttachmentRecord(
            run_id="run",
            step_code="z_report_photo",
            telegram_file_id="file",
            kind="z",
        )
    ]
    requirements = {
        "cash": status.StepRequirementMeta(
            code="cash",
            title="Касса 19:00",
            owner_role="closer",
            required=True,
            step_type="number",
            validators={},
        ),
        "z_report_photo": status.StepRequirementMeta(
            code="z_report_photo",
            title="Z-фото",
            owner_role="closer",
            required=True,
            step_type="photo",
            validators={},
        ),
    }
    summary = status._format_summary(  # noqa: SLF001
        run,
        steps,
        attachments,
        dual_mode=False,
        requirements=requirements,
    )
    assert "Касса 19:00" in summary
    assert "Комментарий" in summary
    assert "Фото" in summary


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


def test_missing_required_steps_detects_roles():
    meta = {
        "cash_float_open": status.StepRequirementMeta(
            code="cash_float_open",
            title="Касса открытие",
            owner_role="opener",
            required=True,
            step_type="number",
            validators={},
        ),
        "z_report_photo": status.StepRequirementMeta(
            code="z_report_photo",
            title="Z-фото",
            owner_role="closer",
            required=True,
            step_type="photo",
            validators={},
        ),
    }
    missing = status._missing_required_steps(meta, [])  # noqa: SLF001
    assert missing == {"opener": ["cash_float_open"], "closer": ["z_report_photo"]}


def test_missing_required_shared_uses_any_role():
    meta = {
        "shared_note": status.StepRequirementMeta(
            code="shared_note",
            title="Общий комментарий",
            owner_role="shared",
            required=True,
            step_type="text",
            validators={},
        )
    }
    steps = [
        RunStepRecord(
            run_id="run",
            phase="close",
            step_code="shared_note",
            owner_role="opener",
            value_text="done",
            status="ok",
        )
    ]
    missing = status._missing_required_steps(meta, steps)  # noqa: SLF001
    assert missing == {}


def test_missing_required_terminal_demands_both_roles():
    meta = {
        "photo_terminal_1": status.StepRequirementMeta(
            code="photo_terminal_1",
            title="Сверка терминала",
            owner_role="shared",
            required=True,
            step_type="photo",
            validators={},
        )
    }
    steps = [
        RunStepRecord(
            run_id="run",
            phase="finance",
            step_code="photo_terminal_1",
            owner_role="opener",
            status="ok",
        )
    ]
    missing = status._missing_required_steps(meta, steps)  # noqa: SLF001
    assert missing == {"closer": ["photo_terminal_1"]}


def test_conditional_comment_requirement_triggers():
    meta = {
        "delta_comment": status.StepRequirementMeta(
            code="delta_comment",
            title="Комментарий",
            owner_role="closer",
            required=False,
            step_type="text",
            validators={"delta_threshold": 50},
        )
    }
    steps = [
        RunStepRecord(run_id="run", phase="close", step_code="cash", delta_number="70", status="ok")
    ]
    errors = status._check_conditional_requirements(meta, steps, [], total_delta=70)  # noqa: SLF001
    assert errors
    assert "Комментарий" in errors[0]


def test_conditional_photo_requirement_uses_attachments():
    meta = {
        "delta_photo": status.StepRequirementMeta(
            code="delta_photo",
            title="Фото расхождения",
            owner_role="closer",
            required=False,
            step_type="photo",
            validators={"delta_threshold": 20},
        )
    }
    steps = [
        RunStepRecord(run_id="run", phase="close", step_code="cash", delta_number="25", status="ok")
    ]
    attachments = [
        AttachmentRecord(
            run_id="run",
            step_code="delta_photo",
            telegram_file_id="file_1",
            kind="other",
        )
    ]
    errors = status._check_conditional_requirements(meta, steps, attachments, total_delta=25)  # noqa: SLF001
    assert not errors


def test_attachments_summary_uses_role_hint():
    requirements = {
        "photo_terminal_1": status.StepRequirementMeta(
            code="photo_terminal_1",
            title="Сверка терминала (фото)",
            owner_role="shared",
            required=True,
            step_type="photo",
            validators={},
        )
    }
    attachments = [
        AttachmentRecord(
            run_id="run",
            step_code="photo_terminal_1",
            telegram_file_id="file_a",
            kind="pos_receipt:opener",
        ),
        AttachmentRecord(
            run_id="run",
            step_code="photo_terminal_1",
            telegram_file_id="file_b",
            kind="pos_receipt:closer",
        ),
    ]
    lines = status._format_attachments_summary(attachments, requirements)  # noqa: SLF001
    joined = " ".join(lines)
    assert "A" in joined
    assert "B" in joined

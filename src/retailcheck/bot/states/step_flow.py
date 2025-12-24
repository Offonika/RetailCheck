from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class StepFlowState(StatesGroup):
    preparing = State()
    waiting_input = State()

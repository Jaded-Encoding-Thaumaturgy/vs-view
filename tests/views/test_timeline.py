import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from vsview.app.views.timeline import FrameEdit, TimeEdit
from vsview.types import Frame

pytestmark = [pytest.mark.unit, pytest.mark.qt]


def test_frame_edit_immediate_emission_on_scroll(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)

    edit = FrameEdit(parent)
    qtbot.addWidget(edit)
    edit.setMaximum(1000)

    emitted: list[tuple[Frame, Frame]] = []
    edit.frameChanged.connect(lambda cur, old: emitted.append((cur, old)))

    # Step on spinner (simulating wheel scroll or arrow button click)
    edit.stepBy(1)
    assert edit.value() == 1
    assert len(emitted) == 1
    assert emitted[0] == (Frame(1), Frame(0))

    edit.stepBy(2)
    assert edit.value() == 3
    assert len(emitted) == 2
    assert emitted[1] == (Frame(3), Frame(1))


def test_time_edit_immediate_emission_on_scroll(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)

    edit = TimeEdit(parent)
    qtbot.addWidget(edit)
    emitted: list[tuple[QTime, QTime]] = []
    edit.valueChanged.connect(lambda cur, old: emitted.append((cur, old)))

    # Step on time edit
    edit.stepBy(1)
    assert len(emitted) == 1
    assert emitted[0] == (QTime(1, 0, 0, 0), QTime(0, 0, 0, 0))

    edit.stepBy(1)
    assert len(emitted) == 2
    assert emitted[1] == (QTime(2, 0, 0, 0), QTime(1, 0, 0, 0))

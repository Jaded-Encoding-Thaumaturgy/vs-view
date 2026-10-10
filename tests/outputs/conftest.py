import pytest

from vsview.vsenv.loop import QtEventLoop


@pytest.fixture(autouse=True)
def use_qt_loop(qt_event_loop: QtEventLoop) -> None: ...

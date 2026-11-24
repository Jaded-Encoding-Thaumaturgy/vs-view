from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from vsview.app.icon import load_icon
from vsview.app.settings import SettingsManager
from vsview.app.views.notifications import Notification, NotificationManager, NotificationWidget
from vsview.assets import IconName

pytestmark = [pytest.mark.unit, pytest.mark.qt]

ANCHOR = QPoint(800, 580)


@pytest.fixture
def host(qtbot: QtBot) -> QWidget:
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(1000, 700)
    host.show()
    return host


@pytest.fixture
def manager(host: QWidget) -> NotificationManager:
    manager = NotificationManager(host)
    manager.set_anchor(ANCHOR)
    return manager


def _sticky(message: str, key: str | None = None) -> Notification:
    return Notification(message, key=key, timeout_ms=0)


def _close(qtbot: QtBot, card: NotificationWidget) -> None:
    qtbot.mouseClick(card.close_button, Qt.MouseButton.LeftButton)


def _bottom_right(card: NotificationWidget, host: QWidget) -> QPoint:
    return card.mapTo(host, QPoint(card.width(), card.height()))


def test_cards_stack_at_the_anchor_with_the_newest_at_the_bottom(
    qtbot: QtBot, host: QWidget, manager: NotificationManager
) -> None:
    first, second = _sticky("first"), _sticky("second")

    manager.notify(first)
    manager.notify(second)

    older, newer = manager.cards
    corner = ANCHOR - QPoint(NotificationManager.MARGIN, NotificationManager.MARGIN)
    above = corner - QPoint(0, newer.height() + NotificationManager.SPACING)
    qtbot.waitUntil(lambda: (_bottom_right(newer, host), _bottom_right(older, host)) == (corner, above))
    assert manager.notifications == [first, second]
    assert manager.height() == older.height() + NotificationManager.SPACING + newer.height()


def test_cards_follow_a_new_anchor(qtbot: QtBot, host: QWidget, manager: NotificationManager) -> None:
    manager.notify(_sticky("message"))
    (card,) = manager.cards
    anchor = QPoint(900, 650)

    manager.set_anchor(anchor)

    corner = anchor - QPoint(NotificationManager.MARGIN, NotificationManager.MARGIN)
    qtbot.waitUntil(lambda: _bottom_right(card, host) == corner)


@pytest.mark.parametrize(
    ("severity", "icon"),
    [
        (Notification.Severity.INFO, IconName.INFO),
        (Notification.Severity.SUCCESS, IconName.CHECK_CIRCLE),
        (Notification.Severity.WARNING, IconName.WARNING_CIRCLE),
        (Notification.Severity.ERROR, IconName.X_CIRCLE),
    ],
)
def test_card_shows_the_severity_icon_in_the_accent_color(
    manager: NotificationManager, severity: Notification.Severity, icon: IconName
) -> None:
    manager.notify(Notification("message", severity))

    (card,) = manager.cards
    expected = load_icon(icon, NotificationWidget.ICON_SIZE, card.accent_color)
    assert card.icon_label.pixmap().toImage() == expected.toImage()


@pytest.mark.parametrize(("title", "title_visible"), [("", False), ("Export", True)])
def test_card_shows_the_title_only_when_there_is_one(
    manager: NotificationManager, title: str, title_visible: bool
) -> None:
    manager.notify(Notification("message", title=title))

    (card,) = manager.cards
    assert card.title_label.isVisibleTo(card) is title_visible
    assert card.title_label.text() == title
    assert card.message_label.text() == "message"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("one line", "one line", id="short"),
        pytest.param("\n".join(["line"] * 8), "\n".join(["line"] * 8), id="max_lines"),
        pytest.param("\n".join(["line"] * 9), "\n".join(["line"] * 8) + "…", id="too_many_lines"),
        pytest.param("a" * 600, "a" * 600, id="max_chars"),
        pytest.param("a" * 601, "a" * 600 + "…", id="too_many_chars"),
    ],
)
def test_shorten_cuts_long_messages(text: str, expected: str) -> None:
    assert NotificationWidget.shorten(text) == expected


def test_card_with_a_shortened_message_keeps_the_full_message_in_the_tooltip(manager: NotificationManager) -> None:
    message = "\n".join(f"line {i}" for i in range(20))

    manager.notify(Notification(message))

    (card,) = manager.cards
    assert card.message_label.text() == NotificationWidget.shorten(message)
    assert card.message_label.toolTip() == message


@pytest.mark.parametrize(
    ("severity", "timeout_ms"),
    [
        pytest.param(Notification.Severity.INFO, 100, id="info_explicit"),
        pytest.param(Notification.Severity.INFO, None, id="info_setting"),
        pytest.param(Notification.Severity.ERROR, 100, id="error_explicit"),
    ],
)
def test_card_closes_after_its_timeout(
    qtbot: QtBot, manager: NotificationManager, severity: Notification.Severity, timeout_ms: int | None
) -> None:
    SettingsManager.global_settings.notification_timeout = 100
    manager.notify(Notification("message", severity, timeout_ms=timeout_ms))
    (card,) = manager.cards

    qtbot.waitUntil(lambda: manager.cards == [], timeout=2000)
    qtbot.waitUntil(lambda: not Shiboken.isValid(card), timeout=2000)
    assert manager.isHidden()


@pytest.mark.parametrize(
    ("severity", "timeout_ms", "setting_ms"),
    [
        pytest.param(Notification.Severity.ERROR, None, 100, id="error_default"),
        pytest.param(Notification.Severity.INFO, 0, 100, id="info_explicit_zero"),
        pytest.param(Notification.Severity.INFO, None, 0, id="info_setting_zero"),
    ],
)
def test_sticky_card_stays_until_it_is_closed(
    qtbot: QtBot,
    manager: NotificationManager,
    severity: Notification.Severity,
    timeout_ms: int | None,
    setting_ms: int,
) -> None:
    SettingsManager.global_settings.notification_timeout = setting_ms
    notification = Notification("message", severity, timeout_ms=timeout_ms)
    manager.notify(notification)
    (card,) = manager.cards

    qtbot.wait(300)
    assert manager.notifications == [notification]

    _close(qtbot, card)
    assert manager.cards == []


def test_hovering_a_card_pauses_its_timer(qtbot: QtBot, host: QWidget, manager: NotificationManager) -> None:
    manager.notify(Notification("message", timeout_ms=200))
    (card,) = manager.cards

    qtbot.mouseMove(card, card.rect().center())
    qtbot.wait(500)
    assert manager.cards == [card]

    qtbot.mouseMove(host, QPoint(5, 5))
    qtbot.waitUntil(lambda: manager.cards == [], timeout=2000)


def test_set_paused_stops_the_timers_until_it_is_resumed(qtbot: QtBot, manager: NotificationManager) -> None:
    manager.notify(Notification("before", timeout_ms=150))
    manager.set_paused(True)
    manager.notify(Notification("while paused", timeout_ms=150))

    qtbot.wait(400)
    assert [n.message for n in manager.notifications] == ["before", "while paused"]

    manager.set_paused(False)
    qtbot.waitUntil(lambda: manager.cards == [], timeout=2000)


def test_a_keyed_notification_updates_the_visible_card(manager: NotificationManager) -> None:
    manager.notify(_sticky("Exporting clip 1/2", key="export"))
    (card,) = manager.cards
    update = Notification("Successfully exported 2 clip(s).", Notification.Severity.SUCCESS, key="export")

    manager.notify(update)

    assert manager.cards == [card]
    assert manager.notifications == [update]
    assert card.message_label.text() == "Successfully exported 2 clip(s)."


def test_a_keyed_notification_replaces_the_queued_one_in_place(manager: NotificationManager) -> None:
    SettingsManager.global_settings.notification_max_visible = 1
    manager.notify(_sticky("visible"))
    later = _sticky("later")
    manager.notify(_sticky("Exporting clip 1/2", key="export"))
    manager.notify(later)
    update = _sticky("Exporting clip 2/2", key="export")

    manager.notify(update)

    assert manager.queued == [update, later]


def test_overflow_waits_in_the_queue_until_a_card_closes(qtbot: QtBot, manager: NotificationManager) -> None:
    SettingsManager.global_settings.notification_max_visible = 2
    first, second, third = _sticky("first"), _sticky("second"), _sticky("third")
    manager.notify(first)
    manager.notify(second)
    manager.notify(third)
    assert (manager.notifications, manager.queued) == ([first, second], [third])

    _close(qtbot, manager.cards[0])

    assert (manager.notifications, manager.queued) == ([second, third], [])


def test_a_full_queue_drops_its_oldest_notification_that_is_not_an_error(manager: NotificationManager) -> None:
    SettingsManager.global_settings.notification_max_visible = 1
    manager.notify(_sticky("visible"))
    error = Notification("error", Notification.Severity.ERROR)
    infos = [_sticky(f"info {i}") for i in range(NotificationManager.MAX_QUEUED - 1)]
    for notification in [error, *infos]:
        manager.notify(notification)
    newest = _sticky("newest")

    manager.notify(newest)

    assert manager.queued == [error, *infos[1:], newest]

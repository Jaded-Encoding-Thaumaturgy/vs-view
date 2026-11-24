from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, cast, override

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QEnterEvent,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from ...assets import IconName
from ..icon import IconReloadMixin, load_icon
from ..settings import SettingsManager


@dataclass(frozen=True, slots=True)
class Notification:
    """A message shown as a card in the bottom-right corner of the main window."""

    class Severity(StrEnum):
        """Severity of a notification. It sets the icon, the accent color and the default timeout."""

        INFO = "info"
        SUCCESS = "success"
        WARNING = "warning"
        ERROR = "error"

    message: str
    severity: Severity = Severity.INFO
    title: str = ""
    key: str | None = None
    """A new notification with the same key replaces this one instead of adding a new card."""
    timeout_ms: int | None = None
    """
    Delay before the card closes by itself. `0` keeps it until the user closes it.
    `None` keeps error notifications open and uses the notification timeout setting for the other ones.
    """


NotificationSeverity = Notification.Severity


class NotificationWidget(IconReloadMixin, QWidget):
    closeRequested = Signal(object)  # NotificationWidget

    WIDTH = 360
    ICON_SIZE = QSize(22, 22)
    CLOSE_ICON_SIZE = QSize(18, 18)
    ACCENT_WIDTH = 3
    RADIUS = 6.0
    MAX_MESSAGE_LINES = 8
    MAX_MESSAGE_CHARS = 600

    SEVERITY_ICONS: ClassVar[Mapping[Notification.Severity, IconName]] = {
        Notification.Severity.INFO: IconName.INFO,
        Notification.Severity.SUCCESS: IconName.CHECK_CIRCLE,
        Notification.Severity.WARNING: IconName.WARNING_CIRCLE,
        Notification.Severity.ERROR: IconName.X_CIRCLE,
    }
    SEVERITY_COLORS: ClassVar[Mapping[Notification.Severity, QColor]] = {
        Notification.Severity.SUCCESS: QColor("#4caf50"),
        Notification.Severity.WARNING: QColor("#ff9800"),
        Notification.Severity.ERROR: QColor("#f44336"),
    }

    def __init__(self, notification: Notification, parent: QWidget) -> None:
        super().__init__(parent)

        self._notification = notification
        self._auto_dismiss = False
        self._hovered = False
        self._paused = False
        self._remaining_ms = 0
        self._elapsed = QElapsedTimer()

        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._on_timeout)

        self.setFixedWidth(self.WIDTH)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(self.ICON_SIZE)

        self.title_label = QLabel(self, textFormat=Qt.TextFormat.PlainText, wordWrap=True)
        title_font = self.title_label.font()
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.message_label = QLabel(
            self,
            textFormat=Qt.TextFormat.PlainText,
            wordWrap=True,
            textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        self.message_label.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        self.close_button = self.make_tool_button(
            IconName.CLOSE,
            "Close",
            self,
            icon_size=self.CLOSE_ICON_SIZE,
            color_role=QPalette.ColorRole.WindowText,
        )
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(self._on_close_clicked)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.message_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.ACCENT_WIDTH + 10, 10, 6, 10)
        layout.setSpacing(8)
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)

        self.register_icon_callback(self._reload_icons)
        self.set_notification(notification)

    @property
    def notification(self) -> Notification:
        return self._notification

    @property
    def accent_color(self) -> QColor:
        return self.SEVERITY_COLORS.get(self._notification.severity, self.palette().color(QPalette.ColorRole.Highlight))

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        menu.addAction("Copy message", self._copy_message)
        menu.exec(event.globalPos())
        menu.deleteLater()

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self._update_timer()
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self._update_timer()
        super().leaveEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        palette = self.palette()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)

        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillPath(path, palette.color(QPalette.ColorRole.Window))
            painter.setClipPath(path)
            painter.fillRect(QRectF(rect.left(), rect.top(), self.ACCENT_WIDTH, rect.height()), self.accent_color)
            painter.setClipping(False)
            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            painter.drawPath(path)

    def set_notification(self, notification: Notification) -> None:
        """Show the content of the notification in this card. The timer does not change."""
        self._notification = notification

        message = notification.message.strip()
        shown = self.shorten(message)

        self.title_label.setText(notification.title)
        self.title_label.setVisible(bool(notification.title))
        self.message_label.setText(shown)
        self.message_label.setToolTip(message if shown != message else "")

        self._reload_icons()
        self.update()

    def fitted_height(self) -> int:
        """Return the height the card needs to show its whole content at its fixed width."""
        height = self.heightForWidth(self.WIDTH)
        return height if height > 0 else self.sizeHint().height()

    def start_timer(self, timeout_ms: int) -> None:
        """Close the card after `timeout_ms`, not counting the time it is paused. `0` keeps it open."""
        self._timer.stop()
        self._auto_dismiss = timeout_ms > 0
        self._remaining_ms = timeout_ms
        self._update_timer()

    def stop_timer(self) -> None:
        self._timer.stop()
        self._auto_dismiss = False

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._update_timer()

    @staticmethod
    def shorten(text: str) -> str:
        short = "\n".join(text.splitlines()[: NotificationWidget.MAX_MESSAGE_LINES])[
            : NotificationWidget.MAX_MESSAGE_CHARS
        ]
        return text if short == text else f"{short.rstrip()}…"

    def _update_timer(self) -> None:
        if not self._auto_dismiss:
            return

        paused = self._hovered or self._paused

        if paused and self._timer.isActive():
            self._remaining_ms = max(1, self._remaining_ms - self._elapsed.elapsed())
            self._timer.stop()
        elif not paused and not self._timer.isActive():
            self._elapsed.start()
            self._timer.start(self._remaining_ms)

    def _reload_icons(self) -> None:
        icon = self.SEVERITY_ICONS[self._notification.severity]
        self.icon_label.setPixmap(load_icon(icon, self.ICON_SIZE, self.accent_color))

    @Slot()
    def _on_timeout(self) -> None:
        self._auto_dismiss = False
        self.closeRequested.emit(self)

    @Slot()
    def _on_close_clicked(self) -> None:
        self.closeRequested.emit(self)

    @Slot()
    def _copy_message(self) -> None:
        QGuiApplication.clipboard().setText(self._notification.message)


class NotificationManager(QWidget):
    """Stack of notification cards, newest at the bottom."""

    MARGIN = 12
    SPACING = 8
    MAX_QUEUED = 20
    ANIMATION_MS = 180
    SLIDE_OFFSET = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._anchor = QPoint()
        self._paused = False
        self._cards = list[NotificationWidget]()
        self._stack = list[NotificationWidget]()
        self._queue = deque[Notification]()
        self._slides = dict[NotificationWidget, QPropertyAnimation]()

        self.hide()

    @property
    def cards(self) -> list[NotificationWidget]:
        """The visible cards, oldest first. Cards that are closing are not included."""
        return self._cards.copy()

    @property
    def notifications(self) -> list[Notification]:
        """The notifications of the visible cards, oldest first."""
        return [card.notification for card in self._cards]

    @property
    def queued(self) -> list[Notification]:
        """The notifications waiting for a free place, in the order they will be shown."""
        return list(self._queue)

    def set_anchor(self, point: QPoint) -> None:
        self._anchor = QPoint(point)
        self._layout_cards()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

        for card in self._cards:
            card.set_paused(paused)

    @Slot(object)
    def notify(self, notification: Notification) -> None:
        if notification.key is not None and self._replace(notification):
            return

        if len(self._cards) < SettingsManager.global_settings.notification_max_visible:
            self._add_card(notification)
            return

        if len(self._queue) >= self.MAX_QUEUED:
            dropped = next((n for n in self._queue if n.severity is not Notification.Severity.ERROR), self._queue[0])
            self._queue.remove(dropped)

        self._queue.append(notification)

    @Slot(str)
    def notify_message(self, message: str) -> None:
        self.notify(Notification(message))

    def _replace(self, notification: Notification) -> bool:
        for card in self._cards:
            if card.notification.key == notification.key:
                card.set_notification(notification)
                card.start_timer(self._timeout_ms(notification))
                self._layout_cards()
                return True

        for i, queued in enumerate(self._queue):
            if queued.key == notification.key:
                self._queue[i] = notification
                return True

        return False

    def _add_card(self, notification: Notification) -> None:
        card = NotificationWidget(notification, self)
        card.closeRequested.connect(self._on_close_requested)

        self._cards.append(card)
        self._stack.append(card)
        self._layout_cards()

        target = card.pos()
        card.move(target + QPoint(0, self.SLIDE_OFFSET))

        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(0.0)
        card.setGraphicsEffect(effect)

        slide = QPropertyAnimation(card, b"pos", card)
        slide.setDuration(self.ANIMATION_MS)
        slide.setStartValue(card.pos())
        slide.setEndValue(target)
        slide.setEasingCurve(QEasingCurve.Type.OutQuad)
        slide.finished.connect(lambda: self._slides.pop(card, None))
        self._slides[card] = slide

        fade = QPropertyAnimation(effect, b"opacity", card)
        fade.setDuration(self.ANIMATION_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutQuad)
        fade.finished.connect(lambda: effect.setEnabled(False))

        card.show()
        slide.start()
        fade.start()

        card.set_paused(self._paused)
        card.start_timer(self._timeout_ms(notification))

    @Slot(object)
    def _on_close_requested(self, card: NotificationWidget) -> None:
        if card not in self._cards:
            return

        self._cards.remove(card)
        card.stop_timer()
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        if (slide := self._slides.pop(card, None)) is not None:
            slide.stop()

        effect = QGraphicsOpacityEffect(card)
        card.setGraphicsEffect(effect)

        fade = QPropertyAnimation(effect, b"opacity", card)
        fade.setDuration(self.ANIMATION_MS)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutQuad)
        fade.finished.connect(lambda: self._on_card_faded(card))
        fade.start()

        while self._queue and len(self._cards) < SettingsManager.global_settings.notification_max_visible:
            self._add_card(self._queue.popleft())

        self._layout_cards()

    def _on_card_faded(self, card: NotificationWidget) -> None:
        if card in self._stack:
            self._stack.remove(card)

        card.deleteLater()
        self._layout_cards()

    def _layout_cards(self) -> None:
        if not self._stack:
            self.hide()
            return

        heights = [card.fitted_height() for card in self._stack]
        height = sum(heights) + self.SPACING * (len(heights) - 1)

        old_top = self.y()
        self.setGeometry(
            self._anchor.x() - self.MARGIN - NotificationWidget.WIDTH,
            self._anchor.y() - self.MARGIN - height,
            NotificationWidget.WIDTH,
            height,
        )
        # Child positions are relative to this widget, so a running slide must follow the move of its top edge.
        shift = QPoint(0, old_top - self.y())

        y = height
        for card, card_height in zip(reversed(self._stack), reversed(heights)):
            card.setFixedHeight(card_height)
            y -= card_height

            if (slide := self._slides.get(card)) is not None:
                slide.setStartValue(cast(QPoint, slide.startValue()) + shift)
                slide.setEndValue(QPoint(0, y))
            else:
                card.move(0, y)

            y -= self.SPACING

        self.show()
        self.raise_()

    @staticmethod
    def _timeout_ms(notification: Notification) -> int:
        if notification.timeout_ms is not None:
            return max(notification.timeout_ms, 0)

        if notification.severity is Notification.Severity.ERROR:
            return 0

        return SettingsManager.global_settings.notification_timeout

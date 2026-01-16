from collections.abc import Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import QBrush, QColor, QPainter, QPaintEvent, QPalette, QPen, QShowEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...assets import app_icon


class SegmentedControl(QWidget):
    """
    A segmented control widget for binary choices.

    Emits segmentChanged signal with the index of the selected segment.
    """

    segmentChanged = Signal(int)

    def __init__(
        self,
        labels: Sequence[str],
        parent: QWidget | None = None,
        direction: QBoxLayout.Direction = QBoxLayout.Direction.LeftToRight,
    ) -> None:
        super().__init__(parent)

        self.current_layout = QBoxLayout(direction, self)
        self.current_layout.setContentsMargins(2, 2, 2, 2)
        self.current_layout.setSpacing(2)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.buttons = list[QPushButton]()

        for i, label in enumerate(labels):
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

            self.button_group.addButton(btn, i)
            self.buttons.append(btn)
            self.current_layout.addWidget(btn)

        if self.buttons:
            self.buttons[0].setChecked(True)

        self.button_group.idClicked.connect(self._on_button_clicked)
        self._update_button_colors()

    @property
    def index(self) -> int:
        return self.button_group.checkedId()

    @index.setter
    def index(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)
            self._update_button_colors()

    def _update_button_colors(self) -> None:
        states = {
            True: QPalette.ColorRole.Mid,
            False: QPalette.ColorRole.ToolTipText,
        }

        for btn in self.buttons:
            palette = btn.palette()
            palette.setColor(QPalette.ColorRole.ButtonText, palette.color(states[btn.isChecked()]))
            btn.setPalette(palette)

    def _on_button_clicked(self, button_id: int) -> None:
        self._update_button_colors()
        self.segmentChanged.emit(button_id)


class DockButton(QToolButton):
    """Dock toggle button styled as a splitter handle."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(False)  # Dock is hidden by default
        self.setFixedWidth(6)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Toggle dock panel")
        self.setStyleSheet("""
            QToolButton {
                background-color: palette(mid);
                border: none;
                border-radius: 0px;
            }
            QToolButton:hover {
                background-color: palette(highlight);
            }
            QToolButton:checked {
                background-color: palette(mid);
            }
            QToolButton:checked:hover {
                background-color: palette(highlight);
            }
        """)


class CustomLoadingPage(QWidget):
    """Custom loading page with a bouncing icon and progress bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.loading_layout = QVBoxLayout(self)
        self.loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.setFixedWidth(500)
        self.progress_bar.setTextVisible(False)

        self.icon_label = QLabel(self)
        self.icon_label.setPixmap(app_icon().pixmap(QSize(64, 64)))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_layout.addWidget(self.icon_label)
        self.loading_layout.addWidget(QLabel("Loading...", self))
        self.loading_layout.addWidget(self.progress_bar)

        self.icon_animation = QPropertyAnimation(self.icon_label, b"pos", self)
        self.icon_animation.setDuration(1500)
        self.icon_animation.setEasingCurve(QEasingCurve.Type.InBounce)
        self.icon_animation.setLoopCount(-1)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)

        QTimer.singleShot(0, self._start_animation)

    def _start_animation(self) -> None:
        start_pos = self.icon_label.pos()
        end_pos = QPoint(start_pos.x(), start_pos.y() - 30)

        self.icon_animation.setStartValue(start_pos)
        self.icon_animation.setKeyValueAt(0.5, end_pos)
        self.icon_animation.setEndValue(start_pos)
        self.icon_animation.start()


class AnimatedToggle(QCheckBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        palette = self.palette()

        self.border_unchecked_color = palette.color(QPalette.ColorRole.Accent)

        self.bar_color = palette.color(QPalette.ColorRole.Base)
        self.bar_checked_color = palette.color(QPalette.ColorRole.Accent)

        self.handle_color = palette.color(QPalette.ColorRole.Button)
        self.handle_checked_color = palette.color(QPalette.ColorRole.Highlight)

        self.setContentsMargins(8, 0, 8, 0)
        self.handle_position = 0.0

        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.setDuration(150)
        self.animation.valueChanged.connect(self._on_value_changed)

        self.stateChanged.connect(self._setup_animation)

    def sizeHint(self) -> QSize:
        return QSize(58, 45)

    def hitButton(self, pos: QPoint) -> bool:
        return self.contentsRect().contains(pos)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Sync handle position with initial checked state without animation
        self.handle_position = 1.0 if self.isChecked() else 0.0

    def paintEvent(self, e: QPaintEvent) -> None:
        cont_rect = self.contentsRect()
        handle_radius = round(0.24 * cont_rect.height())
        t = self.handle_position

        bar_color = self._interpolate_color(self.bar_color, self.bar_checked_color, t)
        handle_color = self._interpolate_color(self.handle_color, self.handle_checked_color, t)

        # Subtle pulse effect: handle stretches slightly mid-animation
        pulse = 1.0 + 0.15 * (1.0 - abs(2.0 * t - 1.0))  # peaks at t=0.5
        handle_width = handle_radius * pulse
        handle_height = handle_radius

        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Draw bar
            p.setPen(Qt.PenStyle.NoPen)
            bar_rect = QRectF(0, 0, cont_rect.width() - handle_radius, 0.40 * cont_rect.height())
            bar_rect.moveCenter(cont_rect.center())
            rounding = bar_rect.height() / 2

            p.setBrush(QBrush(bar_color))
            p.drawRoundedRect(bar_rect, rounding, rounding)

            # Draw handle
            trail_length = cont_rect.width() - 2 * handle_radius
            x_pos = cont_rect.x() + handle_radius + trail_length * t

            # Slight border when unchecked (fades out during animation)
            if t < 1.0:
                pen = QPen(self.border_unchecked_color)
                pen.setWidthF(1.0 - t)  # Fade out the border
                p.setPen(pen)
            else:
                p.setPen(Qt.PenStyle.NoPen)

            p.setBrush(QBrush(handle_color))
            p.drawEllipse(QPointF(x_pos, bar_rect.center().y()), handle_width, handle_height)

    @Slot(int)
    def _setup_animation(self, value: int) -> None:
        self.animation.stop()
        self.animation.setStartValue(self.handle_position)
        self.animation.setEndValue(1.0 if value else 0.0)
        self.animation.start()

    @Slot(float)
    def _on_value_changed(self, value: float) -> None:
        self.handle_position = value
        self.update()

    def _interpolate_color(self, c1: QColor, c2: QColor, t: float) -> QColor:
        return QColor(
            round(c1.red() + (c2.red() - c1.red()) * t),
            round(c1.green() + (c2.green() - c1.green()) * t),
            round(c1.blue() + (c2.blue() - c1.blue()) * t),
            round(c1.alpha() + (c2.alpha() - c1.alpha()) * t),
        )

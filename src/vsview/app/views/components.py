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
from PySide6.QtGui import QBrush, QColor, QPainter, QPaintEvent, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...assets import loading_icon


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
        self.icon_label.setPixmap(loading_icon().pixmap(QSize(150, 150)))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setMinimumHeight(150 + 30)  # Reserve space for bounce
        self.loading_layout.addWidget(self.icon_label)
        self.loading_layout.addWidget(QLabel("Loading...", self))
        self.loading_layout.addWidget(self.progress_bar)

        self.icon_animation = QVariantAnimation(self)
        self.icon_animation.setDuration(1500)
        self.icon_animation.setEasingCurve(QEasingCurve.Type.InBounce)
        self.icon_animation.setLoopCount(-1)
        self.icon_animation.valueChanged.connect(self._update_bounce)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)

        QTimer.singleShot(0, self._start_animation)

    def _start_animation(self) -> None:
        self.icon_animation.setStartValue(0)
        self.icon_animation.setKeyValueAt(0.5, 30)
        self.icon_animation.setEndValue(0)
        self.icon_animation.start()

    def _update_bounce(self, value: int) -> None:
        self.icon_label.setContentsMargins(0, 0, 0, int(value))


class AnimatedToggle(QCheckBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        palette = self.palette()

        self.bar = palette.color(QPalette.ColorRole.Light)
        self.bar_disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Light).darker()
        self.bar_checked = palette.color(QPalette.ColorRole.Accent)
        self.bar_checked_disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Accent)

        self.handle = palette.color(QPalette.ColorRole.Midlight)
        self.handle_disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Midlight).darker(225)
        self.handle_checked = palette.color(QPalette.ColorRole.Highlight)
        self.handle_checked_disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Accent).darker()

        self.setContentsMargins(8, 0, 8, 0)
        self.handle_position = 0.0

        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.setDuration(150)
        self.animation.valueChanged.connect(self._on_value_changed)

        self.stateChanged.connect(self._setup_animation)

    def sizeHint(self) -> QSize:
        return QSize(58, 30)

    def hitButton(self, pos: QPoint) -> bool:
        return self.contentsRect().contains(pos)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Sync handle position with initial checked state without animation
        self.handle_position = 1.0 if self.isChecked() else 0.0

    def paintEvent(self, e: QPaintEvent) -> None:
        cont_rect = self.contentsRect()
        handle_radius = round(0.40 * cont_rect.height())
        t = self.handle_position

        if self.isEnabled():
            bar = self._interpolate_color(self.bar, self.bar_checked, t)
            handle = self._interpolate_color(self.handle, self.handle_checked, t)
        else:
            bar = self._interpolate_color(self.bar_disabled, self.bar_checked_disabled, t)
            handle = self._interpolate_color(self.handle_disabled, self.handle_checked_disabled, t)

        # Subtle pulse effect: handle stretches slightly mid-animation
        pulse = 1.0 + 0.15 * (1.0 - abs(2.0 * t - 1.0))  # peaks at t=0.5
        handle_width = handle_radius * pulse
        handle_height = handle_radius

        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)

            bar_rect = QRectF(0, 0, cont_rect.width() - handle_radius, 0.60 * cont_rect.height())
            bar_rect.moveCenter(cont_rect.center())
            rounding = bar_rect.height() / 2

            p.setBrush(QBrush(bar))
            p.drawRoundedRect(bar_rect, rounding, rounding)

            # Draw handle
            trail_length = cont_rect.width() - 2 * handle_radius
            x_pos = cont_rect.x() + handle_radius + trail_length * t

            p.setBrush(QBrush(handle))
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


class Accordion(QFrame):
    HEADER_STYLE = """
    QToolButton {
        border: none;
        padding: 8px 12px;
        font-weight: bold;
        text-align: left;
    }
    QToolButton:hover {
        background-color: palette(midlight);
    }
    """

    def __init__(self, title: str, parent: QWidget | None = None, collapsed: bool = False) -> None:
        super().__init__(parent)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.header = QToolButton(self)
        self.header.setText(f"  {title}")
        self.header.setCheckable(True)
        self.header.setChecked(not collapsed)
        self.header.setArrowType(Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(
            self.header.sizePolicy().horizontalPolicy(),
            self.header.sizePolicy().verticalPolicy(),
        )
        self.header.setStyleSheet(self.HEADER_STYLE)
        self.header.toggled.connect(self.on_toggle)
        self.main_layout.addWidget(self.header)

        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 8, 12, 12)
        self.content_layout.setSpacing(8)
        self.main_layout.addWidget(self.content)

        self.animation = QPropertyAnimation(self.content, b"maximumHeight")
        # FIXME: A larger duration just seems to increase flickering and ghosting during collapsing
        self.animation.setDuration(80)
        self.animation.setEasingCurve(QEasingCurve.Type.Linear)

        if collapsed:
            self.content.setMaximumHeight(0)

    def on_toggle(self, checked: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

        self.animation.stop()

        if checked:
            self.content.setMaximumHeight(0)
            self.animation.setStartValue(0)
            self.animation.setEndValue(self.content.sizeHint().height())
        else:
            # Collapse
            self.content.setMaximumHeight(self.content.sizeHint().height())
            self.animation.setStartValue(self.content.sizeHint().height())
            self.animation.setEndValue(0)

        self.animation.start()

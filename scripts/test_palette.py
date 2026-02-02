import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStyleFactory,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class PaletteVisualizer(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PySide6 Palette & Style Visualizer")
        self.resize(1200, 900)

        # Main Layout
        self.main_layout: QVBoxLayout = QVBoxLayout(self)

        # 1. Control Panel
        self.setup_controls()

        # 2. Main Content Area (Split between Swatches and Live Preview)
        content_layout = QHBoxLayout()
        self.main_layout.addLayout(content_layout)

        # --- Left: Color Swatches ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.swatch_container: QWidget = QWidget()
        self.swatch_grid: QGridLayout = QGridLayout(self.swatch_container)
        self.swatch_grid.setSpacing(10)
        self.scroll_area.setWidget(self.swatch_container)

        content_layout.addWidget(self.scroll_area, stretch=2)

        # --- Right: Live Preview ---
        self.preview_container = QGroupBox("Live Preview")
        self.preview_layout = QVBoxLayout(self.preview_container)
        self.setup_live_preview()
        content_layout.addWidget(self.preview_container, stretch=1)

        # Initial Render
        self.refresh_grid()

    def setup_controls(self) -> None:
        """Sets up the style selection and options."""
        control_group = QGroupBox("Configuration")
        control_layout = QHBoxLayout(control_group)

        # Style Selector
        style_label = QLabel("Application Style:")
        style_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        control_layout.addWidget(style_label)

        self.style_combo = QComboBox()
        self.style_combo.addItems(QStyleFactory.keys())

        # Set current style
        current_style = QApplication.style().objectName().lower()
        for i in range(self.style_combo.count()):
            if self.style_combo.itemText(i).lower() == current_style:
                self.style_combo.setCurrentIndex(i)
                break

        self.style_combo.currentTextChanged.connect(self.change_style)
        control_layout.addWidget(self.style_combo)

        # Dark Mode Toggle
        self.dark_mode_check = QCheckBox("Force Dark Palette")
        self.dark_mode_check.toggled.connect(self.apply_palette_override)
        control_layout.addWidget(self.dark_mode_check)

        control_layout.addStretch()
        self.main_layout.addWidget(control_group)

    def setup_live_preview(self) -> None:
        """Creates a set of standard widgets to see the palette in action."""
        layout = self.preview_layout

        # Group 1: Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("Primary Button"))
        btn_disabled = QPushButton("Disabled")
        btn_disabled.setEnabled(False)
        btn_layout.addWidget(btn_disabled)
        layout.addLayout(btn_layout)

        # Group 2: Inputs
        layout.addWidget(QLabel("Input Field:"))
        layout.addWidget(QLineEdit("Sample Text"))
        layout.addWidget(QSpinBox())

        # Group 3: Selection
        layout.addWidget(QCheckBox("Checkbox Option"))
        radio_layout = QHBoxLayout()
        radio_layout.addWidget(QRadioButton("Option A"))
        radio_layout.addWidget(QRadioButton("Option B"))
        layout.addLayout(radio_layout)

        # Group 4: Tabs
        tabs = QTabWidget()
        t1 = QWidget()
        t1_layout = QVBoxLayout(t1)
        t1_layout.addWidget(QLabel("Content inside Tab 1"))
        t2 = QWidget()
        tabs.addTab(t1, "Tab 1")
        tabs.addTab(t2, "Tab 2")
        layout.addWidget(tabs)

        # Group 5: Text Area
        text_edit = QTextEdit()
        text_edit.setPlainText("Multi-line text editor.\nCheck selection color highlight.")
        layout.addWidget(text_edit)

        # Group 6: Progress
        pbar = QProgressBar()
        pbar.setValue(65)
        layout.addWidget(pbar)

        layout.addStretch()

    def change_style(self, style_name: str) -> None:
        """Updates the application style."""
        from typing import cast

        app = QApplication.instance()
        if app:
            cast(QApplication, app).setStyle(style_name)
            self.apply_palette_override(self.dark_mode_check.isChecked())

    def apply_palette_override(self, is_dark: bool) -> None:
        """Applies custom palette or restores default."""
        app = QApplication.instance()
        if not app:
            return

        if is_dark:
            # Dark Palette optimized for things like Fusion
            dark_palette = QPalette()

            # Base Colors
            dark_palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
            dark_palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
            dark_palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
            dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))

            # Tooltips
            dark_palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
            dark_palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)

            # Text
            dark_palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
            dark_palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(127, 127, 127))

            # Buttons
            dark_palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
            dark_palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
            dark_palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)

            # Links/Highlights
            # Using a nice blue for highlight/accent
            accent_color = QColor(42, 130, 218)
            dark_palette.setColor(QPalette.ColorRole.Link, accent_color)
            dark_palette.setColor(QPalette.ColorRole.Highlight, accent_color)
            dark_palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)

            # Qt6 Accent
            if hasattr(QPalette.ColorRole, "Accent"):
                dark_palette.setColor(QPalette.ColorRole.Accent, accent_color)

            self.setPalette(dark_palette)
        else:
            # Restore standard palette by handling the widget attribute and inheriting from app
            self.setPalette(QPalette())
            self.setAttribute(Qt.WidgetAttribute.WA_SetPalette, False)

        self.refresh_grid()

    def refresh_grid(self) -> None:
        """Rebuilds the color grid."""
        # Clear existing
        while self.swatch_grid.count():
            item = self.swatch_grid.takeAt(0)
            if (item := self.swatch_grid.takeAt(0)) and (w := item.widget()):
                w.deleteLater()

        # Use the widget's palette (which captures the override or the app style)
        palette = self.palette()

        # Define Roles
        roles = [
            (QPalette.ColorRole.Window, "Window"),
            (QPalette.ColorRole.WindowText, "WindowText"),
            (QPalette.ColorRole.Base, "Base"),
            (QPalette.ColorRole.AlternateBase, "AlternateBase"),
            (QPalette.ColorRole.ToolTipBase, "ToolTipBase"),
            (QPalette.ColorRole.ToolTipText, "ToolTipText"),
            (QPalette.ColorRole.PlaceholderText, "PlaceholderText"),
            (QPalette.ColorRole.Text, "Text"),
            (QPalette.ColorRole.Button, "Button"),
            (QPalette.ColorRole.ButtonText, "ButtonText"),
            (QPalette.ColorRole.BrightText, "BrightText"),
            (QPalette.ColorRole.Light, "Light"),
            (QPalette.ColorRole.Midlight, "Midlight"),
            (QPalette.ColorRole.Dark, "Dark"),
            (QPalette.ColorRole.Mid, "Mid"),
            (QPalette.ColorRole.Shadow, "Shadow"),
            (QPalette.ColorRole.Highlight, "Highlight"),
            (QPalette.ColorRole.HighlightedText, "HighlightedText"),
            (QPalette.ColorRole.Link, "Link"),
            (QPalette.ColorRole.LinkVisited, "LinkVisited"),
        ]

        # Add Accent if available (Qt 6.6+)
        if hasattr(QPalette.ColorRole, "Accent"):
            roles.append((QPalette.ColorRole.Accent, "Accent"))

        roles.append((QPalette.ColorRole.NoRole, "NoRole"))

        # Headers
        headers = ["Role", "Active", "Inactive", "Disabled"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.swatch_grid.addWidget(lbl, 0, col)

        # Content
        row = 1
        groups = [
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ]

        for role_enum, role_name in roles:
            # Label
            lbl = QLabel(role_name)
            lbl.setFont(QFont("Segoe UI", 9))
            self.swatch_grid.addWidget(lbl, row, 0)

            # Swatches
            for col, group in enumerate(groups, start=1):
                color = palette.color(group, role_enum)
                swatch = self.create_swatch(color)
                self.swatch_grid.addWidget(swatch, row, col)

            row += 1

    def create_swatch(self, color: QColor) -> QFrame:
        """Creates a visual swatch for a color."""
        frame = QFrame()
        frame.setFixedSize(160, 50)

        # Determine contrast text color
        text_color = "black" if color.lightness() > 128 else "white"
        alpha_text = f" A:{color.alpha()}" if color.alpha() < 255 else ""

        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {color.name()};
                border: 1px solid #888;
                border-radius: 4px;
            }}
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Hex / RGB Info
        info = f"{color.name().upper()}{alpha_text}\nRGB: {color.red()},{color.green()},{color.blue()}"
        lbl = QLabel(info)
        lbl.setStyleSheet(f"""
            color: {text_color};
            background: transparent;
            font-family: Consolas;
            font-size: 10px;
            font-weight: bold;
        """)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        return frame


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Default to a reasonable style if possible
    app.setStyle("windows11")

    window = PaletteVisualizer()
    window.show()

    sys.exit(app.exec())

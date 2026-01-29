from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import Self

from jetpytools import cachedproperty, classproperty
from pygments.styles import get_all_styles
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...assets import ICON_PROVIDERS, IconName, IconReloadMixin
from .models import GlobalSettings, LocalSettings, SettingEntry, ShortcutConfig, extract_settings

# Style for shortcut editors with conflicts
# Must target internal QLineEdit since QKeySequenceEdit is a compound widget
CONFLICT_STYLE = "QKeySequenceEdit QLineEdit { border: 2px solid #e74c3c; }"
NORMAL_STYLE = ""

# Apparently missing from PySide6
QWIDGETSIZE_MAX = 16777215

logger = getLogger(__name__)


class SettingsSection(QFrame):
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

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._header = QToolButton(self)
        self._header.setText(f"  {title}")
        self._header.setCheckable(True)
        self._header.setChecked(not collapsed)
        self._header.setArrowType(Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow)
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setSizePolicy(
            self._header.sizePolicy().horizontalPolicy(),
            self._header.sizePolicy().verticalPolicy(),
        )
        self._header.setStyleSheet(self.HEADER_STYLE)
        self._header.toggled.connect(self._on_toggle)
        main_layout.addWidget(self._header)

        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 8, 12, 12)
        self._content_layout.setSpacing(8)
        main_layout.addWidget(self._content)

        self._animation = QPropertyAnimation(self._content, b"maximumHeight")
        # FIXME: A larger duration just seems to increase flickering and ghosting during collapsing
        # I've tried many things to fix that but alas
        self._animation.setDuration(80)
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)

        if collapsed:
            self._content.setMaximumHeight(0)

    def _on_toggle(self, checked: bool) -> None:
        self._header.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

        self._animation.stop()

        if checked:
            self._content.setMaximumHeight(0)
            self._animation.setStartValue(0)
            self._animation.setEndValue(self._content.sizeHint().height())
        else:
            # Collapse
            self._content.setMaximumHeight(self._content.sizeHint().height())
            self._animation.setStartValue(self._content.sizeHint().height())
            self._animation.setEndValue(0)

        self._animation.start()

    def add_widget(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)

    def add_form_layout(self) -> QFormLayout:
        # This form has to be without parent, otherwise we're getting an error
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self._content_layout.addLayout(form)
        return form


class SettingsTab(QScrollArea):
    def __init__(self, tab_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tab_name = tab_name

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        # Container widget
        self.container = QWidget(self)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(16, 16, 16, 16)
        self.container_layout.setSpacing(16)

        self.setWidget(self.container)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.finalize()

    def add_section(self, section: SettingsSection) -> None:
        self.container_layout.addWidget(section)

    def finalize(self) -> None:
        """Add stretch at the end to push sections to the top."""
        self.container_layout.addStretch(1)


@dataclass(slots=True, repr=False, eq=False, match_args=False)
class ShortcutWidgets:
    """UI widgets and state for shortcut configuration."""

    editors: dict[str, QKeySequenceEdit] = field(default_factory=dict)
    reset_buttons: dict[str, QToolButton] = field(default_factory=dict)
    conflict_labels: dict[str, QLabel] = field(default_factory=dict)
    original_shortcuts: dict[str, str] = field(default_factory=dict)


class SettingsDialog(QDialog, IconReloadMixin):
    def __init__(self, script_path: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._script_path = script_path

        # Store original settings for cancel
        self._original_global = self._settings_manager.global_settings.model_copy(deep=True)
        self._original_local = (
            self._settings_manager.get_local_settings(script_path).model_copy(deep=True) if script_path else None
        )

        self._global_widgets = dict[str, QWidget]()
        self._local_widgets = dict[str, QWidget]()

        self.setWindowTitle("Settings")
        self.setMinimumSize(600, 500)
        self.resize(700, 600)

        self._setup_ui()
        self._load_settings_to_ui()

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    def deleteLater(self) -> None:
        self._global_widgets.clear()
        self._local_widgets.clear()
        super().deleteLater()

    @classproperty.cached
    @classmethod
    def global_settings_registry(cls) -> list[SettingEntry]:
        """Registry of global settings extracted from GlobalSettings."""
        return extract_settings(GlobalSettings)

    @classproperty.cached
    @classmethod
    def local_settings_registry(cls) -> list[SettingEntry]:
        """Registry of local settings extracted from LocalSettings."""
        return extract_settings(LocalSettings)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)

        self.tab_widget = QTabWidget(self)
        layout.addWidget(self.tab_widget)

        self.global_tab = self._create_global_tab()
        self.local_tab = self._create_local_tab()
        self.shortcuts_tab = self._create_shortcuts_tab()

        # Update shortcut conflicts
        self._on_shortcut_changed()

        self.tab_widget.addTab(self.global_tab, "Global")
        self.tab_widget.addTab(self.local_tab, "Local")
        self.tab_widget.addTab(self.shortcuts_tab, "Shortcuts")

        # Disable local tab if no script path
        if self._script_path is None:
            self.tab_widget.setTabEnabled(1, False)
            self.tab_widget.setTabToolTip(1, "Load a script to configure local settings")

        # Can't set the parent on this one
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(12, 0, 12, 0)
        button_layout.addStretch()
        button_layout.addStretch()

        # Manually create button box to control order
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        apply_btn = self.button_box.addButton("Apply", QDialogButtonBox.ButtonRole.ActionRole)
        apply_btn.clicked.connect(self._on_apply)
        self.button_box.rejected.connect(self.reject)
        button_layout.addWidget(self.button_box)

        layout.addLayout(button_layout)

    def _create_global_tab(self) -> SettingsTab:
        # Build sections from registry
        sections = dict[str, tuple[SettingsSection, QFormLayout]]()

        with SettingsTab("Global", self) as tab:
            for entry in self.global_settings_registry:
                # Get or create section
                if entry.section not in sections:
                    section = SettingsSection(entry.section, tab)
                    form = section.add_form_layout()
                    sections[entry.section] = (section, form)
                    tab.add_section(section)

                _, form = sections[entry.section]

                # Create widget from metadata
                widget = entry.metadata.create_widget(self)
                self._global_widgets[entry.key] = widget

                # Special handling for icon_provider and icon_weight dropdowns
                if entry.key == "appearance.icon_provider" and isinstance(widget, QComboBox):
                    widget.clear()
                    for provider_id, provider in ICON_PROVIDERS.items():
                        widget.addItem(provider.name, provider_id)

                    widget.currentIndexChanged.connect(self._on_provider_changed)
                elif entry.key == "appearance.icon_weight":
                    # Will be populated when provider is loaded/changed
                    pass
                elif entry.key == "appearance.editor_theme" and isinstance(widget, QComboBox):
                    widget.clear()
                    for style_name in sorted(get_all_styles()):
                        # Convert style name to title case for display
                        display_name = style_name.replace("-", " ").replace("_", " ").title()
                        widget.addItem(display_name, style_name)

                form.addRow(f"{entry.metadata.label}:", widget)

        return tab

    def _on_provider_changed(self) -> None:
        provider_combo = self._global_widgets.get("appearance.icon_provider")
        weight_combo = self._global_widgets.get("appearance.icon_weight")

        if not isinstance(provider_combo, QComboBox) or not isinstance(weight_combo, QComboBox):
            logger.warning("Icon provider or weight combo not found")
            return

        if not (provider_id := provider_combo.currentData()):
            logger.warning("Icon provider not selected")
            return

        # Store current weight if valid for new provider
        current_weight = weight_combo.currentData()
        provider = ICON_PROVIDERS[provider_id]

        # Repopulate weight dropdown
        weight_combo.clear()
        for weight in provider.weights:
            weight_combo.addItem(weight.title(), weight)

        # Try to restore previous weight, otherwise use provider default
        index = weight_combo.findData(current_weight)
        if index >= 0:
            weight_combo.setCurrentIndex(index)
        else:
            weight_combo.setCurrentIndex(weight_combo.findData(provider.default_weight))

    def _create_shortcuts_tab(self) -> SettingsTab:
        from .shortcuts import ShortcutManager

        self._shortcut_widgets = ShortcutWidgets()

        grouped_actions = dict[str, list[str]]()

        for aid, definition in ShortcutManager.definitions.items():
            # Extract first component from action ID (e.g., "workspace" from "workspace.loader.view.reload")
            first_component = aid.split(".")[0]

            # Check if this is a plugin shortcut
            if first_component in self._plugin_display_names:
                group_name = f"Plugin - {self._plugin_display_names[first_component]}"
            else:
                group_name = first_component.title()

            grouped_actions.setdefault(group_name, []).append(aid)

        with SettingsTab("Shortcuts", self) as tab:
            # Create a section for each group
            for group_name, actions in grouped_actions.items():
                shortcuts_section = SettingsSection(group_name, tab)
                form = shortcuts_section.add_form_layout()

                # Create an editor for each shortcut action in this group
                for aid in actions:
                    definition = ShortcutManager.definitions[aid]
                    current_key = self._settings_manager.global_settings.get_key(aid)
                    self._shortcut_widgets.original_shortcuts[aid] = current_key

                    row_widget = QWidget(self)
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(4)

                    editor = QKeySequenceEdit(
                        self,
                        keySequence=QKeySequence(current_key),
                        clearButtonEnabled=True,
                        maximumSequenceLength=1,
                    )
                    editor.keySequenceChanged.connect(self._on_shortcut_changed)
                    row_layout.addWidget(editor)

                    reset_btn = self.make_tool_button(
                        IconName.ARROW_U_TOP_LEFT, f"Reset to default: {definition.default_key or 'None'}"
                    )
                    reset_btn.clicked.connect(lambda checked=False, aid=aid: self._reset_shortcut(aid))
                    row_layout.addWidget(reset_btn)

                    conflict_label = QLabel(self)
                    conflict_label.setFixedWidth(20)
                    conflict_label.setToolTip("This shortcut conflicts with another action")
                    row_layout.addWidget(conflict_label)

                    row_layout.addStretch()

                    self._shortcut_widgets.editors[aid] = editor
                    self._shortcut_widgets.reset_buttons[aid] = reset_btn
                    self._shortcut_widgets.conflict_labels[aid] = conflict_label
                    form.addRow(f"{definition.label}:", row_widget)

                tab.add_section(shortcuts_section)

        return tab

    def _create_local_tab(self) -> SettingsTab:
        with SettingsTab("Local", self) as tab:
            # Script info section
            if self._script_path:
                info_section = SettingsSection("Script", tab)
                form = info_section.add_form_layout()

                path_label = QLabel(str(self._script_path), self)
                path_label.setWordWrap(True)
                form.addRow("Path:", path_label)

                tab.add_section(info_section)

            # Build sections from registry
            sections = dict[str, tuple[SettingsSection, QFormLayout]]()

            for entry in self.local_settings_registry:
                # Get or create section
                if entry.section not in sections:
                    section = SettingsSection(entry.section, tab)
                    form = section.add_form_layout()
                    sections[entry.section] = (section, form)
                    tab.add_section(section)

                _, form = sections[entry.section]

                # Create widget from metadata
                widget = entry.metadata.create_widget(self)
                self._local_widgets[entry.key] = widget
                form.addRow(f"{entry.metadata.label}:", widget)

        return tab

    def _load_settings_to_ui(self) -> None:
        global_settings = self._settings_manager.global_settings

        # Load registry-driven global settings
        for entry in self.global_settings_registry:
            widget = self._global_widgets[entry.key]
            value = global_settings.get_nested_value(entry.key)
            entry.metadata.load_value(widget, value)

        # Populate weight dropdown based on selected provider, then reload weight value
        self._on_provider_changed()

        if weight_widget := self._global_widgets.get("appearance.icon_weight"):
            for entry in self.global_settings_registry:
                if entry.key == "appearance.icon_weight":
                    entry.metadata.load_value(weight_widget, global_settings.appearance.icon_weight)
                    break

        # Load local settings if available
        if self._script_path:
            local_settings = self._settings_manager.get_local_settings(self._script_path)
            for entry in self.local_settings_registry:
                widget = self._local_widgets[entry.key]
                value = local_settings.get_nested_value(entry.key)
                entry.metadata.load_value(widget, value)

    def _get_global_settings_from_ui(self) -> GlobalSettings:
        # Build shortcuts from UI editors
        shortcuts = [
            ShortcutConfig(action_id=aid, key_sequence=self._shortcut_widgets.editors[aid].keySequence().toString())
            for aid in self._shortcut_widgets.editors
        ]

        # Use existing settings as base to preserve hidden fields
        data = self._settings_manager.global_settings.model_dump()

        for entry in self.global_settings_registry:
            widget = self._global_widgets[entry.key]
            value = entry.metadata.get_value(widget)
            GlobalSettings.set_nested_value(data, entry.key, value)

        # Add shortcuts (not in registry) and build settings dynamically
        data["shortcuts"] = shortcuts

        return GlobalSettings.model_validate(data)

    def _get_local_settings_from_ui(self) -> LocalSettings:
        if not self._script_path:
            raise ValueError("No script path provided")

        local_settings = self._settings_manager.get_local_settings(self._script_path)

        # Use existing settings as base to preserve hidden fields
        data = local_settings.model_dump()

        for entry in self.local_settings_registry:
            widget = self._local_widgets[entry.key]
            value = entry.metadata.get_value(widget)
            LocalSettings.set_nested_value(data, entry.key, value)

        return LocalSettings.model_validate(data)

    def _on_apply(self) -> None:
        global_settings = self._get_global_settings_from_ui()
        self._settings_manager.save_global(global_settings)

        if self._script_path:
            self._settings_manager.save_local(self._script_path, self._get_local_settings_from_ui())

        self.accept()

    def _on_shortcut_changed(self) -> None:
        from .shortcuts import ShortcutManager

        key_to_actions = dict[str, list[str]]()

        for aid, editor in self._shortcut_widgets.editors.items():
            key = editor.keySequence().toString()
            # Exclude plugin shortcuts from built-in shortcut conflict detection
            if key and aid.split(".")[0] not in self._plugin_display_names:
                key_to_actions.setdefault(key, []).append(aid)

        # Find conflicting keys (assigned to more than one action)
        conflicting_keys = {key for key, actions in key_to_actions.items() if len(actions) > 1}

        # Update each editor's conflict state
        for aid, editor in self._shortcut_widgets.editors.items():
            key = editor.keySequence().toString()
            has_conflict = key in conflicting_keys
            conflict_label = self._shortcut_widgets.conflict_labels[aid]

            conflict_label.setText("⚠" if has_conflict else "")
            conflict_label.setStyleSheet("color: #E74C3C; font-weight: bold;" if has_conflict else "")
            editor.setStyleSheet(CONFLICT_STYLE if has_conflict else NORMAL_STYLE)

            # Update tooltip to show what it conflicts with
            if has_conflict:
                conflicting_with = [
                    ShortcutManager.definitions[other_aid].label
                    for other_aid in key_to_actions[key]
                    if other_aid != aid
                ]
                conflict_label.setToolTip(f"Conflicts with: {', '.join(conflicting_with)}")

    @cachedproperty
    def _default_shortcuts(self) -> dict[str, str]:
        from .shortcuts import ShortcutManager

        return {aid: d.default_key for aid, d in ShortcutManager.definitions.items()}

    @cachedproperty
    def _plugin_display_names(self) -> dict[str, str]:
        from ..plugins.manager import PluginManager

        return {plugin.identifier: plugin.display_name for plugin in PluginManager.all_plugins}

    def _reset_shortcut(self, aid: str) -> None:
        default_key = self._default_shortcuts.get(aid, "")
        self._shortcut_widgets.editors[aid].setKeySequence(QKeySequence(default_key))

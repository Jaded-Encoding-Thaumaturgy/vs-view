import os
from dataclasses import dataclass, field
from logging import getLogger
from pathlib import Path
from typing import Self, override

from jetpytools import cachedproperty, classproperty
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, Slot
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTimeEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...assets import ICON_PROVIDERS, IconName
from ..icon import IconReloadMixin
from ..views.components import Accordion
from .manager import SettingsManager
from .models import GlobalSettings, LocalSettings, SettingEntry, ShortcutConfig, extract_settings
from .shortcuts import ShortcutManager

# Style for shortcut editors with conflicts
# Must target internal QLineEdit since QKeySequenceEdit is a compound widget
CONFLICT_STYLE = "QKeySequenceEdit QLineEdit { border: 2px solid #e74c3c; }"
NORMAL_STYLE = ""

# Apparently missing from PySide6
QWIDGETSIZE_MAX = 16777215

PLUGIN_PREFIX = "Plugin"

logger = getLogger(__name__)


class WheelEventFilter(QObject):
    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return super().eventFilter(watched, event)

        # Ignore wheel event from the current widget
        event.ignore()
        # Send the event to the parent widget container instead, e.g. the SettingsTab.
        if parent := watched.parent():
            QCoreApplication.sendEvent(parent, event)
        return True


class SettingsTab(QWidget):
    def __init__(self, tab_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tab_name = tab_name
        self._sections = list[tuple[QTreeWidgetItem, Accordion]]()
        self._updating_scroll = False
        self._updating_tree = False

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_layout.addWidget(self.splitter)

        # Left navigation tree
        self.tree = QTreeWidget(self.splitter)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(12)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setStyleSheet("QTreeWidget { border: none; } QTreeWidget::item { padding: 4px 2px; }")
        self.tree.currentItemChanged.connect(self._on_tree_selection_changed)

        # Right scroll area
        self.scroll_area = QScrollArea(self.splitter)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        # Container widget for scroll area
        self.container = QWidget(self.scroll_area)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(4, 8, 8, 8)
        self.container_layout.setSpacing(8)

        self.scroll_area.setWidget(self.container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.scroll_area)
        self.splitter.setCollapsible(0, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([200, 600])

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.finalize()

    def add_section(self, section: Accordion) -> None:
        self.container_layout.addWidget(section)

        title = section.header.text().strip()
        if title.startswith(f"{PLUGIN_PREFIX} - "):
            parts = title.removeprefix(f"{PLUGIN_PREFIX} - ").split(" - ")
            current_parent = self._get_or_create_parent_item("Plugins")

            for i, part in enumerate(parts):
                is_leaf = i == len(parts) - 1
                child = self._find_child_item(current_parent, part)
                if child is None:
                    child = QTreeWidgetItem(current_parent, [part])
                    current_parent.setExpanded(True)
                if is_leaf or child.data(0, Qt.ItemDataRole.UserRole) is None:
                    child.setData(0, Qt.ItemDataRole.UserRole, section)
                current_parent = child

            item = current_parent
        else:
            item = QTreeWidgetItem(self.tree, [title])
            item.setData(0, Qt.ItemDataRole.UserRole, section)

        self._sections.append((item, section))

    def finalize(self) -> None:
        """Add stretch at the end to push sections to the top."""
        self.container_layout.addStretch(1)
        if self._sections:
            self.tree.setCurrentItem(self._sections[0][0])

    def _get_or_create_parent_item(self, parent_title: str) -> QTreeWidgetItem:
        if item := self._find_child_item(self.tree, parent_title):
            return item
        parent_item = QTreeWidgetItem(self.tree, [parent_title])
        parent_item.setExpanded(True)
        return parent_item

    def _find_child_item(self, parent: QTreeWidget | QTreeWidgetItem, title: str) -> QTreeWidgetItem | None:
        if isinstance(parent, QTreeWidget):
            count = parent.topLevelItemCount()
            get_item = parent.topLevelItem
        else:
            count = parent.childCount()
            get_item = parent.child
        for i in range(count):
            item = get_item(i)
            if item and item.text(0) == title:
                return item
        return None

    @Slot(QTreeWidgetItem, QTreeWidgetItem)
    def _on_tree_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        if current is None or self._updating_tree:
            return

        section: Accordion | None = current.data(0, Qt.ItemDataRole.UserRole)
        if section is None:
            return

        if not section.header.isChecked():
            section.header.setChecked(True)

        self._updating_scroll = True
        try:
            self.scroll_area.ensureWidgetVisible(section, 0, 12)
        finally:
            self._updating_scroll = False

    @Slot(int)
    def _on_scroll_changed(self, value: int) -> None:
        if self._updating_scroll or not self._sections:
            return

        viewport = self.scroll_area.viewport()
        viewport_top = viewport.rect().top()
        closest_item: QTreeWidgetItem | None = None
        min_diff = float("inf")

        for item, section in self._sections:
            if not section.isVisible():
                continue
            section_y = section.mapTo(viewport, QPoint(0, 0)).y()
            diff = abs(section_y - viewport_top)
            if section_y <= viewport_top + 50 and diff < min_diff:
                min_diff = diff
                closest_item = item

        if closest_item is None and self._sections:
            closest_item = self._sections[0][0]

        if closest_item and closest_item != self.tree.currentItem():
            self._updating_tree = True
            try:
                self.tree.setCurrentItem(closest_item)
            finally:
                self._updating_tree = False


class ShortcutEditor(QKeySequenceEdit):
    """
    Subclass of QKeySequenceEdit that allows capturing Tab.
    """

    @override
    def event(self, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
        ):
            self.setKeySequence(QKeySequence(Qt.Key.Key_Tab | event.modifiers().value))
            return True
        return super().event(event)


@dataclass(slots=True, repr=False, eq=False, match_args=False)
class ShortcutWidgets:
    """UI widgets and state for shortcut configuration."""

    editors: dict[str, ShortcutEditor] = field(default_factory=dict)
    reset_buttons: dict[str, QToolButton] = field(default_factory=dict)
    conflict_labels: dict[str, QLabel] = field(default_factory=dict)
    original_shortcuts: dict[str, str] = field(default_factory=dict)


class SettingsDialog(QDialog, IconReloadMixin):
    def __init__(self, script_path: os.PathLike[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._script_path = Path(script_path) if script_path else None

        # Store original settings for cancel
        self._original_global = SettingsManager.global_settings.model_copy(deep=True)
        self._original_local = (
            SettingsManager.get_local_settings(script_path).model_copy(deep=True) if script_path else None
        )

        self._global_widgets = dict[str, QWidget]()
        self._local_widgets = dict[str, QWidget]()
        self._wheel_filter = WheelEventFilter(self)

        self.setWindowTitle("Settings")
        self.setMinimumSize(750, 550)
        self.resize(950, 650)

        self._setup_ui()
        self._load_settings_to_ui()

        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    @override
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
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply,
            self,
        )
        self.button_box.accepted.connect(self._on_ok)
        self.button_box.rejected.connect(self.reject)

        if apply_btn := self.button_box.button(QDialogButtonBox.StandardButton.Apply):
            apply_btn.clicked.connect(self._on_apply)

        button_layout.addWidget(self.button_box)

        layout.addLayout(button_layout)

    def _apply_wheel_filter(self, widget: QWidget) -> None:
        if isinstance(widget, (QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit)):
            widget.installEventFilter(self._wheel_filter)

    def _create_global_tab(self) -> SettingsTab:
        # Build sections from registry
        sections = dict[str, tuple[Accordion, QFormLayout]]()

        with SettingsTab("Global", self) as tab:
            for entry in self.global_settings_registry:
                # Get or create section
                if entry.section not in sections:
                    section = Accordion(entry.section, tab)
                    form = section.add_form_layout()
                    sections[entry.section] = (section, form)
                    tab.add_section(section)

                _, form = sections[entry.section]

                # Create widget from metadata
                widget = entry.metadata.create_widget(self)
                self._global_widgets[entry.key] = widget
                self._apply_wheel_filter(widget)

                # Special handling for icon_provider and icon_weight dropdowns
                if entry.key == "appearance.icon_provider" and isinstance(widget, QComboBox):
                    widget.currentIndexChanged.connect(self._on_provider_changed)

                form.addRow(f"{entry.metadata.label}:", widget)

        return tab

    @Slot()
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
        self._shortcut_widgets = ShortcutWidgets()

        grouped_actions = dict[str, list[str]]()

        for aid, definition in ShortcutManager.definitions.items():
            # Extract first component from action ID (e.g., "workspace" from "workspace.loader.view.reload")
            first_component = aid.split(".")[0]

            # Check if this is a plugin shortcut
            if first_component in self._plugin_names:
                group_name = f"{PLUGIN_PREFIX} - {self._plugin_names[first_component]}"
            else:
                group_name = first_component.title()

            grouped_actions.setdefault(group_name, []).append(aid)

        with SettingsTab("Shortcuts", self) as tab:
            # Create a section for each group
            for group_name, actions in grouped_actions.items():
                shortcuts_section = Accordion(group_name, tab)
                form = shortcuts_section.add_form_layout()

                # Create an editor for each shortcut action in this group
                for aid in actions:
                    definition = ShortcutManager.definitions[aid]
                    current_key = SettingsManager.global_settings.get_key(aid)
                    self._shortcut_widgets.original_shortcuts[aid] = current_key

                    row_widget = QWidget(self)
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(4)

                    editor = ShortcutEditor(
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
                info_section = Accordion("Script", tab)
                form = info_section.add_form_layout()

                path_label = QLabel(str(self._script_path), self)
                path_label.setWordWrap(True)
                form.addRow("Path:", path_label)

                tab.add_section(info_section)

            # Build sections from registry
            sections = dict[str, tuple[Accordion, QFormLayout]]()

            for entry in self.local_settings_registry:
                # Get or create section
                if entry.section not in sections:
                    section = Accordion(entry.section, tab)
                    form = section.add_form_layout()
                    sections[entry.section] = (section, form)
                    tab.add_section(section)

                _, form = sections[entry.section]

                # Create widget from metadata
                widget = entry.metadata.create_widget(self)
                self._local_widgets[entry.key] = widget
                self._apply_wheel_filter(widget)
                form.addRow(f"{entry.metadata.label}:", widget)

        return tab

    def _load_settings_to_ui(self) -> None:
        global_settings = SettingsManager.global_settings

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
            local_settings = SettingsManager.get_local_settings(self._script_path)
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
        data = SettingsManager.global_settings.model_dump()

        for entry in self.global_settings_registry:
            widget = self._global_widgets[entry.key]
            try:
                value = entry.metadata.get_value(widget)
                GlobalSettings.set_nested_value(data, entry.key, value)
            except Exception:
                logger.error("Failed to get value for %s", entry.key)
                raise

        # Add shortcuts (not in registry) and build settings dynamically
        data["shortcuts"] = shortcuts

        return GlobalSettings.model_validate(data)

    def _get_local_settings_from_ui(self, base_settings: LocalSettings | None = None) -> LocalSettings:
        if not self._script_path:
            raise ValueError("No script path provided")

        local_settings = base_settings or SettingsManager.get_local_settings(self._script_path)

        # Use existing settings as base to preserve hidden fields
        data = local_settings.model_dump()

        for entry in self.local_settings_registry:
            widget = self._local_widgets[entry.key]
            try:
                value = entry.metadata.get_value(widget)
                LocalSettings.set_nested_value(data, entry.key, value)
            except Exception:
                logger.error("Failed to get value for %s", entry.key)
                raise

        return LocalSettings.model_validate(data)

    @Slot()
    def _on_ok(self) -> None:
        if self._on_apply():
            self.accept()

    @Slot(result=bool)
    def _on_apply(self) -> bool:
        try:
            global_settings = self._get_global_settings_from_ui()
            SettingsManager.save_global(global_settings)
        except Exception as e:
            logger.error("Failed to save global settings with the error: %s", e)
            logger.debug("Full traceback:", exc_info=e)
            return False

        if self._script_path:
            try:
                SettingsManager.save_local(self._script_path, self._get_local_settings_from_ui)
            except Exception as e:
                logger.error("Failed to save local settings with the error: %s", e)
                logger.debug("Full traceback:", exc_info=e)
                return False

        return True

    @Slot()
    def _on_shortcut_changed(self) -> None:
        key_to_actions = dict[str, list[str]]()

        for aid, editor in self._shortcut_widgets.editors.items():
            key = editor.keySequence().toString()

            if not key or (
                # Exclude plugin shortcuts with a reduced scope
                aid.split(".")[0] in self._plugin_names
                and ShortcutManager.get_hierarchy(aid)
                <= ShortcutManager.SCOPE_HIERARCHY[Qt.ShortcutContext.WidgetWithChildrenShortcut]
            ):
                continue

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
        return {aid: d.default_key for aid, d in ShortcutManager.definitions.items()}

    @cachedproperty
    def _plugin_names(self) -> dict[str, str]:
        from ..plugins.manager import PluginManager

        return {plugin.identifier: plugin.display_name for plugin in PluginManager.all_plugins}

    def _reset_shortcut(self, aid: str) -> None:
        default_key = self._default_shortcuts.get(aid, "")
        self._shortcut_widgets.editors[aid].setKeySequence(QKeySequence(default_key))

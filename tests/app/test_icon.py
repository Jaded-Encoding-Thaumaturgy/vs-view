from __future__ import annotations

import gc
import weakref
from collections.abc import Callable

import pytest
from PySide6.QtCore import QObject, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QToolButton, QWidget
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from vsview.app.icon import ICON_PROVIDERS, IconName, IconReloadMixin, load_icon
from vsview.app.settings import SettingsManager
from vsview.app.themes import AppearanceManager

pytestmark = [pytest.mark.unit, pytest.mark.qt]


class ReloadableWidget(QWidget, IconReloadMixin):
    """Standard cooperative subclass with QWidget first in MRO."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)


class MixinFirstWidget(IconReloadMixin, QWidget):
    """Cooperative subclass with IconReloadMixin first in MRO."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)


def create_icon_image(
    icon: QIcon,
    size: QSize | None = None,
    mode: QIcon.Mode = QIcon.Mode.Normal,
    state: QIcon.State = QIcon.State.Off,
) -> QImage:
    return icon.pixmap(size or QSize(20, 20), mode, state).toImage()


def set_widget_role_color(
    widget: QWidget,
    role: QPalette.ColorRole,
    color: QColor,
    group: QPalette.ColorGroup = QPalette.ColorGroup.Normal,
) -> None:
    palette = widget.palette()
    palette.setColor(group, role, color)
    widget.setPalette(palette)


class TestDefaultIconStatesMapping:
    @pytest.mark.parametrize(
        ("mode", "state", "expected_group", "expected_role"),
        [
            (QIcon.Mode.Normal, QIcon.State.Off, QPalette.ColorGroup.Normal, QPalette.ColorRole.ButtonText),
            (QIcon.Mode.Disabled, QIcon.State.Off, QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText),
            (QIcon.Mode.Normal, QIcon.State.On, QPalette.ColorGroup.Normal, QPalette.ColorRole.ButtonText),
        ],
    )
    def test_default_states_resolve_expected_roles(
        self,
        qapp: QApplication,
        mode: QIcon.Mode,
        state: QIcon.State,
        expected_group: QPalette.ColorGroup,
        expected_role: QPalette.ColorRole,
    ) -> None:
        AppearanceManager.apply_style("Fusion")
        mapping = IconReloadMixin.DEFAULT_ICON_STATES
        assert mapping[mode, state] == (expected_group, expected_role)


class TestGetOnColorRole:
    def test_returns_button_text_for_non_windows11_style(self, qapp: QApplication) -> None:
        AppearanceManager.apply_style("Fusion")
        assert IconReloadMixin.get_on_color_role() == QPalette.ColorRole.ButtonText

    def test_returns_highlighted_text_for_windows11_style(self, qapp: QApplication, mocker: MockerFixture) -> None:
        style = qapp.style()
        mocker.patch.object(style, "name", return_value="windows11")

        assert IconReloadMixin.get_on_color_role() == QPalette.ColorRole.HighlightedText


class TestLoadIconAndMakeIcon:
    @pytest.mark.parametrize(("size_arg", "expected_size"), [(24, QSize(24, 24)), (QSize(32, 16), QSize(32, 16))])
    def test_load_icon_size_variants_and_color(
        self,
        qapp: QApplication,
        size_arg: int | QSize,
        expected_size: QSize,
    ) -> None:
        pixmap = load_icon(IconName.PLAY, size_arg, QColor("#ff0000"), dpr=1.0)
        assert isinstance(pixmap, QPixmap)
        assert not pixmap.isNull()
        assert pixmap.size() == expected_size

    def test_load_icon_explicit_provider_weight_and_dpr(self, qapp: QApplication) -> None:
        provider_id = next(iter(ICON_PROVIDERS))
        provider = ICON_PROVIDERS[provider_id]
        weight = provider.default_weight

        pixmap = load_icon(
            IconName.CHECK,
            20,
            Qt.GlobalColor.green,
            provider=provider_id,
            weight=weight,
            dpr=2.0,
        )
        assert not pixmap.isNull()
        assert pixmap.devicePixelRatio() == 2.0
        assert pixmap.size() == QSize(40, 40)

    def test_make_icon_simple_tuple_and_stateful_mapping(self, qapp: QApplication) -> None:
        red = QColor("#ff0000")
        blue = QColor("#0000ff")
        size = QSize(24, 24)

        simple_icon = IconReloadMixin.make_icon((IconName.PLAY, red), size=size)
        expected_play_red = load_icon(IconName.PLAY, size, red).toImage()
        assert create_icon_image(simple_icon, size, QIcon.Mode.Normal, QIcon.State.Off) == expected_play_red

        stateful_icon = IconReloadMixin.make_icon(
            {
                (QIcon.Mode.Normal, QIcon.State.Off): (IconName.PLAY, red),
                (QIcon.Mode.Normal, QIcon.State.On): (IconName.PAUSE, blue),
            },
            size=size,
        )
        expected_pause_blue = load_icon(IconName.PAUSE, size, blue).toImage()
        assert create_icon_image(stateful_icon, size, QIcon.Mode.Normal, QIcon.State.Off) == expected_play_red
        assert create_icon_image(stateful_icon, size, QIcon.Mode.Normal, QIcon.State.On) == expected_pause_blue


class TestMakeToolButtonAndAction:
    def test_make_tool_button_configures_properties_and_icon(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        explicit_color = QColor("#12ab34")
        size = QSize(28, 28)
        btn = widget.make_tool_button(
            IconName.LINK,
            "Link Tooltip",
            widget,
            checkable=True,
            checked=True,
            icon_size=size,
            color=explicit_color,
        )

        assert btn.parentWidget() is widget
        assert btn.toolTip() == "Link Tooltip"
        assert btn.isCheckable() is True
        assert btn.isChecked() is True
        assert btn.autoRaise() is True
        assert btn.iconSize() == size

        expected_img = create_icon_image(IconReloadMixin.make_icon((IconName.LINK, explicit_color), size=size), size)
        assert create_icon_image(btn.icon(), size) == expected_img

    def test_make_action_configures_properties_and_icon(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        color = QColor("#dc3545")
        size = QSize(22, 22)
        act = widget.make_action(
            IconName.PLUS,
            "Add Item",
            checkable=True,
            checked=True,
            icon_size=size,
            color=color,
        )

        assert act.parent() is widget
        assert act.toolTip() == "Add Item"
        assert act.isCheckable() is True
        assert act.isChecked() is True

        expected_img = create_icon_image(IconReloadMixin.make_icon((IconName.PLUS, color), size=size), size)
        assert create_icon_image(act.icon(), size) == expected_img

    @pytest.mark.parametrize(
        "factory",
        [
            lambda w: w.make_tool_button(IconReloadMixin.make_icon((IconName.SAVE, QColor("#abcdef"))), "Save", w),
            lambda w: w.make_tool_button(IconName.PLAY, "Play", w, register_icon=False),
            lambda w: w.make_action(QIcon(), "Custom", w),
            lambda w: w.make_action(IconName.MINUS, "Remove", w, register_icon=False),
        ],
    )
    def test_factory_methods_with_premade_icon_or_opt_out_skip_hot_reload(
        self,
        qtbot: QtBot,
        mocker: MockerFixture,
        factory: Callable[[ReloadableWidget], QToolButton | QAction],
    ) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        item = factory(widget)
        set_icon_spy = mocker.spy(item, "setIcon")

        SettingsManager.signals.globalChanged.emit()
        qtbot.wait(20)

        assert set_icon_spy.call_count == 0


class TestIconHotReloading:
    def test_reloads_registered_button_with_color_role_on_global_changed(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        size = QSize(20, 20)
        initial_color = QColor("#112233")
        updated_color = QColor("#ddccbb")

        btn = QToolButton(widget)
        set_widget_role_color(btn, QPalette.ColorRole.WindowText, initial_color)
        widget.register_icon_button(btn, IconName.PLAY, icon_size=size, color_role=QPalette.ColorRole.WindowText)

        set_widget_role_color(btn, QPalette.ColorRole.WindowText, updated_color)
        expected_img = create_icon_image(IconReloadMixin.make_icon((IconName.PLAY, updated_color), size=size), size)

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: create_icon_image(btn.icon(), size) == expected_img)

    def test_reloads_registered_button_with_stateful_roles_and_group_tuples(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        size = QSize(20, 20)
        off_color = QColor("#224466")
        on_color = QColor("#88aa00")
        disabled_color = QColor("#555555")

        icon_states = {
            (QIcon.Mode.Normal, QIcon.State.Off): QPalette.ColorRole.ButtonText,
            (QIcon.Mode.Normal, QIcon.State.On): (QPalette.ColorGroup.Normal, QPalette.ColorRole.Highlight),
            (QIcon.Mode.Disabled, QIcon.State.Off): (QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText),
        }

        btn = widget.make_tool_button(
            IconName.FRAME_CORNERS,
            "Corners",
            widget,
            icon_size=size,
            icon_states=icon_states,
        )

        set_widget_role_color(btn, QPalette.ColorRole.ButtonText, off_color, QPalette.ColorGroup.Normal)
        set_widget_role_color(btn, QPalette.ColorRole.Highlight, on_color, QPalette.ColorGroup.Normal)
        set_widget_role_color(btn, QPalette.ColorRole.ButtonText, disabled_color, QPalette.ColorGroup.Disabled)

        expected_icon = IconReloadMixin.make_icon(
            {
                (QIcon.Mode.Normal, QIcon.State.Off): (IconName.FRAME_CORNERS, off_color),
                (QIcon.Mode.Normal, QIcon.State.On): (IconName.FRAME_CORNERS, on_color),
                (QIcon.Mode.Disabled, QIcon.State.Off): (IconName.FRAME_CORNERS, disabled_color),
            },
            size=size,
        )

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(
            lambda: (
                create_icon_image(btn.icon(), size, QIcon.Mode.Normal, QIcon.State.Off)
                == create_icon_image(expected_icon, size, QIcon.Mode.Normal, QIcon.State.Off)
            )
        )
        assert create_icon_image(btn.icon(), size, QIcon.Mode.Normal, QIcon.State.On) == create_icon_image(
            expected_icon, size, QIcon.Mode.Normal, QIcon.State.On
        )
        assert create_icon_image(btn.icon(), size, QIcon.Mode.Disabled, QIcon.State.Off) == create_icon_image(
            expected_icon, size, QIcon.Mode.Disabled, QIcon.State.Off
        )

    def test_reloads_registered_action_with_widget_parent_and_non_widget_parent_fallback(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)
        non_widget_parent = QObject(widget)

        size = QSize(20, 20)
        widget_color = QColor("#aa11cc")
        state_on_color = QColor("#11ccaa")

        set_widget_role_color(widget, QPalette.ColorRole.ToolTipText, widget_color)
        set_widget_role_color(widget, QPalette.ColorRole.Highlight, state_on_color)

        act_fallback = QAction(non_widget_parent)
        widget.register_icon_action(
            act_fallback,
            IconName.CLIPBOARD,
            icon_size=size,
            color_role=QPalette.ColorRole.ToolTipText,
        )

        act_stateful = widget.make_action(
            IconName.PIPETTE,
            "Pipette",
            widget,
            icon_size=size,
            icon_states={
                (QIcon.Mode.Normal, QIcon.State.Off): QPalette.ColorRole.ToolTipText,
                (QIcon.Mode.Normal, QIcon.State.On): (QPalette.ColorGroup.Normal, QPalette.ColorRole.Highlight),
            },
        )

        new_color = QColor("#ff8800")
        set_widget_role_color(widget, QPalette.ColorRole.ToolTipText, new_color)

        expected_fallback = create_icon_image(
            IconReloadMixin.make_icon((IconName.CLIPBOARD, new_color), size=size), size
        )
        expected_stateful_off = create_icon_image(
            IconReloadMixin.make_icon((IconName.PIPETTE, new_color), size=size), size
        )
        expected_stateful_on = create_icon_image(
            IconReloadMixin.make_icon((IconName.PIPETTE, state_on_color), size=size),
            size,
        )

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: create_icon_image(act_fallback.icon(), size) == expected_fallback)
        assert create_icon_image(act_stateful.icon(), size, QIcon.Mode.Normal, QIcon.State.Off) == expected_stateful_off
        assert create_icon_image(act_stateful.icon(), size, QIcon.Mode.Normal, QIcon.State.On) == expected_stateful_on

    def test_reloads_when_global_icon_weight_setting_changes(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        size = QSize(24, 24)
        color = QColor("#ffffff")
        set_widget_role_color(widget, QPalette.ColorRole.ToolTipText, color)

        appearance_settings = SettingsManager.global_settings.appearance
        provider = ICON_PROVIDERS[appearance_settings.icon_provider]
        alternative_weights = [w for w in provider.weights if w != appearance_settings.icon_weight]
        assert alternative_weights, "Expected icon provider to have at least 2 weights"
        new_weight = alternative_weights[0]

        btn = widget.make_tool_button(IconName.PLAY, "Play", widget, icon_size=size)
        initial_img = create_icon_image(btn.icon(), size)

        appearance_settings.icon_weight = new_weight
        expected_new_img = load_icon(IconName.PLAY, size, color, provider=provider.id, weight=new_weight).toImage()
        assert expected_new_img != initial_img

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: create_icon_image(btn.icon(), size) == expected_new_img)

    def test_custom_callbacks_execute_and_prune_runtime_error_callbacks(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        failing_calls = 0
        healthy_calls = 0

        def failing_callback() -> None:
            nonlocal failing_calls
            failing_calls += 1
            raise RuntimeError("Internal C++ object already deleted")

        def healthy_callback() -> None:
            nonlocal healthy_calls
            healthy_calls += 1

        widget.register_icon_callback(failing_callback)
        widget.register_icon_callback(healthy_callback)

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: healthy_calls == 1)
        assert failing_calls == 1

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: healthy_calls == 2)
        assert failing_calls == 1


class TestLifecycleAndLeakPrevention:
    def test_idempotent_init_does_not_reset_registrations_or_duplicate_connections(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        calls = 0

        def on_reload() -> None:
            nonlocal calls
            calls += 1

        widget.register_icon_callback(on_reload)

        IconReloadMixin.__init__(widget)

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: calls >= 1)
        qtbot.wait(20)

        assert calls == 1

    def test_unreferenced_buttons_and_actions_are_garbage_collected_without_leaking(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        btn = widget.make_tool_button(IconName.PLAY, "Play")
        act = QAction()
        widget.register_icon_action(act, IconName.PAUSE)

        btn_ref = weakref.ref(btn)
        act_ref = weakref.ref(act)

        del btn
        del act
        gc.collect()

        assert btn_ref() is None
        assert act_ref() is None

        healthy_calls = 0

        def on_reload() -> None:
            nonlocal healthy_calls
            healthy_calls += 1

        widget.register_icon_callback(on_reload)
        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: healthy_calls == 1)

    def test_cpp_deleted_button_and_action_with_live_python_wrappers_are_skipped_safely(self, qtbot: QtBot) -> None:
        widget = ReloadableWidget()
        qtbot.addWidget(widget)

        size = QSize(20, 20)
        dead_btn = widget.make_tool_button(IconName.PLAY, "Dead Button", widget, icon_size=size)
        dead_act = widget.make_action(IconName.PAUSE, "Dead Action", widget, icon_size=size)
        live_btn = widget.make_tool_button(
            IconName.CHECK,
            "Live Button",
            widget,
            icon_size=size,
            color_role=QPalette.ColorRole.WindowText,
        )

        Shiboken.delete(dead_btn)
        Shiboken.delete(dead_act)
        assert not Shiboken.isValid(dead_btn)
        assert not Shiboken.isValid(dead_act)

        updated_color = QColor("#00ee77")
        set_widget_role_color(live_btn, QPalette.ColorRole.WindowText, updated_color)
        expected_live_img = create_icon_image(
            IconReloadMixin.make_icon((IconName.CHECK, updated_color), size=size), size
        )

        SettingsManager.signals.globalChanged.emit()
        qtbot.waitUntil(lambda: create_icon_image(live_btn.icon(), size) == expected_live_img)

    def test_delete_later_disconnects_signal_and_clears_callbacks_for_both_mro_orders(
        self, qtbot: QtBot, mocker: MockerFixture
    ) -> None:
        # Mixin-first MRO calls IconReloadMixin.deleteLater() synchronously before event loop runs
        mixin_first = MixinFirstWidget()
        btn = mixin_first.make_tool_button(IconName.PLAY, "Play", mixin_first)
        btn_spy = mocker.spy(btn, "setIcon")
        immediate_calls = 0

        def on_immediate_reload() -> None:
            nonlocal immediate_calls
            immediate_calls += 1

        mixin_first.register_icon_callback(on_immediate_reload)
        mixin_first.deleteLater()
        mixin_first.deleteLater()

        SettingsManager.signals.globalChanged.emit()
        qtbot.wait(30)

        assert immediate_calls == 0
        assert btn_spy.call_count == 0

        # QWidget-first MRO cleans up via destroyed signal once DeferredDelete is processed
        qwidget_first = ReloadableWidget()
        deferred_calls = 0

        def on_deferred_reload() -> None:
            nonlocal deferred_calls
            deferred_calls += 1

        qwidget_first.register_icon_callback(on_deferred_reload)
        qwidget_first.deleteLater()
        qtbot.waitUntil(lambda: not Shiboken.isValid(qwidget_first))

        SettingsManager.signals.globalChanged.emit()
        qtbot.wait(30)

        assert deferred_calls == 0

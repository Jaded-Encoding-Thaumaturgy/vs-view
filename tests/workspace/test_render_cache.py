from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import vapoursynth as vs
from pytestqt.qtbot import QtBot

from tests.scripts import blank_clip_script, dual_output_script, script_source
from vsview.app.cache.job import JobState
from vsview.app.cache.session import close_session
from vsview.app.cache.storage import DiskFrameStore
from vsview.app.plugins.manager import PluginManager
from vsview.app.settings import SettingsManager
from vsview.app.settings.models import CacheRange
from vsview.app.views.timeline import Timeline
from vsview.app.workspace.file import PythonScriptWorkspace
from vsview.vsenv.loop import QtEventLoop

pytestmark = [pytest.mark.unit, pytest.mark.qt, pytest.mark.vpy("no-core")]

TIMEOUT_MS = 5000


@pytest.fixture(autouse=True)
def scratch_directory(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "scratch"
    SettingsManager.global_settings.render_cache.scratch_directory = str(root)
    yield root
    close_session()


@pytest.fixture
def new_workspace(
    qt_event_loop: QtEventLoop, qtbot: QtBot, init_plugin_manager: PluginManager
) -> Callable[[], PythonScriptWorkspace]:
    def create() -> PythonScriptWorkspace:
        ws = PythonScriptWorkspace()
        qtbot.addWidget(ws)
        return ws

    return create


def _load(qtbot: QtBot, ws: PythonScriptWorkspace, script_file: Path) -> None:
    fut = ws.load_content(script_file)
    qtbot.waitUntil(lambda: fut.done() and ws.stack.currentWidget() is ws.loaded_page, timeout=TIMEOUT_MS)
    assert fut.result() == 0


def _script(tmp_path: Path, script: Callable[..., None] = blank_clip_script) -> Path:
    script_file = tmp_path / "script.vpy"
    script_file.write_text(script_source(script, length=30), encoding="utf-8")
    return script_file


def _second_output_script(length: int = 100) -> None:
    """The green output of `dual_output_script` alone, still at index 1."""

    clip = vs.core.std.BlankClip(
        length=length, width=64, height=64, format=vs.RGB24, fpsnum=24, fpsden=1, color=[0, 255, 0]
    )
    clip = vs.core.std.SetFrameProps(
        clip, _Matrix=vs.MATRIX_RGB, _Primaries=vs.PRIMARIES_BT709, _Transfer=vs.TRANSFER_BT709
    )
    clip.set_output(1)


def _store(ws: PythonScriptWorkspace, index: int = 0) -> DiskFrameStore | None:
    cache = ws.outputs_manager.voutputs[index].cache
    assert cache is not None
    return cache.store


def _is_done(ws: PythonScriptWorkspace, index: int = 0) -> bool:
    job = ws.render_cache.job(index)
    return job is not None and job.progress.state is JobState.DONE


def _notch_spans(ws: PythonScriptWorkspace, key: str) -> set[tuple[int, int | None]]:
    return {(n.data, n.end_data) for n in ws.tbar.timeline.custom_notches.get(key, ())}


def _render(qtbot: QtBot, ws: PythonScriptWorkspace, start: int, end: int) -> None:
    ws.render_cache.set_start(start)
    ws.render_cache.set_end(end)
    ws.render_cache.toggle_render()
    qtbot.waitUntil(lambda: _is_done(ws), timeout=TIMEOUT_MS)


def test_rendered_range_is_cached_and_shown_on_the_timeline(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path))

    _render(qtbot, ws, 5, 9)

    store = _store(ws)
    assert store is not None
    assert store.frames() == [5, 6, 7, 8, 9]
    qtbot.waitUntil(lambda: _notch_spans(ws, Timeline.CACHE_FRAMES_NOTCH_KEY) == {(5, 10)}, timeout=TIMEOUT_MS)
    assert _notch_spans(ws, Timeline.CACHE_RANGE_NOTCH_KEY) == {(5, 9)}


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ([("start", 5), ("end", 10)], CacheRange(start=5, end=10)),
        ([("end", 10), ("start", 20)], CacheRange(start=20)),
        ([("start", 20), ("end", 10)], CacheRange(end=10)),
    ],
)
def test_an_end_on_the_wrong_side_of_the_other_end_clears_it(
    qtbot: QtBot,
    tmp_path: Path,
    new_workspace: Callable[[], PythonScriptWorkspace],
    edits: list[tuple[str, int]],
    expected: CacheRange,
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path))

    for end, frame in edits:
        (ws.render_cache.set_start if end == "start" else ws.render_cache.set_end)(frame)

    assert ws.render_cache.ranges == {0: expected}


def test_render_waits_while_playing(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path))
    ws.playback.playbackStateChanged.emit(True)

    ws.render_cache.set_start(0)
    ws.render_cache.set_end(9)
    ws.render_cache.toggle_render()
    job = ws.render_cache.job(0)
    assert job is not None
    assert job.wait(TIMEOUT_MS / 1000)

    assert job.progress.state is JobState.WAITING
    assert job.store.frame_count == 0

    ws.playback.playbackStateChanged.emit(False)
    qtbot.waitUntil(lambda: _is_done(ws), timeout=TIMEOUT_MS)

    assert job.store.frames() == list(range(10))


def test_render_of_an_output_waits_while_another_output_is_shown(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path, dual_output_script))
    ws.playback.playbackStateChanged.emit(True)
    ws.render_cache.set_start(0)
    ws.render_cache.set_end(9)
    ws.render_cache.toggle_render()

    ws.tab_manager.switch_tab(1)
    qtbot.waitUntil(lambda: ws.outputs_manager.current_video_index == 1 and ws.playback.is_idle, timeout=TIMEOUT_MS)
    ws.playback.playbackStateChanged.emit(False)
    job = ws.render_cache.job(0)
    assert job is not None

    assert job.progress.state is JobState.WAITING
    assert job.store.frame_count == 0

    ws.tab_manager.switch_tab(0)
    qtbot.waitUntil(lambda: _is_done(ws), timeout=TIMEOUT_MS)


@pytest.mark.parametrize("render_after_reload", [False, True])
def test_reload_deletes_the_cached_frames_and_keeps_the_range(
    qtbot: QtBot,
    tmp_path: Path,
    new_workspace: Callable[[], PythonScriptWorkspace],
    render_after_reload: bool,
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path))
    _render(qtbot, ws, 5, 9)
    old_store = _store(ws)
    assert old_store is not None
    SettingsManager.global_settings.render_cache.render_after_reload = render_after_reload

    fut = ws.reload_content()
    qtbot.waitUntil(lambda: fut.done() and ws.playback.is_idle, timeout=TIMEOUT_MS)
    assert fut.result() == 0
    qtbot.waitUntil(lambda: not old_store.path.exists(), timeout=TIMEOUT_MS)

    assert ws.render_cache.ranges == {0: CacheRange(start=5, end=9, started=True)}

    if render_after_reload:
        qtbot.waitUntil(lambda: _is_done(ws), timeout=TIMEOUT_MS)
        store = _store(ws)
        assert store is not None
        assert store.frames() == [5, 6, 7, 8, 9]
    else:
        assert ws.render_cache.job(0) is None
        assert _store(ws) is None


def test_range_stays_with_its_output_when_an_earlier_output_is_removed(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    script_file = _script(tmp_path, dual_output_script)
    ws = new_workspace()
    _load(qtbot, ws, script_file)
    ws.render_cache.set_start(2)
    ws.render_cache.set_end(4)
    ws.tab_manager.switch_tab(1)
    qtbot.waitUntil(lambda: ws.outputs_manager.current_video_index == 1 and ws.playback.is_idle, timeout=TIMEOUT_MS)
    ws.render_cache.set_start(5)
    ws.render_cache.set_end(9)
    script_file.write_text(script_source(_second_output_script, length=30), encoding="utf-8")

    fut = ws.reload_content()
    qtbot.waitUntil(lambda: fut.done() and ws.playback.is_idle, timeout=TIMEOUT_MS)
    assert fut.result() == 0

    assert ws.render_cache.ranges == {1: CacheRange(start=5, end=9)}
    assert ws.render_cache.current_range() == CacheRange(start=5, end=9)


def test_saved_range_comes_back_with_the_script_without_rendering(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    script_file = _script(tmp_path)
    first = new_workspace()
    _load(qtbot, first, script_file)
    _render(qtbot, first, 5, 9)

    SettingsManager.save_local(script_file, first.local_settings)
    second = new_workspace()
    _load(qtbot, second, script_file)

    assert second.render_cache.ranges == {0: CacheRange(start=5, end=9)}
    assert second.render_cache.job(0) is None
    assert _notch_spans(second, Timeline.CACHE_RANGE_NOTCH_KEY) == {(5, 9)}


def test_concurrency_override_comes_back_with_the_script_until_reset(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    SettingsManager.global_settings.render_cache.concurrency = 2
    script_file = _script(tmp_path)
    first = new_workspace()
    _load(qtbot, first, script_file)

    first.tbar.timeline.cache_concurrency_spinbox.setValue(5)
    first.snapshot_settings(None)
    SettingsManager.save_local(script_file, first.local_settings)

    second = new_workspace()
    _load(qtbot, second, script_file)
    restored = second.render_cache.concurrency

    second.tbar.timeline.cache_concurrency_reset_action.trigger()

    assert restored == 5
    assert second.render_cache.concurrency_override is None
    assert second.render_cache.concurrency == 2


def test_clear_deletes_the_cached_frames_and_the_range(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path))
    _render(qtbot, ws, 5, 9)
    store = _store(ws)
    assert store is not None

    ws.render_cache.clear()
    qtbot.waitUntil(lambda: not store.path.exists(), timeout=TIMEOUT_MS)

    assert _store(ws) is None
    assert ws.render_cache.ranges == {}
    assert ws.render_cache.job(0) is None
    qtbot.waitUntil(lambda: not _notch_spans(ws, Timeline.CACHE_FRAMES_NOTCH_KEY), timeout=TIMEOUT_MS)
    assert _notch_spans(ws, Timeline.CACHE_RANGE_NOTCH_KEY) == set()


def test_play_cache_range_is_enabled_only_with_a_full_range(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path))
    button = ws.tbar.playback_container.play_cache_btn
    enabled = [button.isEnabled()]

    ws.render_cache.set_start(5)
    enabled.append(button.isEnabled())
    ws.render_cache.set_end(9)
    enabled.append(button.isEnabled())
    ws.render_cache.clear()
    enabled.append(button.isEnabled())

    assert enabled == [False, False, True, False]


def test_play_cache_range_plays_the_range_with_the_play_step(
    qtbot: QtBot, tmp_path: Path, new_workspace: Callable[[], PythonScriptWorkspace]
) -> None:
    ws = new_workspace()
    _load(qtbot, ws, _script(tmp_path))
    ws.render_cache.set_start(5)
    ws.render_cache.set_end(9)
    ws.tbar.playback_container.settings.step = -1
    frames = list[int]()
    ws.playback.timelineCursorChanged.connect(frames.append)

    ws.tbar.playback_container.play_cache_btn.click()
    qtbot.waitUntil(
        lambda: frames[-1:] == [5] and not ws.playback.state.is_playing and ws.playback.is_idle, timeout=TIMEOUT_MS
    )

    assert frames == [9, 8, 7, 6, 5]

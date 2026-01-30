from __future__ import annotations

from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, wait
from logging import getLogger
from time import perf_counter_ns
from typing import TYPE_CHECKING

from jetpytools import clamp, cround
from PySide6.QtCore import QObject, Qt, QTime, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from ...vsenv import run_in_background, run_in_loop
from ..outputs import AudioBuffer, FrameBuffer
from ..settings import SettingsManager
from ..views.timeline import Frame, Time, TimelineControlBar

if TYPE_CHECKING:
    from vsengine.policy import ManagedEnvironment

    from ...vsenv import QtEventLoop
    from ..outputs import OutputsManager
    from ..plugins.api import PluginAPI
    from .tab_manager import TabManager

logger = getLogger(__name__)

# Module constants
MIN_FRAME_DELAY_NS = 1_000_000  # 1ms minimum scheduling delay


class PlaybackState(QObject):
    """
    Manages playback-related state.

    Attributes:
        current_frame: Current frame being played.
        current_audio_frame: Current audio frame being played.
        is_playing: Whether playback is currently active.

        last_fps_update_ns: Timestamp (ns) of last FPS display update.
        fps_history: Rolling window of frame timestamps for FPS averaging.

        stop_at_frame: Frame number to stop playback at.

        buffer: Frame buffer for async pre-fetching during playback.
        frame_interval_ns: Target frame interval in nanoseconds for FPS limiting.
        next_frame_time_ns: Target time (ns) when next frame should start.

        audio_buffer: Audio buffer for async audio frame pre-fetching.
        audio_frame_interval_ns: Target audio frame interval in nanoseconds.
        next_audio_frame_time_ns: Target time (ns) when next audio frame should be pushed.

        video_timer: Timer for video frame scheduling.
        audio_timer: Timer for audio frame scheduling.

        _cleanup_future: Pending buffer cleanup future.
        _audio_cleanup_future: Pending audio buffer cleanup future.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self.current_frame = 0
        self.current_audio_frame = 0
        self.is_playing = False

        self.last_fps_update_ns = 0
        self.fps_history = deque[int](maxlen=25)

        self.stop_at_frame: int | None = None

        # Video playback state
        self.buffer: FrameBuffer | None = None
        self.frame_interval_ns = 0
        self.next_frame_time_ns = 0

        # Audio playback state
        self.audio_buffer: AudioBuffer | None = None
        self.audio_frame_interval_ns = 0
        self.next_audio_frame_time_ns = 0

        self.video_timer = QTimer(self, timerType=Qt.TimerType.PreciseTimer, singleShot=True)
        self.audio_timer = QTimer(self, timerType=Qt.TimerType.PreciseTimer, singleShot=True)

        self._cleanup_future: Future[None] | None = None
        self._audio_cleanup_future: Future[None] | None = None

    def reset(self) -> None:
        self.last_fps_update_ns = 0
        self.fps_history.clear()

        self.stop_at_frame = None

        self.frame_interval_ns = 0
        self.next_frame_time_ns = 0

        self.audio_frame_interval_ns = 0
        self.next_audio_frame_time_ns = 0

        self.video_timer.stop()
        self.audio_timer.stop()

        if self.buffer:
            self._cleanup_future = self.buffer.invalidate()
            self.buffer = None

        if self.audio_buffer:
            self._audio_cleanup_future = self.audio_buffer.invalidate()
            self.audio_buffer = None

    def reset_audio(self) -> None:
        self.next_audio_frame_time_ns = 0
        self.audio_frame_interval_ns = 0
        self.audio_timer.stop()

        if self.audio_buffer:
            self._audio_cleanup_future = self.audio_buffer.invalidate()
            self.audio_buffer = None

    def wait_for_cleanup(self, timeout: float | None = None, stall_cb: Callable[[], None] | None = None) -> None:
        futures = list[Future[None]]()

        if self._cleanup_future:
            futures.append(self._cleanup_future)
        if self._audio_cleanup_future:
            futures.append(self._audio_cleanup_future)

        if futures:
            if timeout is not None and stall_cb:
                _, undone = wait(futures, timeout=timeout)
                if undone:
                    stall_cb()

            for f in futures:
                f.result()

        self._cleanup_future = None
        self._audio_cleanup_future = None


class PlaybackManager(QObject):
    """
    Manages video and audio playback.

    This class encapsulates all playback logic including buffering, frame timing,
    seeking, and audio synchronization. It communicates with the UI via signals.
    """

    # Signals for UI communication
    frameRendered = Signal(QImage)  # image
    timelineCursorChanged = Signal(int)  # frame number

    audioOutputChanged = Signal(int)  # index

    statusLoadingStarted = Signal(str)  # message
    statusLoadingFinished = Signal(str)  # completed message
    statusLoadingErrored = Signal(str)  # error message

    loadFailed = Signal()  # error during frame render

    def __init__(
        self,
        loop: QtEventLoop,
        get_env: Callable[[], ManagedEnvironment],
        api: PluginAPI,
        outputs_manager: OutputsManager,
        tab_manager: TabManager,
        tbar: TimelineControlBar,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._loop = loop
        self._get_env = get_env

        self._api = api
        self._outputs_manager = outputs_manager
        self._tab_manager = tab_manager
        self._tbar = tbar

        # Playback buttons signals
        self._tbar.playback_container.seek_n_back_btn.clicked.connect(lambda: self.seek_n_frames(-1))
        self._tbar.playback_container.seek_1_back_btn.clicked.connect(lambda: self.seek_frame(-1))
        self._tbar.playback_container.play_pause_btn.clicked.connect(self.toggle_playback)
        self._tbar.playback_container.seek_1_fwd_btn.clicked.connect(lambda: self.seek_frame(1))
        self._tbar.playback_container.seek_n_fwd_btn.clicked.connect(lambda: self.seek_n_frames(1))

        # Custom context menu signals
        self._tbar.playback_container.settingsChanged.connect(self._on_playback_settings_changed)
        self._tbar.playback_container.playZone.connect(self._on_play_zone)
        self._tbar.playback_container.audioDelayChanged.connect(self._on_audio_delay_changed)
        self._tbar.playback_container.audio_output_combo.currentIndexChanged.connect(self._on_audio_output_changed)

        # Connect Frame/Time Edit signals
        self._tbar.playback_container.frame_edit.frameChanged.connect(self._on_frame_changed)
        self._tbar.playback_container.time_edit.valueChanged.connect(self._on_time_changed)

        # Speaker and slider signals
        self._tbar.playback_container.volumeChanged.connect(self._on_volume_changed)
        self._tbar.playback_container.muteChanged.connect(self._on_mute_changed)

        # Timeline signal
        self._tbar.timeline.clicked.connect(self._on_timeline_clicked)

        # Playback state
        self.state = PlaybackState(self)
        self.state.video_timer.timeout.connect(self._play_next_frame)
        self.state.audio_timer.timeout.connect(self._play_next_audio_frame)

        self.can_reload = False

    @property
    def _env(self) -> ManagedEnvironment:
        return self._get_env()

    @run_in_loop
    def request_frame(self, n: int, cb_render: Callable[[Future[None]], None] | None = None) -> None:
        """Request a specific frame to be rendered and displayed."""
        logger.debug("Frame requested: %d", n)

        fut = self._render_frame(n)

        def on_complete(f: Future[None]) -> None:
            if f.exception():
                logger.exception("Frame render failed")
                self.loadFailed.emit()
            elif self._tab_manager.tabs.currentIndex() != -1:
                self.state.current_frame = n
                self._tab_manager.current_view.last_frame = n

        fut.add_done_callback(on_complete)

        if cb_render:
            fut.add_done_callback(cb_render)

    @run_in_background(name="RenderFrame")
    def _render_frame(self, n: int) -> None:
        """Render a specific frame and emit for display."""
        logger.debug("Rendering frame %d (background)", n)

        if not (voutput := self._outputs_manager.current_voutput):
            return

        self.can_reload = False

        with self._env.use():
            if self.state.is_playing:
                with voutput.prepared_clip.get_frame(n) as frame:
                    logger.debug("Frame %d rendered", n)
                    image = voutput.packer.frame_to_qimage(frame)

                self._api._on_current_frame_changed(n, None)
            else:
                self.statusLoadingStarted.emit(f"Rendering frame {n}...")

                with voutput.prepared_clip.get_frame(n) as frame:
                    logger.debug("Frame %d rendered", n)
                    image = voutput.packer.frame_to_qimage(frame)

                self._api._on_current_frame_changed(n, None)
                self.statusLoadingFinished.emit("Completed")

        self.frameRendered.emit(image)
        self.timelineCursorChanged.emit(n)
        self.can_reload = True

    @Slot(int)
    def seek_frame(self, delta: int) -> None:
        """Seek by a relative number of frames."""
        if not (voutput := self._outputs_manager.current_voutput):
            logger.warning("No current video output, ignoring seek")
            return

        self.request_frame(clamp(self.state.current_frame + delta, 0, voutput.clip.num_frames - 1))

    @Slot(int)
    def seek_n_frames(self, direction: int) -> None:
        """Seek by N frames (configured step size)."""
        self.seek_frame(direction * self._tbar.playback_container.settings.seek_step)

    @Slot()
    def toggle_playback(self) -> None:
        """Toggle between play and pause."""
        self.state.is_playing = not self.state.is_playing

        if self.state.is_playing:
            self._start_playback()
        else:
            self._stop_playback()

    def stop(self) -> None:
        """Stop playback externally."""
        if self.state.is_playing:
            self._stop_playback()

    def wait_for_cleanup(self, timeout: float | None = None, stall_cb: Callable[[], None] | None = None) -> None:
        """Wait for buffer cleanup to complete."""
        self.state.wait_for_cleanup(timeout, stall_cb)

    @run_in_background(name="StartPlayback")
    def _start_playback(self, loop_range: range | None = None, stop_at: int | None = None) -> None:
        logger.debug("Starting playback")

        self._tbar.set_playback_controls_enabled(False)
        self._tbar.timeline.is_events_blocked = True
        self.can_reload = False

        try:
            # Wait for any pending buffer cleanup before creating new buffer
            # This prevents accumulation of buffers when user spams play/pause
            self.state.wait_for_cleanup(
                timeout=1.0,
                stall_cb=lambda: self.statusLoadingStarted.emit("Clearing buffer..."),
            )

            self.state.is_playing = True
            self.state.reset()
            self.state.stop_at_frame = stop_at

            self._prepare_video(loop_range=loop_range)
            self._prepare_audio(loop_range=loop_range)

            # Check if user cancelled
            if not self.state.is_playing or not self.state.audio_buffer:
                return

            self.state.audio_buffer.wait_for_first_frame(
                timeout=0.25,
                stall_cb=lambda: self.statusLoadingStarted.emit("Buffering audio..."),
            )

            # Check if user cancelled
            if not self.state.is_playing or not self.state.buffer:
                return

            self.state.buffer.wait_for_first_frame(
                timeout=0.25,
                stall_cb=lambda: self.statusLoadingStarted.emit("Buffering..."),
            )

            # Check if user cancelled
            if not all([self.state.is_playing, self.state.buffer, self.state.audio_buffer]):
                return

            self._api._on_playback_started()

            # Start both audio and video at the same time
            self.state.next_frame_time_ns = self.state.next_audio_frame_time_ns = perf_counter_ns()
            self.statusLoadingStarted.emit("Playing...")

            # Start the render chain. Each frame will chain to the next
            self._play_next_audio_frame()
            self._play_next_frame()
        except Exception:
            self._stop_playback()
            raise
        finally:
            self.can_reload = True

    @run_in_loop(return_future=False)
    def _stop_playback(self) -> None:
        logger.debug("Stopping playback")
        self._stop_audio()
        self.state.is_playing = False

        self.state.reset()

        self._tbar.set_playback_controls_enabled(True)
        self._tbar.timeline.is_events_blocked = False
        self._tbar.playback_container.play_pause_btn.setChecked(False)

        self.can_reload = True

        self._api._on_playback_stopped()
        self.statusLoadingFinished.emit("Paused")

    @run_in_loop(return_future=False)
    def _stop_audio(self) -> None:
        self.state.audio_timer.stop()

        if aoutput := self._outputs_manager.current_aoutput:
            aoutput.sink.reset()

        self.state.reset_audio()

    def _restart_playback(self) -> None:
        self._stop_playback()
        self.state.is_playing = True
        self._start_playback()

    @run_in_background(name="PlaybackNextFrame")
    def _play_next_frame(self) -> None:
        if not self.state.is_playing:
            return

        if not (voutput := self._outputs_manager.current_voutput):
            logger.error("No current video output during playback")
            self.toggle_playback()
            return

        if self.state.buffer:
            try:
                result = self.state.buffer.get_next_frame()
            except Exception as e:
                logger.error(
                    "An error occured during the rendering of the frame %d with the message: (%s): %s",
                    self.state.current_frame + 1,
                    e.__class__.__name__,
                    e,
                )
                self.loadFailed.emit()
                return

            if result:
                frame_n, frame, plugin_frames = result

                if self.state.stop_at_frame is not None and frame_n >= self.state.stop_at_frame:
                    self.toggle_playback()
                    return

                self._track_fps()

                self.state.current_frame = frame_n
                self._tab_manager.current_view.last_frame = frame_n

                try:
                    with self._env.use(), frame:
                        image = voutput.packer.frame_to_qimage(frame)

                    self.frameRendered.emit(image)
                    self.timelineCursorChanged.emit(frame_n)

                    self._api._on_current_frame_changed(frame_n, plugin_frames)
                finally:
                    for frame_to_close in plugin_frames.values():
                        frame_to_close.close()

                self._schedule_or_continue()
                return

        if self.state.current_frame + 1 >= voutput.clip.num_frames:
            self.toggle_playback()
            return

        if self._tbar.playback_container.settings.uncapped:
            self._loop.from_thread(self._play_next_frame)
        else:
            self._schedule_or_continue()

    def _track_fps(self) -> None:
        now = perf_counter_ns()

        self.state.fps_history.append(now)

        if (total_elapsed := self.state.fps_history[-1] - self.state.fps_history[0]) > 0:
            avg_fps = (len(self.state.fps_history) - 1) * 1_000_000_000 / total_elapsed

            if (
                now - self.state.last_fps_update_ns
                >= SettingsManager.global_settings.playback.fps_update_interval * 1_000_000_000
            ):
                self.statusLoadingStarted.emit(f"Playing @ {avg_fps:.3f} fps")
                self.state.last_fps_update_ns = now

    def _schedule_or_continue(self) -> None:
        # Use absolute targets to prevent clock drift; catch up immediately if lagging
        self.state.next_frame_time_ns += self.state.frame_interval_ns

        # Schedule timer only if delay is significant; avoids timer overhead for <1ms delays
        if (delay_ns := self.state.next_frame_time_ns - perf_counter_ns()) > MIN_FRAME_DELAY_NS:
            self._loop.from_thread(lambda: self.state.video_timer.start(cround(delay_ns / 1_000_000)))
        else:
            self._play_next_frame()

    def _prepare_video(self, loop_range: range | None = None) -> None:
        if not (voutput := self._outputs_manager.current_voutput):
            raise RuntimeError("No video output available")

        # Calculate target frame interval for FPS limiting
        if self._tbar.playback_container.settings.uncapped:
            self.state.frame_interval_ns = 0
        elif voutput.clip.fps.denominator > 0:
            self.state.frame_interval_ns = cround(
                1_000_000_000
                * voutput.clip.fps.denominator
                / (voutput.clip.fps.numerator * self._tbar.playback_container.settings.speed)
            )
        else:
            self.state.frame_interval_ns = 0

        # Create and allocate video buffer
        self.state.buffer = FrameBuffer(video_output=voutput, env=self._env)
        self._api._register_plugin_nodes_to_buffer()

        self.state.buffer.allocate(self.state.current_frame, loop_range=loop_range)

        logger.debug(
            "Target frame interval: %d ns (fps=%s), buffer_size=%d",
            self.state.frame_interval_ns,
            voutput.clip.fps,
            self.state.buffer._size,
        )

    def _prepare_audio(self, loop_range: range | None = None) -> None:
        if (
            not (aoutput := self._outputs_manager.current_aoutput)
            or self._tbar.playback_container.is_muted
            or self._tbar.playback_container.settings.uncapped
            or not (voutput := self._outputs_manager.current_voutput)
        ):
            return

        if not aoutput.setup_sink(
            self._tbar.playback_container.settings.speed,
            self._tbar.playback_container.volume,
        ):
            return

        # Calculate starting audio frame from current video time
        self.state.current_audio_frame = aoutput.time_to_frame(
            voutput.frame_to_time(self.state.current_frame).total_seconds()
        )

        # Convert video-frame-based loop_range to audio-frame-based loop_range
        if loop_range:
            a_loop_range = range(
                aoutput.time_to_frame(voutput.frame_to_time(loop_range.start).total_seconds()),
                aoutput.time_to_frame(voutput.frame_to_time(loop_range.stop).total_seconds()),
            )
        else:
            a_loop_range = None

        # Allocate audio buffer for async pre-fetching of subsequent frames
        self.state.audio_buffer = AudioBuffer(aoutput, self._env)
        self.state.audio_buffer.allocate(self.state.current_audio_frame, loop_range=a_loop_range)

        self.state.audio_frame_interval_ns = cround(
            1_000_000_000
            * aoutput.fps.denominator
            / (aoutput.fps.numerator * self._tbar.playback_container.settings.speed)
        )

        logger.debug(
            "Audio prepared: prefilled to frame %d, interval=%d ns",
            self.state.current_audio_frame,
            self.state.audio_frame_interval_ns,
        )

    @run_in_loop
    def _play_next_audio_frame(self) -> None:
        if (
            not self.state.is_playing
            or not (aoutput := self._outputs_manager.current_aoutput)
            or self._tbar.playback_container.is_muted
            or self.state.audio_frame_interval_ns <= 0
        ):
            self.state.audio_timer.stop()
            return

        # Use absolute targets to prevent clock drift; catch up immediately if lagging
        self.state.next_audio_frame_time_ns += self.state.audio_frame_interval_ns

        if (
            aoutput.sink.bytesFree() >= aoutput.bytes_per_frame
            and self.state.audio_buffer
            and (result := self.state.audio_buffer.get_next_frame())
        ):
            frame_n, frame = result
            with frame:
                aoutput.render_raw_audio_frame(frame)
                self.state.current_audio_frame = frame_n

        # Audio uses strict 0 to minimize jitter; any lag must be processed immediately
        if (delay_ns := self.state.next_audio_frame_time_ns - perf_counter_ns()) > 0:
            self.state.audio_timer.start(cround(delay_ns / 1_000_000))
        else:
            self._play_next_audio_frame()

    # Signals
    @Slot(int, float, bool)
    def _on_playback_settings_changed(self, seek_step: int, speed: float, uncapped: bool) -> None:
        """Handle playback settings change from UI."""
        self._tbar.playback_container.settings.seek_step = seek_step
        self._tbar.playback_container.settings.speed = speed
        self._tbar.playback_container.settings.uncapped = uncapped

        if self.state.is_playing:
            self._restart_playback()

    def _on_play_zone(self, zone_frames: int, loop: bool) -> None:
        """Play a specific zone of frames."""
        if not (voutput := self._outputs_manager.current_voutput):
            return

        # Ensure zone doesn't exceed total frames
        end_frame = min(self.state.current_frame + zone_frames, voutput.clip.num_frames)

        loop_range = range(self.state.current_frame, end_frame) if loop else None
        stop_at = end_frame if not loop else None

        self._tbar.playback_container.play_pause_btn.setChecked(True)

        self._start_playback(loop_range=loop_range, stop_at=stop_at)

    def _on_audio_delay_changed(self, delay_s: float) -> None:
        """Handle audio delay change from UI."""
        if self.state.is_playing:
            self._stop_playback()

        with self._env.use():
            for aoutput in self._outputs_manager.aoutputs:
                aoutput.prepare_audio(delay_s, self._api)

    def _on_audio_output_changed(self, index: int) -> None:
        """Handle audio output selection change."""
        self.audioOutputChanged.emit(index)

        if self.state.is_playing:
            self._restart_playback()

    @Slot(Frame, Frame)
    def _on_frame_changed(self, frame: Frame, old_frame: Frame) -> None:
        logger.debug("Frame changed: frame=%d", frame)
        self.request_frame(frame)

    @Slot(float)
    def _on_volume_changed(self, volume: float) -> None:
        if aoutput := self._outputs_manager.current_aoutput:
            aoutput.volume = volume

    def _on_mute_changed(self, is_muted: bool) -> None:
        if is_muted:
            self._stop_audio()
        elif self.state.is_playing:
            self._restart_playback()

    @Slot(QTime, QTime)
    def _on_time_changed(self, time: QTime, old_time: QTime) -> None:
        logger.debug("Time changed: time=%s", time)

        # FIXME: Probably doesn't work with VFR here
        frame = cround(
            Time.from_qtime(time).total_seconds()
            * self._tbar.timeline.fps.numerator
            / self._tbar.timeline.fps.denominator
        )

        logger.debug("Time changed: frame=%d", frame)

        self.request_frame(frame)

    @Slot(Frame, Time)
    def _on_timeline_clicked(self, frame: Frame, time: Time) -> None:
        logger.debug("Timeline clicked: frame=%d, time=%s", frame, time)
        self.request_frame(frame)

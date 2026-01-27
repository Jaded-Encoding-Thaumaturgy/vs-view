from __future__ import annotations

from array import array
from contextlib import suppress
from enum import Enum, auto
from fractions import Fraction
from logging import getLogger
from typing import TYPE_CHECKING, Any

import vapoursynth as vs
from jetpytools import clamp, cround
from PySide6.QtCore import QIODevice
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink
from PySide6.QtWidgets import QApplication

from ...vsenv import run_in_loop
from ..plugins.manager import PluginManager
from ..settings import SettingsManager
from ..utils import LRUCache

if TYPE_CHECKING:
    from ...api._helpers import AudioMetadata
    from ..plugins import PluginAPI

logger = getLogger(__name__)


class PrettyChannelsLayout(Enum):
    UNKNOWN = auto(), "Unknown"
    STEREO = (
        (
            vs.AudioChannels.FRONT_LEFT,
            vs.AudioChannels.FRONT_RIGHT,
        ),
        "2.0",
    )
    SURROUND_5_1 = (
        (
            vs.AudioChannels.FRONT_LEFT,
            vs.AudioChannels.FRONT_RIGHT,
            vs.AudioChannels.FRONT_CENTER,
            vs.AudioChannels.LOW_FREQUENCY,
            vs.AudioChannels.SIDE_LEFT,
            vs.AudioChannels.SIDE_RIGHT,
        ),
        "5.1",
    )
    SURROUND_7_1 = (
        (
            vs.AudioChannels.FRONT_LEFT,
            vs.AudioChannels.FRONT_RIGHT,
            vs.AudioChannels.FRONT_CENTER,
            vs.AudioChannels.LOW_FREQUENCY,
            vs.AudioChannels.BACK_LEFT,
            vs.AudioChannels.BACK_RIGHT,
            vs.AudioChannels.SIDE_LEFT,
            vs.AudioChannels.SIDE_RIGHT,
        ),
        "7.1",
    )

    pretty_name: str

    def __init__(self, value: Any, pretty_name: str = "") -> None:
        self._value_ = value
        self.pretty_name = pretty_name

    @classmethod
    def _missing_(cls, value: object) -> Any:
        return next((member for member in cls if member.value == value), cls.UNKNOWN)


class AudioOutput:
    # Standard downmix coefficient for center/surround channels: sqrt(2)/2
    DOWNMIX_COEFF = 0.7071067811865476
    # https://github.com/vapoursynth/vapoursynth/blob/8cd1cba539bf70eea21dc242d43349603115632d/include/VapourSynth4.h#L36
    SAMPLES_PER_FRAME = 3072

    def __init__(self, vs_output: vs.AudioNode, vs_index: int, metadata: AudioMetadata | None) -> None:
        self.vs_output = vs_output
        self.vs_index = vs_index
        self.vs_name = metadata.name if metadata else None
        self.downmix = (
            metadata.downmix
            if metadata and metadata.downmix is not None
            else SettingsManager.global_settings.playback.downmix
        )
        self.chanels_layout = PrettyChannelsLayout(tuple(self.vs_output.channels))

        self._cache_delay_audio = LRUCache[float, vs.AudioNode]()

    @property
    def fps(self) -> Fraction:
        return Fraction(self.prepared_audio.sample_rate, self.SAMPLES_PER_FRAME)

    @property
    def bytes_per_frame(self) -> int:
        return self.SAMPLES_PER_FRAME * self.prepared_audio.num_channels * self.prepared_audio.bytes_per_sample

    @property
    def volume(self) -> float:
        return getattr(self, "_volume", 0.5)

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = clamp(value, 0.0, 1.0)
        self.sink.setVolume(self._volume)

    def prepare_audio(self, delay_s: float, api: PluginAPI) -> None:
        audio = self._cache_delay_audio.get(delay_s)

        if audio is None:
            audio = self.vs_output

            if PluginManager.audio_processor:
                audio = PluginManager.audio_processor(api).prepare(audio)

            if self.vs_output.num_channels > 2 and self.downmix:
                audio = self.create_stereo_downmix(audio)

            # Apply audio delay
            if delay_s != 0:
                delay_samples = round(delay_s * audio.sample_rate)
                abs_delay = abs(delay_samples)
                silence = audio.std.BlankAudio(length=abs_delay, keep=True)

                if delay_samples > 0:
                    audio = (silence + audio)[: audio.num_samples]
                elif delay_samples < 0:
                    audio = (audio[abs_delay:] + silence)[: audio.num_samples]

            self._cache_delay_audio[delay_s] = audio

        self.prepared_audio = audio

        # Playback node
        if audio.sample_type == vs.FLOAT:
            self.array_type = "f"
            sample_format = QAudioFormat.SampleFormat.Float
        elif audio.bits_per_sample <= 16:
            self.array_type = "h"
            sample_format = QAudioFormat.SampleFormat.Int16
        else:
            self.array_type = "i"
            sample_format = QAudioFormat.SampleFormat.Int32

        self._audio_buffer = array(self.array_type, [0] * (self.SAMPLES_PER_FRAME * audio.num_channels))

        self.qformat = QAudioFormat()
        self.qformat.setChannelCount(audio.num_channels)
        self.qformat.setSampleRate(audio.sample_rate)
        self.qformat.setSampleFormat(sample_format)

        self.sink = AudioSink(self.qformat)

    def clear(self) -> None:
        """Clear VapourSynth resources."""
        if hasattr(self, "sink"):
            self.sink.stop()
            self.sink.deleteLater()
            del self.sink

        for name in ["prepared_audio", "vs_output"]:
            with suppress(AttributeError):
                delattr(self, name)

        self._cache_delay_audio.clear()

    def render_raw_audio_frame(self, frame: vs.AudioFrame) -> None:
        if not self.sink.ready:
            return

        num_channels = self.prepared_audio.num_channels

        if num_channels == 1:
            data = frame[0].tobytes()
        else:
            # We can get the number of samples from the shape of the first channel
            required_len = frame[0].shape[0] * num_channels

            buffer = (
                self._audio_buffer
                if required_len == len(self._audio_buffer)
                else array(self.array_type, [0] * required_len)
            )

            for i in range(num_channels):
                buffer[i::num_channels] = array(self.array_type, frame[i].tobytes())

            data = buffer.tobytes()

        with suppress(RuntimeError):
            self.sink.io.write(data)

    @run_in_loop(return_future=False)
    def setup_sink(self, speed: float = 1.0, volume: float = 1.0) -> bool:
        """
        Initialize the audio sink for playback.

        Returns True if successful, False if audio format not supported.
        """
        self.qformat.setSampleRate(round(self.prepared_audio.sample_rate * speed))
        self.sink.stop()
        self.sink.deleteLater()
        self.sink = AudioSink(self.qformat)
        self.sink.setup(SettingsManager.global_settings.playback.audio_buffer_size * self.bytes_per_frame, self.volume)

        if not self.sink.ready:
            logger.error(
                "Failed to start audio sink - format may not be supported (%d Hz, speed=%.2fx)",
                speed,
                self.qformat.sampleRate(),
            )
            return False

        self.volume = volume

        logger.debug(
            "Audio sink initialized: %d Hz (base %d Hz, speed=%.2fx), %d channels, format=%s",
            self.qformat.sampleRate(),
            self.prepared_audio.sample_rate,
            speed,
            self.prepared_audio.num_channels,
            self.qformat.sampleFormat(),
        )
        return True

    def create_stereo_downmix(self, audio: vs.AudioNode) -> vs.AudioNode:
        """
        Create a stereo downmix of the source audio using std.AudioMix.
        """

        # Build downmix matrix
        # 5.1/7.1 to stereo downmix coefficients:
        # L = 1.0*L + 0.707*C + 0.707*Ls + 0.707*Lb
        # R = 1.0*R + 0.707*C + 0.707*Rs + 0.707*Rb

        left_coeffs = [0.0] * audio.num_channels
        right_coeffs = [0.0] * audio.num_channels

        for i, channel in enumerate(audio.channels):
            match channel:
                case vs.AudioChannels.FRONT_LEFT:
                    left_coeffs[i] = 1.0
                case vs.AudioChannels.FRONT_RIGHT:
                    right_coeffs[i] = 1.0
                case vs.AudioChannels.FRONT_CENTER:
                    left_coeffs[i] = self.DOWNMIX_COEFF
                    right_coeffs[i] = self.DOWNMIX_COEFF
                case vs.AudioChannels.SIDE_LEFT | vs.AudioChannels.BACK_LEFT:
                    left_coeffs[i] = self.DOWNMIX_COEFF
                case vs.AudioChannels.SIDE_RIGHT | vs.AudioChannels.BACK_RIGHT:
                    right_coeffs[i] = self.DOWNMIX_COEFF

        normalization = max(sum(c**2 for c in left_coeffs) ** 0.5, sum(c**2 for c in right_coeffs) ** 0.5, 1.0)

        final_matrix = [(c / normalization) for c in (left_coeffs + right_coeffs)]

        logger.debug(
            "Creating downmix: %d channels -> Stereo. Normalization: %.4f. Matrix: %s. Layout: %s",
            audio.num_channels,
            normalization,
            final_matrix,
            [c.name for c in audio.channels],
        )

        return audio.std.AudioMix(matrix=final_matrix, channels_out=[vs.FRONT_LEFT, vs.FRONT_RIGHT])

    def time_to_frame(self, seconds: float, *, eps: float = 1e-6) -> int:
        return cround(seconds * self.fps, eps=eps)

    def frame_to_time(self, frame_num: int) -> float:
        return float(frame_num / self.fps)

    def __del__(self) -> None:
        self.clear()


class AudioSink(QAudioSink):
    def __init__(self, format: QAudioFormat) -> None:
        super().__init__(format)
        self.ready = False
        # Move the sink to the main thread so it can be controlled from LoaderWorkspace._stop_audio
        self.moveToThread(QApplication.instance().thread())  # type: ignore[union-attr]

    def setVolume(self, volume: float) -> None:
        """Override to handle perceptual to linear volume conversion."""
        super().setVolume(
            QAudio.convertVolume(
                volume,
                QAudio.VolumeScale.LogarithmicVolumeScale,
                QAudio.VolumeScale.LinearVolumeScale,
            )
        )

    @property
    def io(self) -> QIODevice:
        if not self.ready:
            raise RuntimeError("Device not ready")
        return self._iodevice

    def stop(self) -> None:
        self.ready = False
        super().stop()

    def reset(self) -> None:
        self.ready = False
        super().reset()

    def setup(self, buffer_size: int, volume: float) -> None:
        self.setBufferSize(buffer_size)
        self.setVolume(volume)

        self._iodevice = self.start()

        if not self._iodevice:
            return

        self.ready = True

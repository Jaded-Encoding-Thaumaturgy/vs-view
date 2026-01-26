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
from ..settings import SettingsManager

if TYPE_CHECKING:
    from ...api._helpers import AudioMetadata

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

    def __init__(self, vs_output: vs.AudioNode, vs_index: int, metadata: AudioMetadata | None = None) -> None:
        self.vs_output = vs_output
        self.vs_index = vs_index
        self.vs_name = metadata.name if metadata else None
        self.downmix = metadata.downmix if metadata else None
        self.chanels_layout = PrettyChannelsLayout(tuple(self.vs_output.channels))

        # Playback node
        if self.vs_output.num_channels > 2 and self.downmix:
            self.prepared_audio = self.create_stereo_downmix()
        else:
            self.prepared_audio = self.vs_output

        if self.prepared_audio.sample_type == vs.FLOAT:
            self.array_type = "f"
            sample_format = QAudioFormat.SampleFormat.Float
        elif self.prepared_audio.bits_per_sample <= 16:
            self.array_type = "h"
            sample_format = QAudioFormat.SampleFormat.Int16
        else:
            self.array_type = "i"
            sample_format = QAudioFormat.SampleFormat.Int32

        self._audio_buffer = array(self.array_type, [0] * (self.SAMPLES_PER_FRAME * self.prepared_audio.num_channels))

        self.qformat = QAudioFormat()
        self.qformat.setChannelCount(self.prepared_audio.num_channels)
        self.qformat.setSampleRate(self.prepared_audio.sample_rate)
        self.qformat.setSampleFormat(sample_format)

        self.sink = AudioSink(self.qformat, self)

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

    def clear(self) -> None:
        """Clear VapourSynth resources."""
        if hasattr(self, "sink"):
            self.sink.stop()
            del self.sink.output
            self.sink.deleteLater()
            del self.sink

        for name in ["prepared_audio", "vs_output"]:
            with suppress(AttributeError):
                delattr(self, name)

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
        self.sink = AudioSink(self.qformat, self)

        self.sink.setup()

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

    def create_stereo_downmix(self) -> vs.AudioNode:
        """
        Create a stereo downmix of the source audio using std.AudioMix.
        """

        num_channels = self.vs_output.num_channels

        # Build downmix matrix
        # 5.1/7.1 to stereo downmix coefficients:
        # L = 1.0*L + 0.707*C + 0.707*Ls + 0.0*LFE
        # R = 1.0*R + 0.707*C + 0.707*Rs + 0.0*LFE

        left_coeffs = [0.0] * num_channels
        right_coeffs = [0.0] * num_channels

        # Standard layout: L, R, C, LFE, Ls, Rs
        left_coeffs[0] = 1.0  # Left
        right_coeffs[1] = 1.0  # Right

        # Center
        if num_channels >= 3:
            left_coeffs[2] = self.DOWNMIX_COEFF
            right_coeffs[2] = self.DOWNMIX_COEFF

        # Skip LFE (3)

        if num_channels >= 6:
            left_coeffs[4] = self.DOWNMIX_COEFF  # Left Surround
            right_coeffs[5] = self.DOWNMIX_COEFF  # Right Surround

        if num_channels >= 8:
            left_coeffs[6] = self.DOWNMIX_COEFF  # Side Left
            right_coeffs[7] = self.DOWNMIX_COEFF  # Side Right

        # Calculate normalization using root-sum-square to preserve power.
        normalization = max(sum(c**2 for c in left_coeffs) ** 0.5, sum(c**2 for c in right_coeffs) ** 0.5, 1.0)

        final_matrix = [(c / normalization) for c in (left_coeffs + right_coeffs)]

        logger.debug(
            "Creating downmix: %d channels -> Stereo. Normalization: %.4f. Matrix: %s",
            num_channels,
            normalization,
            final_matrix,
        )

        return self.vs_output.std.AudioMix(matrix=final_matrix, channels_out=[vs.FRONT_LEFT, vs.FRONT_RIGHT])

    def time_to_frame(self, seconds: float, *, eps: float = 1e-6) -> int:
        return cround(seconds * self.fps, eps=eps)

    def frame_to_time(self, frame_num: int) -> float:
        return float(frame_num / self.fps)

    def __del__(self) -> None:
        self.clear()


class AudioSink(QAudioSink):
    def __init__(self, format: QAudioFormat, output: AudioOutput) -> None:
        super().__init__(format)
        self.output = output
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

    def setup(self) -> None:
        self.setBufferSize(SettingsManager.global_settings.playback.audio_buffer_size * self.output.bytes_per_frame)
        self.setVolume(self.output.volume)

        self._iodevice = self.start()

        if not self._iodevice:
            return

        self.ready = True

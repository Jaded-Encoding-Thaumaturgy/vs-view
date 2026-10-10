from __future__ import annotations

import pytest
import vapoursynth as vs
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from vsview.app.tools.cropping.utils import CommandLabel, CropValues, CustomRect, CustomSize

pytestmark = [pytest.mark.unit, pytest.mark.qt]


@pytest.mark.parametrize(
    ("input_rect", "expected_rect"),
    [
        # Inside image bounds
        (CustomRect(10, 20, 50, 40), CustomRect(10, 20, 50, 40)),
        # Exactly matching image boundaries
        (CustomRect(0, 0, 100, 100), CustomRect(0, 0, 100, 100)),
        # Negative x
        (CustomRect(-15, 20, 50, 40), CustomRect(0, 20, 35, 40)),
        # Negative y
        (CustomRect(10, -25, 50, 60), CustomRect(10, 0, 50, 35)),
        # Both negative x and negative y
        (CustomRect(-10, -20, 50, 60), CustomRect(0, 0, 40, 40)),
        # Overflowing width: x + w > size.width()
        (CustomRect(80, 10, 40, 30), CustomRect(80, 10, 20, 30)),
        # Overflowing height: y + h > size.height()
        (CustomRect(10, 75, 30, 50), CustomRect(10, 75, 30, 25)),
        # Overflowing both width and height
        (CustomRect(60, 70, 80, 90), CustomRect(60, 70, 40, 30)),
        # Negative origins and overflowing width and height
        (CustomRect(-20, -30, 200, 200), CustomRect(0, 0, 100, 100)),
    ],
)
def test_custom_rect_clamp(input_rect: CustomRect, expected_rect: CustomRect) -> None:
    size = QSize(100, 100)
    rect = CustomRect(input_rect.x(), input_rect.y(), input_rect.width(), input_rect.height())

    rect.clamp(size)

    assert rect == expected_rect


@pytest.mark.parametrize(
    "degenerate_rect",
    [
        # Entirely to the left of image
        CustomRect(-50, 10, 20, 20),
        # Entirely to the right of image
        CustomRect(120, 10, 20, 20),
        # Entirely above image
        CustomRect(10, -50, 20, 20),
        # Entirely below image
        CustomRect(10, 120, 20, 20),
        # Zero width
        CustomRect(10, 10, 0, 20),
        # Zero height
        CustomRect(10, 10, 20, 0),
        # Inverted / negative width
        CustomRect(50, 10, -20, 20),
        # Inverted / negative height
        CustomRect(10, 50, 20, -20),
    ],
)
def test_custom_rect_clamp_degenerate_remains_unmodified(degenerate_rect: CustomRect) -> None:
    size = QSize(100, 100)
    rect = CustomRect(degenerate_rect.x(), degenerate_rect.y(), degenerate_rect.width(), degenerate_rect.height())

    rect.clamp(size)

    assert rect == degenerate_rect


@pytest.mark.parametrize(
    ("mod", "expected_rect"),
    [
        (1, CustomRect(3, 5, 11, 13)),
        (2, CustomRect(2, 4, 12, 14)),
        (4, CustomRect(0, 4, 16, 16)),
        (8, CustomRect(0, 0, 16, 24)),
    ],
)
def test_custom_rect_sanitize_mod_alignment(mod: int, expected_rect: CustomRect) -> None:
    size = QSize(100, 100)
    rect = CustomRect(3, 5, 11, 13)

    rect.sanitize(size, mod=mod)

    assert rect == expected_rect


@pytest.mark.parametrize("invalid_mod", [0, -5])
def test_custom_rect_sanitize_mod_clamping_defense(invalid_mod: int) -> None:
    size = QSize(100, 100)
    rect = CustomRect(3, 5, 11, 13)

    rect.sanitize(size, mod=invalid_mod)

    # Mod < 1 defensively defaults to mod 1, keeping coordinates unchanged
    assert rect == CustomRect(3, 5, 11, 13)


def test_custom_rect_sanitize_boundary_clamping() -> None:
    size = QSize(100, 100)
    rect = CustomRect(90, 90, 15, 15)

    rect.sanitize(size, mod=4)

    assert rect == CustomRect(88, 88, 12, 12)


@pytest.mark.parametrize(
    "invalid_rect",
    [
        CustomRect(120, 10, 20, 20),
        CustomRect(10, 120, 20, 20),
        CustomRect(-50, 10, 10, 10),
        CustomRect(10, -50, 10, 10),
        CustomRect(50, 50, -60, 20),
    ],
)
def test_custom_rect_sanitize_collapse_raises_value_error(invalid_rect: CustomRect) -> None:
    size = QSize(100, 100)

    with pytest.raises(ValueError, match=r"^Rect is invalid$"):
        invalid_rect.sanitize(size, mod=2)


@pytest.mark.parametrize(
    ("margins", "expected_rect"),
    [
        ((10, 20, 30, 40), CustomRect(10, 20, 60, 40)),
        ((-10, -5, -20, -15), CustomRect(0, 0, 100, 100)),
    ],
)
def test_custom_rect_from_crop_valid(margins: tuple[int, int, int, int], expected_rect: CustomRect) -> None:
    size = QSize(100, 100)
    left, top, right, bottom = margins

    rect = CustomRect.from_crop(left, top, right, bottom, size, mod=2)

    assert rect == expected_rect


@pytest.mark.parametrize("excessive_margins", [(60, 10, 60, 10), (10, 60, 10, 60)])
def test_custom_rect_from_crop_excessive_raises(excessive_margins: tuple[int, int, int, int]) -> None:
    size = QSize(100, 100)
    left, top, right, bottom = excessive_margins

    with pytest.raises(ValueError, match=r"^Rect is invalid$"):
        CustomRect.from_crop(left, top, right, bottom, size, mod=2)


@pytest.mark.parametrize(
    ("rect", "image_size", "expected_values"),
    [
        (
            QRect(0, 0, 1920, 1080),
            QSize(1920, 1080),
            CropValues(left=0, top=0, right=0, bottom=0, width=1920, height=1080),
        ),
        (QRect(10, 10, 80, 80), QSize(100, 100), CropValues(left=10, top=10, right=10, bottom=10, width=80, height=80)),
        (
            QRect(100, 50, 1700, 950),
            QSize(1920, 1080),
            CropValues(left=100, top=50, right=120, bottom=80, width=1700, height=950),
        ),
    ],
)
def test_crop_values_from_rect(rect: QRect, image_size: QSize, expected_values: CropValues) -> None:
    values = CropValues.from_rect(rect, image_size)
    assert values == expected_values


@pytest.mark.vpy("initial-core")
def test_custom_size_from_clip() -> None:
    clip = vs.core.std.BlankClip(width=1280, height=720)

    size = CustomSize.from_clip(clip)

    assert isinstance(size, CustomSize)
    assert isinstance(size, QSize)
    assert size.width() == 1280
    assert size.height() == 720


def test_command_label(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)

    template = "clip.std.Crop(left={left}, top={top}, right={right}, bottom={bottom})"
    label = CommandLabel(template, parent)
    qtbot.addWidget(label)

    assert label.original_text == template
    assert label.text() == template

    # Formatting with keyword arguments
    label.format(left=10, top=20, right=30, bottom=40)
    assert label.text() == "clip.std.Crop(left=10, top=20, right=30, bottom=40)"

    # Successive format call resets to original before formatting
    label.format(left=0, top=0, right=0, bottom=0)
    assert label.text() == "clip.std.Crop(left=0, top=0, right=0, bottom=0)"

    # Reset text restores original template
    label.reset_text()
    assert label.text() == template

    # Positional arguments formatting
    pos_label = CommandLabel("Crop: {}, {}", parent)
    qtbot.addWidget(pos_label)
    pos_label.format(100, 200)
    assert pos_label.text() == "Crop: 100, 200"

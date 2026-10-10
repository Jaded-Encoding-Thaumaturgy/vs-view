from __future__ import annotations

import logging
from typing import Any

import pytest
import vapoursynth as vs

from vsview.app.tools.frameprops.formatters import CustomEllipsis, FormatterProperty, FormatterRegistry

pytestmark = [pytest.mark.unit]


def test_formatter_property_callable_formatter() -> None:
    # Arrange
    prop = FormatterProperty(prop_key="test_metric", value_formatter=lambda v: f"{v * 100:.1f}%")
    # Act
    result = prop.format_value(0.954)
    # Assert
    assert result == "95.4%"


def test_formatter_property_callable_formatter_exception_fallback(caplog: pytest.LogCaptureFixture) -> None:
    # Arrange
    def faulty_formatter(value: object) -> str:
        raise ValueError("Formatting failure")

    faulty_prop = FormatterProperty(prop_key="broken_key", value_formatter=faulty_formatter)

    # Act
    with caplog.at_level(logging.ERROR):
        result = faulty_prop.format_value(42)

    # Assert
    assert result == "42"
    assert "There was an error when trying to format 'broken_key':" in caplog.text


def test_formatter_property_dict_formatter() -> None:
    # Arrange
    prop = FormatterProperty(prop_key="gamut_mode", value_formatter={0: "Narrow", 1: "Standard", 2: "Wide"})
    # Act & Assert - Key hits
    assert prop.format_value(1) == "Standard"
    assert prop.format_value(2) == "Wide"
    # Act & Assert - Key misses fall back to default_format
    assert prop.format_value(999) == "999"
    assert prop.format_value(3.14159) == f"{3.14159:.6g}"


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        (100, "100"),
        ("sample text", "sample text"),
        ([1, 2, 3], "[1, 2, 3]"),
    ],
)
def test_formatter_property_none_formatter(input_val: Any, expected: str) -> None:
    prop = FormatterProperty(prop_key="generic_prop", value_formatter=None)
    assert prop.format_value(input_val) == expected


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [(b"Hello World", "Hello World"), (bytearray(b"Bytearray test"), "Bytearray test"), ("ümläut".encode(), "ümläut")],
)
def test_default_format_bytes_and_bytearray(input_val: bytes | bytearray, expected: str) -> None:
    assert FormatterProperty.default_format(input_val) == expected


def test_default_format_bytes_long_truncated() -> None:
    long_bytes = b"A" * 150
    expected = ("A" * 128) + "..."

    assert FormatterProperty.default_format(long_bytes) == expected


def test_default_format_bytearray_long_truncated() -> None:
    long_bytearray = bytearray(b"B" * 200)
    expected = ("B" * 128) + "..."

    assert FormatterProperty.default_format(long_bytearray) == expected


def test_default_format_invalid_utf8_fallback() -> None:
    invalid_utf8 = b"\xff\xfe\xfd"
    assert FormatterProperty.default_format(invalid_utf8) == repr(invalid_utf8)


def test_default_format_long_invalid_utf8_truncated() -> None:
    long_invalid_utf8 = b"\xff" * 50
    repr_str = repr(long_invalid_utf8)
    expected = f"{repr_str[:128]}..."

    result = FormatterProperty.default_format(long_invalid_utf8)

    assert result == expected
    assert len(result) == 131


@pytest.mark.parametrize(("input_val", "expected"), [(0, "0"), (123456, "123456"), (-9876, "-9876")])
def test_default_format_int(input_val: int, expected: str) -> None:
    assert FormatterProperty.default_format(input_val) == expected


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        (0.0, "0"),
        (100.0, "100"),
        (1.2345678, f"{1.2345678:.6g}"),
        (0.0000123456, f"{0.0000123456:.6g}"),
    ],
)
def test_default_format_float(input_val: float, expected: str) -> None:
    assert FormatterProperty.default_format(input_val) == expected


@pytest.mark.vpy("initial-core")
def test_default_format_raw_frame() -> None:
    frame = vs.core.create_video_frame(vs.GRAYS, 640, 360)

    assert FormatterProperty.default_format(frame, repr_frame=False) == str(frame).replace("\t", "    ").rstrip()
    assert FormatterProperty.default_format(frame, repr_frame=True) == repr(frame)


def test_default_format_list_and_custom_ellipsis() -> None:
    ellipsis_obj = CustomEllipsis()
    assert repr(ellipsis_obj) == "..."
    assert str(ellipsis_obj) == "..."

    short_list = [1, 2, 3, "test"]
    assert FormatterProperty.default_format(short_list) == "[1, 2, 3, 'test']"

    long_list = list(range(200))
    expected_list_str = str([*long_list[:64], CustomEllipsis(), *long_list[-64:]])
    assert FormatterProperty.default_format(long_list) == expected_list_str


def test_default_format_dict_and_fallback_strings() -> None:
    short_dict = {"a": 1, "b": 2}
    assert FormatterProperty.default_format(short_dict) == "{'a': 1, 'b': 2}"

    short_str = "Standard short string"
    assert FormatterProperty.default_format(short_str) == short_str

    long_str = "x" * 300
    expected_long_str = ("x" * 256) + "..."
    assert FormatterProperty.default_format(long_str) == expected_long_str

    assert FormatterProperty.default_format(True) == "True"
    assert FormatterProperty.default_format(False) == "False"


def test_formatter_registry_singleton_and_registration(isolate_formatter_registry: None) -> None:
    reg1 = FormatterRegistry()
    reg2 = FormatterRegistry()
    assert reg1 is reg2

    p_callable = FormatterProperty(prop_key="key_c", value_formatter=lambda v: f"C_{v}")
    p_none = FormatterProperty(prop_key="key_n", value_formatter=None)

    FormatterRegistry.register(p_callable, p_none)

    assert FormatterRegistry.has_formatter("key_c") is True
    assert FormatterRegistry.has_formatter("key_n") is False
    assert FormatterRegistry.has_formatter("non_existent") is False


def test_formatter_registry_register_nested_iterables(isolate_formatter_registry: None) -> None:
    p1 = FormatterProperty(prop_key="k1", value_formatter=lambda v: "v1")
    p2 = FormatterProperty(prop_key="k2", value_formatter=lambda v: "v2")
    p3 = FormatterProperty(prop_key="k3", value_formatter=lambda v: "v3")

    FormatterRegistry.register([p1, [p2, [p3]]])

    assert FormatterRegistry.has_formatter("k1") is True
    assert FormatterRegistry.has_formatter("k2") is True
    assert FormatterRegistry.has_formatter("k3") is True


def test_formatter_registry_ordering(isolate_formatter_registry: None) -> None:
    p1 = FormatterProperty(prop_key="first")
    p2 = FormatterProperty(prop_key="second")
    p3 = FormatterProperty(prop_key="third")

    FormatterRegistry.register(p1, p2, p3)

    assert FormatterRegistry.get_property_order("first") == 0
    assert FormatterRegistry.get_property_order("second") == -1
    assert FormatterRegistry.get_property_order("third") == -2
    assert FormatterRegistry.get_property_order("unregistered") == -1000

    p1_updated = FormatterProperty(prop_key="first", value_formatter=lambda v: "updated")
    FormatterRegistry.register(p1_updated)
    assert FormatterRegistry.get_property_order("first") == 0


def test_formatter_registry_format_value(isolate_formatter_registry: None) -> None:
    prop = FormatterProperty(prop_key="fps", value_formatter=lambda v: f"{v:.2f} fps")
    FormatterRegistry.register(prop)

    assert FormatterRegistry.format_value("fps", 23.976) == "23.98 fps"
    assert FormatterRegistry.format_value("unknown_int", 42) == "42"
    assert FormatterRegistry.format_value("unknown_float", 1.2345678) == f"{1.2345678:.6g}"
    assert FormatterRegistry.format_value("unknown_bytes", b"bytes") == "bytes"

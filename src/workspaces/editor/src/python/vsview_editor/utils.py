import sys
from collections.abc import Callable
from functools import wraps
from logging import getLogger
from pathlib import Path
from typing import Any, Generic, TypeVar, overload, override

from PySide6.QtCore import QUrl, Slot

if sys.version_info >= (3, 13):
    FallbackT = TypeVar("FallbackT", default=None)
else:
    import typing_extensions

    FallbackT = typing_extensions.TypeVar("FallbackT", default=None)

logger = getLogger(__name__)


class SafeSlot(Generic[FallbackT]):
    @overload
    def __init__(
        self,
        *types: type | str,
        name: str | None = ...,
        result: type | str | None = ...,
        fallback: None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        *types: type | str,
        name: str | None = ...,
        result: type | str | None = ...,
        fallback: FallbackT,
    ) -> None: ...
    def __init__(
        self,
        *types: type | str,
        name: str | None = None,
        result: type | str | None = None,
        fallback: Any = None,
    ) -> None:
        self._types = types
        self._kwargs = dict[str, Any]()
        self._fallback = fallback

        if name is not None:
            self._kwargs["name"] = name
        if result is not None:
            self._kwargs["result"] = result

    def __call__[**P, R](self, func: Callable[P, R]) -> Callable[P, R | FallbackT]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | FallbackT:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.debug("%s failed", func.__name__, stacklevel=2, exc_info=e)
                return self._fallback

        return Slot(*self._types, **self._kwargs)(wrapper)


class WorkspaceUri(QUrl):
    @property
    def local_path(self) -> Path | None:
        local_path = self.toLocalFile()
        return (
            Path(local_path)
            if self.scheme() == "file" and not local_path.startswith(("/workspace", "\\workspace"))
            else None
        )


class ContentPath:
    __slots__ = ("code", "filename")

    def __init__(self, code: str, filename: str) -> None:
        self.code = code
        self.filename = filename

    def __fspath__(self) -> str:
        return self.filename

    def __len__(self) -> int:
        return len(self.code)

    @override
    def __str__(self) -> str:
        return self.code

    @override
    def __repr__(self) -> str:
        return self.filename

    def splitlines(self, keepends: bool = False) -> list[str]:
        return self.code.splitlines(keepends)

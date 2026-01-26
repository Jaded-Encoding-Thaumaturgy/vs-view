from collections import UserDict, defaultdict
from collections.abc import Callable
from os import PathLike
from typing import Any, NamedTuple

from jetpytools import copy_signature
from vapoursynth import VideoNode


class VideoMetadata(NamedTuple):
    name: str
    alpha: VideoNode | None


class AudioMetadata(NamedTuple):
    name: str
    downmix: bool | None


class DefaultUserDict[K, V](UserDict[K, V]):
    @copy_signature(defaultdict[K, V].__init__)
    def __init__(self, default_factory: Callable[[], V] | None = None, /, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.default_factory = default_factory

    def __missing__(self, key: K) -> V:
        if self.default_factory is None:
            raise KeyError(key)

        new_value = self.default_factory()

        self.data[key] = new_value

        return new_value


class OutputMetadata(DefaultUserDict[str, dict[int, Any]]):
    def _hash_key(self, key: str | PathLike[str]) -> str:
        from ..app.utils import path_to_hash

        return path_to_hash(key)

    def __getitem__(self, key: str | PathLike[str]) -> dict[int, Any]:
        return super().__getitem__(self._hash_key(key))

    def __setitem__(self, key: str | PathLike[str], value: dict[int, Any]) -> None:
        super().__setitem__(self._hash_key(key), value)

    def __contains__(self, key: object) -> bool:
        return super().__contains__(self._hash_key(str(key)))


output_metadata = OutputMetadata(dict)

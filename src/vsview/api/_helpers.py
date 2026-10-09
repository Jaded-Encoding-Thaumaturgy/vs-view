from collections import UserDict, defaultdict
from collections.abc import Callable
from os import PathLike
from typing import Any, override

from jetpytools import copy_signature


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

    @override
    def clear(self) -> None:
        self.data.clear()

    @override
    @copy_signature(UserDict[K, V].pop)
    def pop(self, key: K, default: Any = ...) -> Any:
        return self.data.pop(key) if default is ... else self.data.pop(key, default)

    @override
    def popitem(self) -> tuple[K, V]:
        return self.data.popitem()


class OutputMetadata(DefaultUserDict[str, dict[int, Any]]):
    def _hash_key(self, key: str | PathLike[str]) -> str:
        from ..app.utils import path_to_hash

        return path_to_hash(key)

    @override
    def __getitem__(self, key: str | PathLike[str]) -> dict[int, Any]:
        return super().__getitem__(self._hash_key(key))

    @override
    def __setitem__(self, key: str | PathLike[str], value: dict[int, Any]) -> None:
        super().__setitem__(self._hash_key(key), value)

    @override
    def __delitem__(self, key: str | PathLike[str]) -> None:
        super().__delitem__(self._hash_key(key))

    @override
    def __contains__(self, key: object) -> bool:
        return super().__contains__(self._hash_key(str(key)))

    @override
    @copy_signature(DefaultUserDict[str, dict[int, Any]].pop)
    def pop(self, key: str | PathLike[str], default: Any = ...) -> Any:
        key_hash = self._hash_key(key)
        return self.data.pop(key_hash) if default is ... else self.data.pop(key_hash, default)


output_metadata = OutputMetadata(dict)

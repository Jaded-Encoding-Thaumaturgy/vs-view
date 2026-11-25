import sys
from contextlib import suppress
from io import BufferedRandom
from pathlib import Path


class LockPath:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: BufferedRandom | None = None

    def acquire(self) -> bool:
        fh = self.path.open("a+b")

        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False

        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()

        with suppress(OSError):
            self.path.unlink(missing_ok=True)

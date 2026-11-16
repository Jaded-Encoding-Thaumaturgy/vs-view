from __future__ import annotations

from collections.abc import Callable
from logging import getLogger

from vsview.api import PluginSecrets, SecretsError

logger = getLogger(__name__)

READ_ERROR_MESSAGE = "Could not read the saved secrets from the keyring. New values are kept for this session only."
WRITE_ERROR_MESSAGE = "Could not save the secrets to the keyring. They are kept for this session only."


class SessionSecrets:
    def __init__(self, secrets: PluginSecrets, on_error: Callable[[str], object]) -> None:
        self._secrets = secrets
        self._on_error = on_error
        self._session = dict[tuple[str, str], str | None]()
        self._unreadable = set[tuple[str, str]]()

    def get(self, context: str, key: str) -> str | None:
        if (context, key) in self._session:
            return self._session[(context, key)]

        try:
            return self._secrets.get(context, key)
        except SecretsError:
            logger.warning("Could not read the %s/%s secret from the keyring", context, key, exc_info=True)
            self._unreadable.add((context, key))
            self._on_error(READ_ERROR_MESSAGE)
            return None

    def set(self, context: str, key: str, value: str | None) -> None:
        # The field of an unreadable secret starts empty, so saving it could erase the stored secret.
        if (context, key) in self._unreadable:
            self._session[(context, key)] = value
            return

        try:
            if value:
                self._secrets.set(context, key, value)
            else:
                self._secrets.delete(context, key)
        except SecretsError:
            logger.warning("Could not save the %s/%s secret to the keyring", context, key, exc_info=True)
            self._session[(context, key)] = value
            self._on_error(WRITE_ERROR_MESSAGE)
        else:
            self._session.pop((context, key), None)

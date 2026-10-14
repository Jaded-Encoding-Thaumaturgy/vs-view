"""Secrets manager backed by the OS keyring."""

from __future__ import annotations

import json
from contextlib import suppress
from importlib.util import find_spec
from logging import getLogger
from types import ModuleType
from typing import TYPE_CHECKING, Any

from jetpytools import Singleton, cachedproperty, inject_self

logger = getLogger(__name__)


class SecretsError(RuntimeError):
    """Raised when secure secret storage is unavailable or fails."""


class SecretsManager(Singleton):
    """Manage secure storage of plugin and application secrets using the OS keyring."""

    prefix_service_name = "jet.vsview"

    if TYPE_CHECKING:
        import keyring
        from keyring.credentials import Credential
        from keyring.errors import KeyringError, PasswordDeleteError

    else:

        @cachedproperty
        def keyring(self) -> ModuleType:
            if find_spec("keyring"):
                import keyring

                return keyring

            raise SecretsError("The 'keyring' dependency is not installed.")

        @cachedproperty
        def KeyringError(self) -> type[Exception]:
            return self.keyring.errors.KeyringError

        @cachedproperty
        def PasswordDeleteError(self) -> type[Exception]:
            return self.keyring.errors.PasswordDeleteError

    @classmethod
    def _service_name(cls, namespace: str, context: str) -> str:
        return f"{cls.prefix_service_name}.{namespace}/{context}"

    @inject_self
    def get(self, namespace: str, context: str, username: str) -> str | None:
        """
        Retrieve a password/secret from the keyring.

        Args:
            namespace: The application or plugin identifier.
            context: A sub-category or context for the secret (e.g., 'login', 'api_keys').
            username: The unique identifier or key for the secret.

        Returns:
            The secret string if found, otherwise None.

        Raises:
            SecretsError: If reading from the keyring backend fails.
        """
        try:
            return self.keyring.get_password(self._service_name(namespace, context), username)
        except self.KeyringError as exc:
            raise SecretsError("Failed to read secret from keyring backend.") from exc

    @inject_self
    def set(self, namespace: str, context: str, username: str, password: str) -> None:
        """
        Store a password/secret in the keyring.

        Args:
            namespace: The application or plugin identifier.
            context: A sub-category or context for the secret.
            username: The unique identifier or key for the secret.
            password: The plaintext secret value to store.

        Raises:
            ValueError: If the username is empty.
            SecretsError: If storing the secret in the keyring backend fails.
        """
        if not username:
            raise ValueError("Empty usernames are prohibited.")

        # https://github.com/jaraco/keyring/issues/545
        # Read existing secret in case writing fails after delete
        old_secret: str | None = None
        with suppress(Exception):
            old_secret = self.keyring.get_password(self._service_name(namespace, context), username)

        self.delete(namespace, context, username)

        try:
            self.keyring.set_password(self._service_name(namespace, context), username, password)
        except self.KeyringError as exc:
            if old_secret is not None:
                with suppress(Exception):
                    self.keyring.set_password(self._service_name(namespace, context), username, old_secret)
            raise SecretsError("Failed to store secret in keyring backend.") from exc

    @inject_self
    def delete(self, namespace: str, context: str, username: str) -> None:
        """
        Remove a password/secret from the keyring.

        Args:
            namespace: The application or plugin identifier.
            context: A sub-category or context for the secret.
            username: The unique identifier or key for the secret to delete.

        Raises:
            SecretsError: If deleting the secret from the keyring backend fails.
        """
        try:
            self.keyring.delete_password(self._service_name(namespace, context), username)
        except self.PasswordDeleteError:
            logger.debug("No secret exists for %s:%s", namespace, username)
        except self.KeyringError as exc:
            raise SecretsError("Failed to delete secret from keyring backend.") from exc

    @inject_self
    def get_credential(self, namespace: str, context: str, username: str | None = None) -> Credential | None:
        """
        Retrieve multiple credentials (username and password) from the keyring.

        Args:
            namespace: The application or plugin identifier.
            context: A sub-category or context for the secret.
            username: Optional filter for a specific username.

        Returns:
            A Credential object if found, otherwise None.
        """
        try:
            return self.keyring.get_credential(self._service_name(namespace, context), username)
        except self.KeyringError as exc:
            raise SecretsError("Failed to get credential from keyring backend.") from exc

    @inject_self
    def set_credential(self, namespace: str, context: str, username: str, password: str) -> None:
        """
        Store a credential in the keyring. Alias for `set`.
        """
        self.set(namespace, context, username, password)

    @inject_self
    def delete_credential(self, namespace: str, context: str, username: str) -> None:
        """
        Remove a credential from the keyring. Alias for `delete`.
        """
        self.delete(namespace, context, username)

    @inject_self
    def get_json(self, namespace: str, context: str, key: str) -> Any | None:
        """
        Retrieve a JSON-encoded secret and decode it.

        Args:
            namespace: The application or plugin identifier.
            context: A sub-category or context for the secret.
            key: The unique identifier for the secret.

        Returns:
            The decoded JSON data if found, otherwise None.

        Raises:
            SecretsError: If the retrieved secret is not valid JSON.
        """
        if (raw := self.get(namespace, context, key)) is None:
            return None

        try:
            return json.loads(raw)
        except Exception as exc:
            raise SecretsError(f"Secret for {namespace}:{key} is not valid JSON.") from exc

    @inject_self
    def set_json(self, namespace: str, context: str, key: str, value: Any) -> None:
        """
        Encode value as JSON and store it in the keyring.

        Args:
            namespace: The application or plugin identifier.
            context: A sub-category or context for the secret.
            key: The unique identifier for the secret.
            value: Any JSON-serializable data to store.
        """
        self.set(namespace, context, key, json.dumps(value, separators=(",", ":")))

    @inject_self
    def delete_json(self, namespace: str, context: str, key: str) -> None:
        """
        Remove a JSON-encoded secret from the keyring. Alias for `delete`.
        """
        self.delete(namespace, context, key)

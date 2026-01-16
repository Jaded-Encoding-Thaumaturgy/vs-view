from __future__ import annotations

from collections.abc import Callable
from logging import DEBUG, ERROR, FATAL, INFO, WARNING, getLogger

from vapoursynth import (
    DISABLE_LIBRARY_UNLOADING,
    ENABLE_GRAPH_INSPECTION,
    CoreCreationFlags,
    EnvironmentData,
    MessageType,
    VideoOutputTuple,
    has_policy,
)
from vapoursynth import clear_outputs as vs_clear_outputs
from vsengine.policy import ManagedEnvironment, Policy, ThreadLocalStore
from vsengine.vpy import Script

_policy: CustomPolicy | None = None
_logger = getLogger(__name__)


class CustomPolicy(Policy):
    def new_environment(self, *, set_logger: bool = True) -> ManagedEnvironment:
        _logger.debug("Creating new VS environment")

        data = self.api.create_environment(self.flags_creation)

        if set_logger:
            _logger.debug("Creating log handler for environment %r", data)
            self.api.set_logger(data, get_vs_log_handler(data))

        _logger.debug("Wrapping VapourSynth environment %r", data)
        env = self.api.wrap_environment(data)

        _logger.debug("Successfully created new environment %r", data)

        return ManagedEnvironment(env, data, self)


def get_policy(flags: CoreCreationFlags = ENABLE_GRAPH_INSPECTION | DISABLE_LIBRARY_UNLOADING) -> CustomPolicy:
    global _policy

    if _policy is None:
        if has_policy():
            raise RuntimeError("Policy already exists")

        _policy = CustomPolicy(ThreadLocalStore(), flags)
        _policy.register()

    return _policy


def unregister_policy() -> None:
    get_policy().unregister()

    global _policy
    _policy = None


def create_environment(*, set_logger: bool = True) -> ManagedEnvironment:
    env = get_policy().new_environment(set_logger=set_logger)
    env.core.timings.enabled = True
    return env


def unset_environment() -> None:
    """Unset the current environment in the global policy."""

    # Since we're not storing the previous environments when switching workspaces,
    # when creating a new environment, it would result in vsengine trying to restore
    # the old environment reference from the store, resolving to a dead object.
    # Setting the current environment to None here ensures to remove it from the store.
    get_policy().managed.set_environment(None)


def clear_environment(
    env: Script[ManagedEnvironment] | ManagedEnvironment,
    *,
    clear_caches: bool = False,
    clear_outputs: bool = False,
) -> None:
    _logger.debug("Clearing VS environment")

    unset_environment()

    menv = env.environment if isinstance(env, Script) else env

    if menv.disposed:
        _logger.debug("VS environment already disposed, skipping clear")
        env.dispose()
        return

    if not menv.vs_environment.alive:
        _logger.debug("VS environment not alive, skipping clear")
        return

    if clear_caches or clear_outputs:
        _logger.debug("Clearing VS environment caches")

    with menv.use():
        if clear_outputs:
            vs_clear_outputs()
        if clear_caches:
            for node in menv.outputs.values():
                if isinstance(node, VideoOutputTuple):
                    node.clip.clear_cache()
                else:
                    node.clear_cache()
        menv.core.clear_cache()

    env.dispose()

    _logger.debug("VS environment cleared and deleted")


vs_log_lvl_logging_map = {
    MessageType.MESSAGE_TYPE_DEBUG: DEBUG,
    MessageType.MESSAGE_TYPE_INFORMATION: INFO,
    MessageType.MESSAGE_TYPE_WARNING: WARNING,
    MessageType.MESSAGE_TYPE_CRITICAL: ERROR,
    MessageType.MESSAGE_TYPE_FATAL: FATAL,
}


def get_vs_log_handler(data: EnvironmentData) -> Callable[[int, str], None]:
    vslogger = getLogger("vapoursynth")

    def vs_log_handler(mt: int, msg: str) -> None:
        vslogger.log(DEBUG - 1, "vs logger called from %r", data, stacklevel=2)
        vslogger.log(
            vs_log_lvl_logging_map[MessageType(mt)],
            msg,
            exc_info=mt >= MessageType.MESSAGE_TYPE_CRITICAL,
            stacklevel=2,
        )

    return vs_log_handler

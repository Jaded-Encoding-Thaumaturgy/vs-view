"""Settings submodule for vsview."""

from .manager import SettingsManager
from .models import ActionID
from .shortcuts import ShortcutManager

__all__ = ["ActionID", "SettingsManager", "ShortcutManager"]

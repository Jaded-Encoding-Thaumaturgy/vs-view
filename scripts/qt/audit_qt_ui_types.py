import ast
import importlib
import pathlib

from PySide6.QtCharts import QAbstractAxis, QAbstractSeries
from PySide6.QtCore import QAbstractAnimation, QAbstractItemModel, QItemSelectionModel, QTimer
from PySide6.QtGui import QAction, QPixmap, QShortcut, QTextDocument, QWindow
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QButtonGroup,
    QCompleter,
    QGraphicsEffect,
    QGraphicsItem,
    QGraphicsScene,
    QLayout,
    QWidget,
)

ui_types = (
    QAbstractAxis,
    QAbstractSeries,
    QWebEnginePage,
    QWidget,
    QGraphicsItem,
    QGraphicsScene,
    QLayout,
    QAbstractItemModel,
    QItemSelectionModel,
    QAction,
    QShortcut,
    QTimer,
    QPixmap,
    QWindow,
    QAbstractAnimation,
    QAbstractItemDelegate,
    QGraphicsEffect,
    QTextDocument,
    QButtonGroup,
    QCompleter,
)
classes = set[type]()

for p in pathlib.Path("src").rglob("*.py"):
    tree = ast.parse(p.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("PySide6"):
            mod = importlib.import_module(node.module)

            for alias in node.names:
                if isinstance(obj := getattr(mod, alias.name, None), type):
                    classes.add(obj)

uncovered = {c for c in classes if not issubclass(c, ui_types)}
by_mod = dict[str, list[str]]()

for c in uncovered:
    by_mod.setdefault(c.__module__, []).append(c.__name__)

for mod, names in sorted(by_mod.items()):
    print(f"{mod}: {sorted(names)}")

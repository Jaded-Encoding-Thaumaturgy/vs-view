from __future__ import annotations

import base64
import threading
from dataclasses import dataclass
from functools import partial
from logging import getLogger
from pathlib import Path
from typing import Any, assert_never, override

import vsengine
import vsgraph
from jetpytools import fallback
from PySide6.QtCore import QUrl, QUrlQuery, Slot
from PySide6.QtGui import QCursor, QImage, QShowEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QVBoxLayout, QWidget

from vsview.api import PluginAPI, VideoOutputProxy, WidgetPluginBase, run_in_background, run_in_loop
from vsview.app.settings import ShortcutManager
from vsview.env import getenv_bool

from .analysis import enrich_cytoscape_payload
from .bridge import ExportFormat, JsToPyCommand, NodeCopyTarget, PyToJsCommand
from .call import build_filter_call, build_node_report
from .constants import PLUGIN_DISPLAY_NAME, PLUGIN_IDENTIFIER
from .settings import GraphSettings, GraphShortcut
from .theme import extract_theme_tokens
from .web import GraphWebView

logger = getLogger(__name__)


@dataclass(slots=True)
class CachedGraph:
    graph: vsgraph.Graph[Any]
    payload: dict[str, Any]


class GraphPlugin(WidgetPluginBase[GraphSettings, None]):
    WEB_DIST_DIR = Path(__file__).parent / "web_dist"
    INDEX_PATH = WEB_DIST_DIR / "index.html"

    identifier = PLUGIN_IDENTIFIER
    display_name = PLUGIN_DISPLAY_NAME

    shortcuts = tuple(s.definition for s in GraphShortcut)

    def __init__(self, parent: QWidget, api: PluginAPI) -> None:
        super().__init__(parent, api)

        ShortcutManager.register_definitions(self.shortcuts)

        self._selected_idx: int | None = None
        self._is_dirty = False
        self._selected_node_id: str | None = None

        self._graph_cache = dict[int, CachedGraph]()
        self._sent_key: int | None = None
        self._last_sent_timings = dict[str, int]()
        self._load_lock = threading.Lock()

        self._load_ready = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = GraphWebView(self)
        layout.addWidget(self.view)
        # Connect Bridge signals
        self.view.bridge.webReady.connect(self._on_web_ready)
        self.view.bridge.commandReceived.connect(self._on_command_received)

        self.context_menu = QMenu(self)
        self.fit_act = self.context_menu.addAction("Fit to View")
        self.fit_act.triggered.connect(self._on_fit_view_triggered)
        self.reset_act = self.context_menu.addAction("Reset View")
        self.reset_act.triggered.connect(self._on_reset_view_triggered)
        self.reload_act = self.context_menu.addAction("Reload Graph")
        self.reload_act.triggered.connect(self._on_reload_graph_triggered)

        self.context_menu.addSeparator()

        # Copy Submenu
        copy_menu = self.context_menu.addMenu("Copy Graph as")
        copy_png_act = copy_menu.addAction("PNG Image")
        copy_png_act.triggered.connect(self._on_copy_png_triggered)
        copy_menu.addSeparator()

        for fmt in ExportFormat:
            act = copy_menu.addAction(fmt.label)
            act.triggered.connect(partial(self._export_graph, fmt, clipboard=True))

        # Save Submenu
        save_menu = self.context_menu.addMenu("Save Graph as")
        save_png_act = save_menu.addAction("PNG Image…")
        save_png_act.triggered.connect(self._on_save_png_image)
        save_menu.addSeparator()

        for fmt in ExportFormat:
            act = save_menu.addAction(f"{fmt.label}…")
            act.triggered.connect(partial(self._export_graph, fmt, clipboard=False))

        self.api.globalSettingsChanged.connect(self._sync_web_options)
        self.api.globalSettingsChanged.connect(self._sync_keybindings)
        self.api.register_on_destroy(self.on_destroy)

        self.api.register_action(GraphShortcut.FIT.definition, self.fit_act)
        self.api.register_action(GraphShortcut.RESET_VIEW.definition, self.reset_act)
        self.api.register_action(GraphShortcut.RELOAD.definition, self.reload_act)

    @property
    def default_export_dir(self) -> Path:
        return path.parent if (path := self.api.file_path) else Path.home()

    @property
    def default_export_stem(self) -> str:
        return path.stem if (path := self.api.file_path) else "dependency-graph"

    @property
    def selected_output_index(self) -> int | None:
        """The currently selected video output index, or None if following the active output."""
        return self._selected_idx

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._is_dirty or (self._sent_key is None and self.view.bridge.is_ready):
            self.view.sync_theme()
            self.update_outputs()
            self.update_graph(force_layout=True)

    @override
    def deleteLater(self) -> None:
        self.view.deleteLater()
        super().deleteLater()

    @override
    def on_current_voutput_changed(self, voutput: VideoOutputProxy, tab_index: int) -> None:
        if not self._load_ready:
            self._load_page()
            self._load_ready = True

        self.update_outputs()
        if self._selected_idx is None:
            self.update_graph(force_layout=False)

    def on_destroy(self) -> None:
        self._selected_idx = None
        self._graph_cache.clear()
        self._sent_key = None
        self._last_sent_timings.clear()
        self._is_dirty = False
        self._selected_node_id = None

    def select_output(self, index: int | None) -> None:
        self._selected_idx = index if (index is not None and index >= 0) else None
        self.update_outputs()
        self.update_graph(force_layout=True)

    def refresh(self) -> None:
        self.update_outputs()
        self.update_graph(refresh=True)

    @run_in_loop(return_future=False)
    def update_outputs(self) -> None:
        if not self.view.bridge.is_ready:
            return

        voutputs = self.api.voutputs
        current_vs_index = self.api.current_voutput.vs_index
        outputs = list[dict[str, Any]]()

        for v in voutputs:
            # fmt:off
            is_active = (
                (self._selected_idx == v.vs_index)
                or
                (self._selected_idx is None and v.vs_index == current_vs_index)
            )
            # fmt:on
            outputs.append({"index": v.vs_index, "name": v.vs_name, "isActive": is_active})

        self.view.bridge.dispatch(PyToJsCommand.SET_OUTPUTS, {"outputs": outputs})

    @run_in_background(name="UpdateGraph")
    def update_graph(self, *, force_layout: bool = False, refresh: bool = False) -> None:
        if not self.view.bridge.is_ready or self.api.is_playing:
            return

        if not force_layout and not vsengine.from_thread(self.isVisible).result():
            self._is_dirty = True
            return

        self._is_dirty = False

        if (cached := self._load_graph(refresh=refresh)) is None:
            return

        key = fallback(self._selected_idx, self.api.current_voutput.vs_index)
        node_ids = {n.id for n in cached.graph.nodes}

        must_send_data = force_layout or key != self._sent_key

        timings = list[dict[str, Any]]()
        timings_map = dict[str, int]()
        for n in cached.graph.nodes:
            timings.append(
                {"id": n.id, "processing_time_ns": n.processing_time_ns, "processing_time_ms": n.processing_time_ms}
            )
            timings_map[n.id] = n.processing_time_ns

        if must_send_data:
            self._sent_key = key

            # Drop a selection that no longer exists in the new topology
            if self._selected_node_id is not None and self._selected_node_id not in node_ids:
                self._selected_node_id = None

            self._last_sent_timings = timings_map
            self.view.bridge.dispatch(PyToJsCommand.SET_DATA, cached.payload)

            # setData rebuilds the frontend elements, so restore the selection explicitly
            if self._selected_node_id is not None:
                self.view.bridge.dispatch(PyToJsCommand.SELECT_NODE, {"id": self._selected_node_id})
            return

        timings_changed = timings_map != self._last_sent_timings

        # An explicit refresh always echoes the timings even when the values themselves did not change.
        if not timings_changed and not refresh:
            return

        self._last_sent_timings = timings_map
        self.view.bridge.dispatch(PyToJsCommand.UPDATE_TIMINGS, {"timings": timings})

    @Slot()
    def _on_web_ready(self) -> None:
        self._sent_key = None
        self._last_sent_timings.clear()
        self._sync_web_options()
        self._sync_keybindings()
        self.view.sync_theme()
        self.update_outputs()
        self.update_graph(force_layout=True)

    @Slot(str, dict)
    def _on_command_received(self, command: JsToPyCommand, payload: dict[str, Any]) -> None:
        match command:
            case JsToPyCommand.READY:
                self._on_web_ready()
            case JsToPyCommand.RELOAD:
                self.update_outputs()
                self.update_graph(refresh=True)
            case JsToPyCommand.OUTPUT_SELECT:
                self.select_output(payload.get("index"))
            case JsToPyCommand.NODE_SELECT:
                self._selected_node_id = payload["id"]
            case JsToPyCommand.NODE_DESELECT:
                self._selected_node_id = None
            case JsToPyCommand.NODE_COPY:
                self._copy_node_info(str(payload["id"]), NodeCopyTarget(payload["target"]))
            case JsToPyCommand.CONTEXT_MENU:
                self.context_menu.exec(QCursor.pos())
            case JsToPyCommand.EXPORT_GRAPH:
                self._export_graph(ExportFormat(payload["format"]), clipboard=payload["clipboard"])
            case JsToPyCommand.SAVE_IMAGE:
                destination = payload.get("destination")
                if destination == "clipboard":
                    self._copy_image(payload["dataBase64"])
                elif destination == "file" and (path := payload.get("path")):
                    self._save_image(path, payload["dataBase64"])
                else:
                    logger.error("Malformed Payload")
            case _:
                assert_never(command)

    @Slot()
    def _on_fit_view_triggered(self) -> None:
        self.view.bridge.dispatch(PyToJsCommand.FIT_VIEW)

    @Slot()
    def _on_reset_view_triggered(self) -> None:
        self.view.bridge.dispatch(PyToJsCommand.RESET_VIEW)

    @Slot()
    def _on_reload_graph_triggered(self) -> None:
        self.update_outputs()
        self.update_graph(refresh=True)

    @Slot()
    def _on_copy_png_triggered(self) -> None:
        self.view.bridge.dispatch(PyToJsCommand.REQUEST_PNG_EXPORT, {"destination": "clipboard"})

    @Slot()
    def _on_save_png_image(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Graph Image",
            str(self.default_export_dir / f"{self.default_export_stem}-graph.png"),
            "PNG image (*.png)",
        )
        if not selected:
            return

        self.view.bridge.dispatch(PyToJsCommand.REQUEST_PNG_EXPORT, {"destination": "file", "path": selected})

    @Slot()
    def _sync_web_options(self) -> None:
        if not self.view.bridge.is_ready:
            return

        settings = self.settings.global_
        self.view.bridge.dispatch(
            PyToJsCommand.SET_OPTIONS,
            {
                "direction": settings.direction,
                "layout": settings.layout,
                "rankSep": settings.rank_sep,
                "nodeSep": settings.node_sep,
                "heatmap": settings.heatmap_enabled,
                "showTimings": settings.show_timings,
                "timingUnit": settings.timing_unit,
                "autoFitOnReload": settings.auto_fit_on_reload,
            },
        )

    @Slot()
    def _sync_keybindings(self) -> None:
        if not self.view.bridge.is_ready:
            return

        rules = [
            rule
            for shortcut in GraphShortcut
            if (rule := shortcut.to_rule(self.api.get_shortcut_key_sequence(shortcut.definition))) is not None
        ]
        self.view.bridge.dispatch(PyToJsCommand.SET_KEYBINDINGS, {"keybindings": rules})

    @run_in_loop(return_future=False)
    def _load_page(self) -> None:
        url = QUrl.fromLocalFile(GraphPlugin.INDEX_PATH)
        tokens = extract_theme_tokens(self.palette())
        query = QUrlQuery()
        query.addQueryItem("windowColorRole", tokens["bg"])
        query.addQueryItem("windowTextRole", tokens["textPrimary"])
        query.addQueryItem("accentColor", tokens["accent"])

        url = (
            QUrl("http://localhost:5174")
            if getenv_bool("VSVIEW_GRAPH_DEV")
            else QUrl.fromLocalFile(GraphPlugin.INDEX_PATH)
        )
        url.setQuery(query)
        self.view.load(url)

    @run_in_background(name="CopyNodeInfo")
    def _copy_node_info(self, node_id: str, target: NodeCopyTarget) -> None:
        if target == NodeCopyTarget.ID:
            vsengine.from_thread(lambda: QApplication.clipboard().setText(node_id))
            self.api.statusMessage.emit(f"Copied {target.description}")
            return

        if (cached := self._load_graph()) is None:
            return

        if (node := cached.graph.nodes_by_id.get(node_id)) is None:
            logger.warning("Cannot copy %s: node '%s' not found in current graph.", target.description, node_id)
            return

        with self.api.vs_context():
            match target:
                case NodeCopyTarget.CALL:
                    text = build_filter_call(node)
                case NodeCopyTarget.REPORT:
                    text = build_node_report(node, is_root=node.id == cached.graph.root_id)
                case _:
                    assert_never(target)

        vsengine.from_thread(lambda: QApplication.clipboard().setText(text))
        self.api.statusMessage.emit(f"Copied {target.description}")

    @run_in_background(name="ExportGraph")
    def _export_graph(self, fmt: ExportFormat, clipboard: bool) -> None:
        if (cached := self._load_graph()) is None:
            return

        with self.api.vs_context():
            match fmt:
                case ExportFormat.DOT:
                    text = cached.graph.to_dot(direction=self.settings.global_.direction)
                case ExportFormat.MERMAID:
                    text = cached.graph.to_mermaid()
                case ExportFormat.ASCII:
                    text = cached.graph.to_ascii()
                case ExportFormat.JSON:
                    text = cached.graph.to_json(indent=2)
                case _:
                    assert_never(fmt)

        if clipboard:
            vsengine.from_thread(lambda: QApplication.clipboard().setText(text))
            self.api.statusMessage.emit(f"Copied dependency graph as {fmt.upper()}")
            return

        selected, _ = vsengine.from_thread(
            QFileDialog.getSaveFileName,
            self,
            "Export Dependency Graph",
            str(self.default_export_dir / f"{self.default_export_stem}-graph{fmt.suffix}"),
            fmt.file_filter,
        ).result()

        if not selected:
            return

        Path(selected).write_text(text, encoding="utf-8")
        self.api.statusMessage.emit(f"Saved dependency graph to {selected}")

    def _load_graph(self, *, refresh: bool = False) -> CachedGraph | None:
        with self._load_lock:
            key = fallback(self._selected_idx, self.api.current_voutput.vs_index)

            if refresh:
                self._graph_cache.pop(key, None)

            if (cached := self._graph_cache.get(key)) is not None:
                return cached

            clip = next(
                (v.vs_output.clip for v in self.api.voutputs if v.vs_index == self._selected_idx),
                self.api.current_voutput.vs_output.clip,
            )

            with self.api.vs_context():
                try:
                    graph = vsgraph.get_graph(clip)
                    cached = CachedGraph(graph=graph, payload=enrich_cytoscape_payload(graph, graph.to_cytoscape()))
                except Exception as e:
                    logger.error("Failed to extract graph from clip: %s", e)
                    logger.debug("", exc_info=e)
                    return None

            self._graph_cache[key] = cached
            return cached

    @run_in_background(name="CopyImage")
    def _copy_image(self, data_base64: str) -> None:
        encoded = data_base64.split(",", 1)[-1].strip()
        image = QImage()
        image.loadFromData(base64.b64decode(encoded, validate=True), "PNG")
        vsengine.from_thread(lambda: QApplication.clipboard().setImage(image))
        self.api.statusMessage.emit("Copied graph image to clipboard")
        logger.info("Copied graph image to clipboard")

    @run_in_background(name="SaveImage")
    def _save_image(self, path: str, data_base64: str) -> None:
        encoded = data_base64.split(",", 1)[-1].strip()
        Path(path).write_bytes(base64.b64decode(encoded, validate=True))
        self.api.statusMessage.emit(f"Saved graph image to {path}")
        logger.info("Saved graph image to %s", path)

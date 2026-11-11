import { Disposable } from 'vsview-shared';

import { BridgeService } from './bridge/python';
import { CytoscapeCanvas } from './canvas/cytoscape';
import { HudController } from './ui/hud';
import { InspectorDrawer } from './ui/inspector';
import { KeybindingController } from './ui/keybindings';
import { OverviewPanel } from './ui/overview';
import { ThemeManager } from './utils/theme';
import { NeighborNavigator, findDeepestLeafNode } from './utils/traversal';

export interface GraphManagerOptions {
  cyContainer: HTMLElement;
  hudContainer: HTMLElement;
  inspectorContainer: HTMLElement;
  overviewContainer: HTMLElement;
  loadingOverlay: HTMLElement;
  errorBanner: HTMLElement;
}

export class GraphManager extends Disposable {
  private readonly bridge: BridgeService;
  private readonly canvas: CytoscapeCanvas;
  private readonly hud: HudController;
  private readonly inspector: InspectorDrawer;
  private readonly overview: OverviewPanel;
  private readonly themeManager: ThemeManager;
  private readonly keybindings: KeybindingController;

  private readonly loadingOverlay: HTMLElement;
  private readonly errorBanner: HTMLElement;

  private readonly navigator = new NeighborNavigator();

  constructor(options: GraphManagerOptions) {
    super();

    this.loadingOverlay = options.loadingOverlay;
    this.errorBanner = options.errorBanner;

    this.themeManager = this._disposables.add(new ThemeManager());
    this.bridge = this._disposables.add(new BridgeService());
    this.canvas = this._disposables.add(new CytoscapeCanvas());

    this.inspector = this._disposables.add(
      new InspectorDrawer({
        container: options.inspectorContainer,
        onCopyNode: (id, target, frameIdx) =>
          this.bridge.sendEvent(
            'node.copy',
            frameIdx === undefined ? { id, target } : { id, target, frameIdx },
          ),
        resolveNode: (id) => this.canvas.getNodeById(id),
        resolveEdge: (id) => this.canvas.getEdgeById(id),
        getIncomingConnections: (id) => this.canvas.getIncomingConnections(id),
        onFocusNode: (id) => this.focusNode(id),
      }),
    );

    this.overview = this._disposables.add(
      new OverviewPanel({
        container: options.overviewContainer,
        canvas: this.canvas,
        onFocusNode: (nodeId) => this.focusNode(nodeId),
        onPatternsChanged: (enabled) => this.syncHudOptions(enabled),
      }),
    );

    this.keybindings = this._disposables.add(
      new KeybindingController({
        navigateUp: (cycle) => this.navigate('up', cycle),
        navigateDown: (cycle) => this.navigate('down', cycle),
        selectRoot: () => this.selectRoot(),
        selectDeepestLeaf: () => this.selectDeepestLeaf(),
        centerSelected: () => {
          const id = this.canvas.selectedId;
          if (id) this.canvas.centerNode(id);
        },
        escape: () => this.handleEscape(),
        resetView: () => this.canvas.reset(),
        fit: () => this.canvas.fit(),
        toggleHeatmap: () => this.toggleHeatmap(),
        togglePatterns: () => this.togglePatterns(),
        toggleOverview: () => this.overview.toggle(),
        reload: () => {
          this.bridge.sendEvent('graph.reload');
        },
        focusSearch: () => this.hud.focusSearch(),
      }),
    );

    this.canvas.init(options.cyContainer);

    this._disposables.add(
      this.canvas.onBackgroundContextMenu(() =>
        this.bridge.sendEvent('contextmenu.request', { direction: this.canvas.direction }),
      ),
    );

    this.hud = this._disposables.add(
      new HudController({
        container: options.hudContainer,
        canvas: this.canvas,
        onReloadRequested: () => this.bridge.sendEvent('graph.reload'),
        onOutputSelected: (index) => this.bridge.sendEvent('output.select', { index }),
        onNodeMatched: (nodeId) => this.focusNode(nodeId),
        onPatternsToggled: (enabled) => this.overview.setPatterns(enabled),
        onOverviewRequested: () => this.overview.toggle(),
      }),
    );

    this._disposables.add(
      this.canvas.onEdgeSelected((edge, source, target) => {
        this.inspector.inspectEdge(edge, source, target);
      }),
    );

    this.setupSubscriptions();
  }

  public async start(): Promise<void> {
    (await this.bridge.initBridge()).match({
      ok: () => {
        console.debug('Bridge initialized successfully');
      },
      err: (err) => {
        console.warn('Bridge initialization failed (standalone browser mode?):', err);
        this.hideLoadingOverlay();
      },
    });
  }

  private setupSubscriptions(): void {
    this._disposables.add(
      this.themeManager.onThemeChanged((tokens) => {
        this.canvas.applyTheme(tokens);
      }),
    );

    this._disposables.add(
      this.canvas.onNodeSelected((node) => {
        this.inspector.inspectNode(node);
        this.bridge.sendEvent('node.select', { id: node.id });
      }),
    );

    this._disposables.add(
      this.canvas.onSelectionCleared(() => {
        this.inspector.close();
        this.bridge.sendEvent('node.deselect');
      }),
    );

    this._disposables.add(
      this.bridge.onCommand('graph.setOptions', (payload) => {
        this.canvas.applyOptions(payload);
        this.inspector.setTimingOptions(
          this.canvas.isShowingTimings(),
          this.canvas.getTimingUnit(),
        );
        this.syncHudOptions();
      }),
    );

    this._disposables.add(
      this.bridge.onCommand('graph.setData', (payload) => {
        this.hideErrorBanner();

        this.navigator.reset();
        this.canvas.setGraphData(payload, true);
        this.inspector.refresh();

        this.overview.setData(this.canvas.getNodes(), payload.analysis);
        this.hud.refreshSearch(false);

        this.hideLoadingOverlay();
      }),
    );

    this._disposables.add(
      this.bridge.onCommand('graph.updateTimings', (payload) => {
        this.hideErrorBanner();
        this.canvas.updateTimings(payload.timings);
        this.overview.updateTimings(this.canvas.getNodes());
        this.inspector.refresh();
      }),
    );

    this._disposables.add(
      this.bridge.onCommand('graph.setOutputs', (payload) =>
        this.hud.setOutputs(payload.outputs, payload.following),
      ),
    );

    this._disposables.add(
      this.bridge.onCommand('graph.selectNode', (payload) => {
        if (this.canvas.selectedId === payload.id) return;

        const node = this.canvas.selectNodeById(payload.id);
        if (!node) {
          console.warn(`Cannot select node '${payload.id}': node not found in graph`);
          return;
        }

        this.inspector.inspectNode(node);
      }),
    );

    this._disposables.add(
      this.bridge.onCommand('graph.setError', (payload) => {
        this.showErrorBanner(payload.message);
        this.hideLoadingOverlay();
      }),
    );

    this._disposables.add(
      this.bridge.onCommand('theme.update', (tokens) => this.themeManager.updateTheme(tokens)),
    );

    this._disposables.add(
      this.bridge.onCommand(
        'graph.setKeybindings',
        (payload) => (this.keybindings.keybindings = payload.keybindings),
      ),
    );

    this._disposables.add(this.bridge.onCommand('graph.fitView', () => this.canvas.fit()));

    this._disposables.add(this.bridge.onCommand('graph.resetView', () => this.canvas.reset()));

    this._disposables.add(
      this.bridge.onCommand('graph.requestPngExport', (payload) => {
        this.canvas.exportPng().match({
          ok: (data) => {
            this.bridge.sendEvent('graph.saveImage', {
              format: 'png',
              dataBase64: data,
              destination: payload.destination,
              ...(payload.destination === 'file' ? { path: payload.path } : {}),
            });
          },
          err: (err) => console.error('Failed to export graph as PNG:', err),
        });
      }),
    );
  }

  private syncHudOptions(patterns?: boolean): void {
    this.hud.syncOptions({
      direction: this.canvas.direction,
      heatmap: this.canvas.heatmapActive,
      patterns: patterns ?? this.canvas.isShowingPatternStyles(),
    });
  }

  private focusNode(nodeId: string): void {
    const node = this.canvas.selectNodeById(nodeId);
    if (!node) {
      console.warn(`Cannot focus node '${nodeId}': node not found in graph`);
      return;
    }

    this.canvas.centerNode(nodeId);
    this.inspector.inspectNode(node);
    this.bridge.sendEvent('node.select', { id: nodeId });
  }

  private clearSelection(): void {
    this.canvas.clearSelection();
    this.inspector.close();
    this.bridge.sendEvent('node.deselect');
  }

  private handleEscape(): void {
    if (this.overview.opened) {
      this.overview.close();
    } else if (this.inspector.opened) {
      this.inspector.close();
    } else {
      this.clearSelection();
    }
  }

  private navigate(direction: 'up' | 'down', cycle: boolean): void {
    const target = this.navigator.next(
      this.canvas.selectedId,
      direction,
      cycle,
      this.canvas.getNodes(),
      this.canvas.getEdges(),
    );
    if (target) this.focusNode(target);
  }

  private selectRoot(): void {
    const root_id = this.canvas.getNodes().find((node) => node.is_root)?.id;
    if (root_id) this.focusNode(root_id);
  }

  private selectDeepestLeaf(): void {
    const deepest = findDeepestLeafNode(this.canvas.getNodes(), this.canvas.getEdges());
    if (deepest) this.focusNode(deepest.id);
  }

  private toggleHeatmap(): void {
    this.canvas.heatmapActive = !this.canvas.heatmapActive;
    this.syncHudOptions();
  }

  private togglePatterns(): void {
    const next = !this.canvas.isShowingPatternStyles();
    this.canvas.setPatternStyles(next);
    this.overview.setPatterns(next);
    this.syncHudOptions(next);
  }

  private hideLoadingOverlay(): void {
    if (!this.loadingOverlay.isConnected || this.loadingOverlay.classList.contains('fade-out'))
      return;

    this.loadingOverlay.classList.add('fade-out');
    this.loadingOverlay.addEventListener('transitionend', () => this.loadingOverlay.remove(), {
      once: true,
    });
  }

  private showErrorBanner(message: string): void {
    this.errorBanner.textContent = message;
    this.errorBanner.classList.add('visible');
  }

  private hideErrorBanner(): void {
    this.errorBanner.classList.remove('visible');
    this.errorBanner.textContent = '';
  }
}

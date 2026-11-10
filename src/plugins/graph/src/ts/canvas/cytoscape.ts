import cytoscape from 'cytoscape';
import dagre, { type DagreLayoutOptions } from 'cytoscape-dagre';
import { type IDisposable, Result, toDisposable } from 'vsview-shared';

import type {
  CanvasNodeData,
  CytoscapePayload,
  EdgeData,
  FilterModeName,
  IncomingConnection,
  LayoutDirection,
  LayoutKind,
  NodeTimingsUpdate,
  TimingUnit,
} from '../types';
import { formatTiming } from '../utils/format';
import type { ThemeTokens } from '../utils/theme';
import { calculateHeatmapRatio, interpolateHeatmapColor } from './heatmap';
import { VisualState } from './visual-state';

cytoscape.use(dagre);

const LABEL_FONT = 'normal bold 14px system-ui, -apple-system, sans-serif';
const LABEL_FONT_SIZE = 14;
const FALLBACK_CHAR_WIDTH = 8;

export class CytoscapeCanvas implements IDisposable {
  private cy: cytoscape.Core | null = null;
  private currentDirection: LayoutDirection = 'TB';
  private _heatmapActive = false;
  private _autoFitOnReload = true;
  private showTimings = true;
  private timingUnit: TimingUnit = 'auto';
  private selectedNodeId: string | null = null;
  private themeTokens: ThemeTokens | null = null;
  private lineMeasurer: ((line: string) => number) | null = null;
  private readonly lineMeasurerCache = new Map<string, number>();

  private readonly visualState = new VisualState();
  private currentLayout: LayoutKind = 'dagre';
  private rankSeparation = 60;
  private nodeSeparation = 40;
  private showPatternStyles = false;

  private onNodeSelectedCallbacks: Array<(node: CanvasNodeData) => void> = [];
  private onSelectionClearedCallbacks: Array<() => void> = [];
  private onEdgeSelectedCallbacks: Array<
    (edge: EdgeData, source: CanvasNodeData, target: CanvasNodeData) => void
  > = [];
  private onBackgroundContextMenuCallbacks: Array<(pos: { x: number; y: number }) => void> = [];
  private onViewportChangedCallbacks: Array<() => void> = [];
  private onCanvasTappedCallbacks: Array<() => void> = [];
  private boundPreventContextMenu: ((event: MouseEvent) => void) | null = null;
  private containerElement: HTMLElement | null = null;

  public get direction(): LayoutDirection {
    return this.currentDirection;
  }

  public set direction(direction: LayoutDirection) {
    if (this.currentDirection === direction) return;
    this.currentDirection = direction;
    this.runLayout();
  }

  public get layout(): LayoutKind {
    return this.currentLayout;
  }

  public set layout(layout: LayoutKind) {
    if (this.currentLayout === layout) return;
    this.currentLayout = layout;
    this.runLayout();
  }

  public get rankSep(): number {
    return this.rankSeparation;
  }

  public set rankSep(value: number) {
    if (this.rankSeparation === value) return;
    this.rankSeparation = value;
    this.runLayout();
  }

  public get nodeSep(): number {
    return this.nodeSeparation;
  }

  public set nodeSep(value: number) {
    if (this.nodeSeparation === value) return;
    this.nodeSeparation = value;
    this.runLayout();
  }

  public get autoFitOnReload(): boolean {
    return this._autoFitOnReload;
  }

  public set autoFitOnReload(enabled: boolean) {
    this._autoFitOnReload = enabled;
  }

  public get heatmapActive(): boolean {
    return this._heatmapActive;
  }

  public set heatmapActive(active: boolean) {
    this._heatmapActive = active;
    if (!this.cy) return;

    if (!active) {
      this.cy.nodes().removeStyle('background-color');
    }
    this.applyVisualState();
  }

  public get selectedId(): string | null {
    return this.selectedNodeId;
  }

  public get activeGroupHighlight(): ReadonlySet<string> | null {
    return this.visualState.activeGroupHighlight;
  }

  public init(container: HTMLElement): void {
    this.containerElement = container;
    this.boundPreventContextMenu = (event) => event.preventDefault();
    container.addEventListener('contextmenu', this.boundPreventContextMenu);

    this.cy = cytoscape({
      container,
      boxSelectionEnabled: false,
      autounselectify: false,
      style: [],
    });

    this.setupEventHandlers();
  }

  public applyTheme(tokens: ThemeTokens): void {
    this.themeTokens = tokens;
    if (!this.cy) return;

    this.cy.style(this.buildStylesheet(tokens)).update();

    if (this.heatmapActive) {
      this.applyHeatmap();
    }
  }

  public onNodeSelected(cb: (node: CanvasNodeData) => void): IDisposable {
    this.onNodeSelectedCallbacks.push(cb);
    return toDisposable(() => {
      const idx = this.onNodeSelectedCallbacks.indexOf(cb);
      if (idx !== -1) this.onNodeSelectedCallbacks.splice(idx, 1);
    });
  }

  public onSelectionCleared(cb: () => void): IDisposable {
    this.onSelectionClearedCallbacks.push(cb);
    return toDisposable(() => {
      const idx = this.onSelectionClearedCallbacks.indexOf(cb);
      if (idx !== -1) this.onSelectionClearedCallbacks.splice(idx, 1);
    });
  }

  public setPatternStyles(enabled: boolean): void {
    if (this.showPatternStyles === enabled) return;

    this.showPatternStyles = enabled;
    this.cy?.edges().toggleClass('pattern-labelled', enabled);
  }

  public isShowingPatternStyles(): boolean {
    return this.showPatternStyles;
  }

  public onEdgeSelected(
    cb: (edge: EdgeData, source: CanvasNodeData, target: CanvasNodeData) => void,
  ): IDisposable {
    this.onEdgeSelectedCallbacks.push(cb);
    return toDisposable(() => {
      const idx = this.onEdgeSelectedCallbacks.indexOf(cb);
      if (idx !== -1) this.onEdgeSelectedCallbacks.splice(idx, 1);
    });
  }

  public onBackgroundContextMenu(cb: (pos: { x: number; y: number }) => void): IDisposable {
    this.onBackgroundContextMenuCallbacks.push(cb);
    return toDisposable(() => {
      const idx = this.onBackgroundContextMenuCallbacks.indexOf(cb);
      if (idx !== -1) this.onBackgroundContextMenuCallbacks.splice(idx, 1);
    });
  }

  public onViewportChanged(cb: () => void): IDisposable {
    this.onViewportChangedCallbacks.push(cb);
    return toDisposable(() => {
      const idx = this.onViewportChangedCallbacks.indexOf(cb);
      if (idx !== -1) this.onViewportChangedCallbacks.splice(idx, 1);
    });
  }

  public onCanvasTapped(cb: () => void): IDisposable {
    this.onCanvasTappedCallbacks.push(cb);
    return toDisposable(() => {
      const idx = this.onCanvasTappedCallbacks.indexOf(cb);
      if (idx !== -1) this.onCanvasTappedCallbacks.splice(idx, 1);
    });
  }

  /** Restrict the visible graph to one filter mode, or `null` to show every mode. */
  public setModeFilter(mode: FilterModeName | null): void {
    this.visualState.setModeFilter(mode);

    if (this.cy) {
      this.applyVisualState();
    }
  }

  /** Dim nodes whose media info is unchanged from their parent. */
  public setMediaOnly(enabled: boolean): void {
    this.visualState.setMediaOnly(enabled);

    if (this.cy) {
      this.applyVisualState();
    }
  }

  /** Highlight a specific set of nodes (e.g. from an overview category), dimming all others. */
  public highlightNodeGroup(nodeIds: readonly string[] | null): void {
    this.visualState.setGroupHighlight(nodeIds);

    if (this.cy) {
      this.applyVisualState();
    }
  }

  public getEdges(): EdgeData[] {
    if (!this.cy) return [];
    return this.cy.edges().map((edge) => edge.data() as EdgeData);
  }

  /** Render the whole graph to a PNG data URL, used by the export command. */
  public exportPng(): Result<string> {
    const cy = this.cy;

    if (!cy) {
      return Result.err(new Error('Cannot export PNG: Cytoscape canvas is not initialized'));
    }

    return Result.fromThrowable(() =>
      cy.png({
        full: true,
        scale: 2,
        maxWidth: 8192,
        maxHeight: 8192,
        bg: this.themeTokens?.bg ?? '#000000',
      }),
    ).andThen((png) =>
      png ? Result.ok(png) : Result.err(new Error('Cytoscape returned an empty PNG data URL')),
    );
  }

  public setGraphData(payload: CytoscapePayload, preserveViewport = false): void {
    if (!this.cy) return;

    this.lineMeasurerCache.clear();

    const hadPriorElements = this.cy.elements().length > 0;
    const shouldPreserve = preserveViewport && hadPriorElements && !this.autoFitOnReload;
    const previousSelectedId = this.selectedNodeId;
    const previousPan = shouldPreserve ? this.cy.pan() : null;
    const previousZoom = shouldPreserve ? this.cy.zoom() : null;

    this.selectedNodeId = null;
    this.visualState.clearLineage();

    this.cy.stop();
    this.cy.elements().remove();

    const elements: cytoscape.ElementDefinition[] = [];

    const mediaTargets = new Set(
      payload.edges.filter((e) => Boolean(e.data.media_label)).map((e) => e.data.target),
    );

    for (const n of payload.nodes) {
      const data: CanvasNodeData = {
        ...n.data,
        source_label: n.data.label,
        ...this.buildLabelData(n.data.label, n.data.processing_time_ns),
      };

      const modeClass = `mode-${n.data.filter_mode_name.toLowerCase().replace(/_/g, '-')}`;
      const classes = [modeClass, mediaTargets.has(n.data.id) ? 'media-change-node' : '']
        .filter(Boolean)
        .join(' ');

      elements.push({
        group: 'nodes',
        data,
        classes,
      });
    }

    for (const e of payload.edges) {
      const data: EdgeData = { ...e.data };

      const classes = [
        `pattern-${e.data.pattern_name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`,
        e.data.media_label ? 'media-changed' : '',
        this.showPatternStyles ? 'pattern-labelled' : '',
      ]
        .filter(Boolean)
        .join(' ');

      elements.push({
        group: 'edges',
        data,
        classes,
      });
    }

    this.cy.add(elements);
    this.runLayout();

    if (preserveViewport && previousPan && previousZoom) {
      this.cy.zoom(previousZoom);
      this.cy.pan(previousPan);
    }

    if (this.heatmapActive) {
      this.applyHeatmap();
    }

    // Restore node selection if the previously selected node is still present
    if (previousSelectedId) {
      const restoredNode = this.selectNodeById(previousSelectedId);
      if (restoredNode) {
        this.notifyNodeSelected(restoredNode);
      } else {
        this.notifySelectionCleared();
      }
    }
  }

  public updateTimings(timings: NodeTimingsUpdate[]): void {
    if (!this.cy) return;

    this.cy.batch(() => {
      for (const item of timings) {
        const node = this.cy!.getElementById(item.id);
        if (node.length > 0) {
          node.data('processing_time_ns', item.processing_time_ns);
          node.data('processing_time_ms', item.processing_time_ms);

          // Node labels carry the timing line, so they are rebuilt along with their measured size
          const data = node.data() as CanvasNodeData;
          node.data(this.buildLabelData(data.source_label, item.processing_time_ns));
        }
      }

      if (this.heatmapActive) {
        this.applyHeatmap();
      }
    });
  }

  public runLayout(): void {
    if (!this.cy) return;

    const common = {
      animate: false,
      fit: this.autoFitOnReload,
      padding: 40,
    };

    let options: cytoscape.LayoutOptions | DagreLayoutOptions;

    switch (this.currentLayout) {
      case 'breadthfirst':
        options = { name: 'breadthfirst', directed: true, spacingFactor: 1.2, ...common };
        break;
      case 'concentric':
        options = { name: 'concentric', minNodeSpacing: this.nodeSeparation, ...common };
        break;
      case 'grid':
        options = { name: 'grid', avoidOverlap: true, ...common };
        break;
      default:
        options = {
          name: 'dagre',
          rankDir: this.currentDirection,
          nodeSep: this.nodeSeparation,
          rankSep: this.rankSeparation,
          edgeSep: 20,
          ...common,
        };
    }

    this.cy.layout(options).run();
    this.applyVisualState();
  }

  public setTimingDisplay(show: boolean, unit: TimingUnit): void {
    if (this.showTimings === show && this.timingUnit === unit) return;

    this.showTimings = show;
    this.timingUnit = unit;

    if (!this.cy) return;

    this.cy.nodes().forEach((node) => {
      const data = node.data() as CanvasNodeData;
      node.data(this.buildLabelData(data.source_label, data.processing_time_ns));
    });
  }

  public isShowingTimings(): boolean {
    return this.showTimings;
  }

  public getTimingUnit(): TimingUnit {
    return this.timingUnit;
  }

  public zoomIn(): void {
    if (!this.cy) return;
    this.cy.zoom({
      level: this.cy.zoom() * 1.25,
      renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 },
    });
  }

  public zoomOut(): void {
    if (!this.cy) return;
    this.cy.zoom({
      level: this.cy.zoom() * 0.8,
      renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 },
    });
  }

  public fit(): void {
    if (!this.cy) return;
    this.cy.fit(undefined, 40);
  }

  public reset(): void {
    if (!this.cy) return;
    this.cy.zoom(1);
    this.cy.center();
  }

  public selectNodeById(id: string): CanvasNodeData | null {
    if (!this.cy) return null;
    const node = this.cy.getElementById(id);
    if (node.length === 0) return null;

    this.selectedNodeId = id;
    this.highlightLineage(node);
    return node.data() as CanvasNodeData;
  }

  public getNodeById(id: string): CanvasNodeData | null {
    if (!this.cy) return null;
    const node = this.cy.getElementById(id);
    if (node.length === 0) return null;
    return node.data() as CanvasNodeData;
  }

  public getIncomingConnections(id: string): IncomingConnection[] {
    if (!this.cy) return [];
    const node = this.cy.getElementById(id);
    if (node.length === 0) return [];

    const incomingEdges = node.incomers('edge');
    const connections: IncomingConnection[] = [];

    incomingEdges.forEach((edgeEl) => {
      const edge = edgeEl.data() as EdgeData;
      const source = edgeEl.source().data() as CanvasNodeData | undefined;
      if (source) {
        connections.push({ edge, source });
      }
    });

    return connections.sort((a, b) => a.edge.index - b.edge.index);
  }

  public centerNode(id: string): void {
    if (!this.cy) return;
    const node = this.cy.getElementById(id);
    if (node.length > 0) {
      this.cy.animate(
        {
          center: { eles: node },
          zoom: Math.max(this.cy.zoom(), 1.0),
        },
        {
          duration: 250,
        },
      );
    }
  }

  /** Clear the selection and every transient highlight derived from it. */
  public clearSelection(): void {
    this.selectedNodeId = null;
    this.visualState.clearLineage();

    if (this.cy) {
      this.applyVisualState();
    }
  }

  public highlightSearchMatches(matchingIds: string[]): void {
    if (!this.cy) return;

    this.visualState.setSearch(matchingIds.length > 0 ? matchingIds : null);
    this.applyVisualState();
  }

  public clearSearch(): void {
    if (!this.cy) return;

    this.visualState.setSearch(null);
    this.applyVisualState();
  }

  public getNodes(): CanvasNodeData[] {
    if (!this.cy) return [];
    return this.cy.nodes().map((n) => n.data() as CanvasNodeData);
  }

  public dispose(): void {
    if (this.containerElement) {
      if (this.boundPreventContextMenu) {
        this.containerElement.removeEventListener('contextmenu', this.boundPreventContextMenu);
        this.boundPreventContextMenu = null;
      }
      this.containerElement.title = '';
      this.containerElement = null;
    }
    this.onNodeSelectedCallbacks = [];
    this.onSelectionClearedCallbacks = [];
    this.onEdgeSelectedCallbacks = [];
    this.onBackgroundContextMenuCallbacks = [];
    this.onViewportChangedCallbacks = [];
    this.onCanvasTappedCallbacks = [];
    this.visualState.clearLineage();
    this.visualState.setSearch(null);
    this.lineMeasurer = null;
    this.lineMeasurerCache.clear();
    if (this.cy) {
      this.cy.destroy();
      this.cy = null;
    }
  }

  private setupEventHandlers(): void {
    if (!this.cy) return;

    this.cy.on('cxttap', (evt) => {
      evt.originalEvent.preventDefault();

      if (evt.target === this.cy) {
        this.notifyBackgroundContextMenu({
          x: evt.originalEvent.clientX,
          y: evt.originalEvent.clientY,
        });
      }
    });

    this.cy.on('pan zoom', () => {
      this.notifyViewportChanged();
    });

    this.cy.on('tap', (evt) => {
      this.notifyCanvasTapped();

      if (evt.target === this.cy) {
        this.clearLineage();
        this.selectedNodeId = null;
        this.notifySelectionCleared();
      }
    });

    this.cy.on('tap', 'node', (evt) => {
      const selectedNode = evt.target as cytoscape.NodeSingular;
      this.highlightLineage(selectedNode);

      const data = selectedNode.data() as CanvasNodeData;
      this.selectedNodeId = data.id;
      this.notifyNodeSelected(data);
    });

    this.cy.on('tap', 'edge', (evt) => {
      const edge = evt.target as cytoscape.EdgeSingular;
      const data = edge.data() as EdgeData;
      const source = edge.source().data() as CanvasNodeData;
      const target = edge.target().data() as CanvasNodeData;

      this.notifyEdgeSelected(data, source, target);
    });

    this.cy.on('mouseover', 'edge', (evt) => {
      const edge = evt.target as cytoscape.EdgeSingular;
      const data = edge.data() as EdgeData;
      const source = edge.source().data() as CanvasNodeData;
      const target = edge.target().data() as CanvasNodeData;

      let title = `${source.name} → ${target.name} (Slot #${data.index})\nPattern: ${data.pattern_name}`;
      if (data.media_label) {
        title += `\nMedia: ${data.media_label}`;
      }
      if (this.containerElement) {
        this.containerElement.title = title;
      }
    });

    this.cy.on('mouseout', 'edge', () => {
      if (this.containerElement) {
        this.containerElement.title = '';
      }
    });
  }

  private notifyNodeSelected(node: CanvasNodeData): void {
    for (const cb of this.onNodeSelectedCallbacks) {
      void Result.fromThrowable(() => cb(node)).mapErr((err) => {
        console.error('Error in node selection listener:', err);
      });
    }
  }

  private notifySelectionCleared(): void {
    for (const cb of this.onSelectionClearedCallbacks) {
      void Result.fromThrowable(() => cb()).mapErr((err) => {
        console.error('Error in selection cleared listener:', err);
      });
    }
  }

  private notifyEdgeSelected(edge: EdgeData, source: CanvasNodeData, target: CanvasNodeData): void {
    for (const cb of this.onEdgeSelectedCallbacks) {
      void Result.fromThrowable(() => cb(edge, source, target)).mapErr((err) => {
        console.error('Error in edge selection listener:', err);
      });
    }
  }

  private notifyBackgroundContextMenu(pos: { x: number; y: number }): void {
    for (const cb of this.onBackgroundContextMenuCallbacks) {
      void Result.fromThrowable(() => cb(pos)).mapErr((err) => {
        console.error('Error in background context menu listener:', err);
      });
    }
  }

  private notifyViewportChanged(): void {
    for (const cb of this.onViewportChangedCallbacks) {
      void Result.fromThrowable(() => cb()).mapErr((err) => {
        console.error('Error in viewport changed listener:', err);
      });
    }
  }

  private notifyCanvasTapped(): void {
    for (const cb of this.onCanvasTappedCallbacks) {
      void Result.fromThrowable(() => cb()).mapErr((err) => {
        console.error('Error in canvas tapped listener:', err);
      });
    }
  }

  private highlightLineage(selectedNode: cytoscape.NodeSingular): void {
    if (!this.cy) return;

    this.visualState.setLineage(
      selectedNode.id(),
      selectedNode.predecessors().map((node) => node.id()),
      selectedNode.successors().map((node) => node.id()),
    );
    this.applyVisualState();
  }

  private clearLineage(): void {
    if (!this.cy) return;

    this.visualState.clearLineage();
    this.applyVisualState();
  }

  private applyVisualState(): void {
    if (this.cy) {
      this.visualState.apply(this.cy, { showLineage: !this.heatmapActive });
      if (this.heatmapActive) {
        this.applyHeatmap();
      }
    }
  }

  private applyHeatmap(): void {
    if (!this.cy) return;

    const nodes = this.cy.nodes();
    let maxTime = 0;

    nodes.forEach((n) => {
      const t = n.data('processing_time_ns') as number | undefined;
      if (t === undefined) {
        console.warn("Couldn't find 'processing_time_ns' when applying heatmap");
      } else {
        if (t > maxTime) maxTime = t;
      }
    });

    nodes.forEach((n) => {
      const time = n.data('processing_time_ns') as number | undefined;
      if (time === undefined) {
        console.warn("Couldn't find 'processing_time_ns' when applying heatmap");
        return;
      }
      const ratio = calculateHeatmapRatio(time, maxTime);
      const color = interpolateHeatmapColor(ratio);
      n.style('background-color', color);
    });
  }

  private buildLabelData(
    sourceLabel: string,
    processingTimeNs: number,
  ): { node_width: number; node_height: number; label: string } {
    const timing = this.showTimings ? formatTiming(processingTimeNs, this.timingUnit) : '';
    const label = timing ? `${sourceLabel}\n${timing}` : sourceLabel;
    const lines = label.split('\n');
    const width = lines.reduce(
      (max, line) => Math.max(max, Math.ceil(this.getLineMeasurer()(line))),
      0,
    );

    return { label, node_width: width, node_height: lines.length * LABEL_FONT_SIZE };
  }

  private getLineMeasurer(): (line: string) => number {
    if (this.lineMeasurer) return this.lineMeasurer;

    const context = Result.fromThrowable(() =>
      document.createElement('canvas').getContext('2d'),
    ).unwrapOr(null);

    if (!context) {
      this.lineMeasurer = (line) => line.length * FALLBACK_CHAR_WIDTH;
      return this.lineMeasurer;
    }

    context.font = LABEL_FONT;

    this.lineMeasurer = (line) => {
      let width = this.lineMeasurerCache.get(line);
      if (width === undefined) {
        width = context.measureText(line).width;
        this.lineMeasurerCache.set(line, width);
      }
      return width;
    };

    return this.lineMeasurer;
  }

  private buildStylesheet(t: ThemeTokens): cytoscape.StylesheetStyle[] {
    return [
      {
        selector: 'node',
        style: {
          shape: 'round-rectangle',
          'background-color': t.surfaceElevated,
          'border-width': 1.5,
          'border-color': t.border,
          label: 'data(label)',
          color: t.textPrimary,
          'font-family': 'system-ui, -apple-system, sans-serif',
          'font-size': `${LABEL_FONT_SIZE}px`,
          'font-weight': 'bold',
          'text-valign': 'center',
          'text-halign': 'center',
          'text-wrap': 'wrap',
          width: 'data(node_width)',
          height: 'data(node_height)',
          padding: '12px',
          'transition-property': 'border-color, border-width, opacity',
          'transition-duration': 0.15,
        },
      },
      {
        selector: 'node[?is_root]',
        style: {
          'border-color': t.accentAmber,
          'border-width': 2.5,
        },
      },
      {
        selector: 'node.mode-frame-state, node.mode-framestate',
        style: {
          'border-style': 'dashed',
          'border-color': t.danger,
        },
      },
      {
        selector: 'node.mode-unordered',
        style: {
          'border-style': 'dotted',
          'border-color': t.accent,
        },
      },
      {
        selector: 'node.mode-parallel-requests',
        style: {
          'border-style': 'dashed',
          'border-color': t.accentEmerald,
        },
      },
      {
        selector: 'node.media-change-node',
        style: {
          'underlay-color': t.accentAmber,
          'underlay-padding': 3,
          'underlay-opacity': 0.2,
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-color': t.accent,
          'border-width': 2.5,
          'underlay-color': t.accent,
          'underlay-padding': 4,
          'underlay-opacity': 0.3,
          opacity: 1.0,
          'z-index': 10,
        },
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'target-arrow-shape': 'triangle',
          'line-color': t.border,
          'target-arrow-color': t.border,
          'arrow-scale': 1.1,
          width: 1.5,
        },
      },
      {
        // Request patterns are always encoded in the line style; labels need the toggle.
        selector: 'edge.pattern-strict-spatial',
        style: {
          'line-style': 'dotted',
        },
      },
      {
        selector: 'edge.pattern-no-frame-reuse',
        style: {
          'line-style': 'dashed',
        },
      },
      {
        selector: 'edge.pattern-frame-reuse-last-only',
        style: {
          'line-style': 'dashed',
          width: 2,
        },
      },
      {
        selector: 'edge.media-changed',
        style: {
          'line-color': t.accentAmber,
          'target-arrow-color': t.accentAmber,
          width: 2.5,
        },
      },
      {
        selector: 'edge.pattern-labelled[label != ""]',
        style: {
          label: 'data(label)',
          'font-size': '9px',
          color: t.textSecondary,
          'text-background-opacity': 1,
          'text-background-color': t.bg,
          'text-background-padding': '2px 4px',
          'text-background-shape': 'roundrectangle',
        },
      },
      {
        selector: '.dimmed',
        style: {
          opacity: 0.15,
        },
      },
      {
        selector: '.highlighted-upstream',
        style: {
          'border-color': t.accent,
          'line-color': t.accent,
          'target-arrow-color': t.accent,
          opacity: 1.0,
          'z-index': 10,
        },
      },
      {
        selector: '.highlighted-downstream',
        style: {
          'border-color': t.accentAmber,
          'line-color': t.accentAmber,
          'target-arrow-color': t.accentAmber,
          opacity: 1.0,
          'z-index': 10,
        },
      },
      {
        selector: '.search-matched',
        style: {
          'border-color': t.accentEmerald,
          'border-width': 3,
          'underlay-color': t.accentEmerald,
          'underlay-padding': 6,
          'underlay-opacity': 0.4,
          opacity: 1.0,
        },
      },
    ];
  }
}

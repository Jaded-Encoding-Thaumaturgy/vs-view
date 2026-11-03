import { type TemplateResult, html, nothing, render } from 'lit-html';
import { classMap } from 'lit-html/directives/class-map.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { when } from 'lit-html/directives/when.js';
import { Disposable } from 'vsview-shared';

import type { CytoscapeCanvas } from '../canvas/cytoscape';
import type {
  CanvasNodeData,
  FilterModeName,
  GraphAnalysisPayload,
  TransitionGroup,
  WarningGroup,
} from '../types';
import { type TimingStats, computeTimingStats } from '../utils/analysis';
import { formatTiming } from '../utils/format';

interface OverviewOptions {
  container: HTMLElement;
  canvas: CytoscapeCanvas;
  onFocusNode: (nodeId: string) => void;
  onPatternsChanged: (enabled: boolean) => void;
}

const CHIP_LIMIT = 15 as const;

interface AccordionGroupConfig {
  key: string;
  title: string;
  count: number;
  countUnit: string;
  highlightLabel: string;
  nodeIds: readonly string[];
  description: string;
  severityClass?: string;
  chipLabelPrefix?: string;
}

interface ModeChip {
  value: string;
  label: string;
  count: number;
}

export class OverviewPanel extends Disposable {
  private analysis: GraphAnalysisPayload | null = null;
  private timings: TimingStats | null = null;
  private nodeNames = new Map<string, string>();
  private modeFilter: FilterModeName | null = null;
  private mediaOnly = false;
  private isOpen = false;

  private readonly expandedGroups = new Set<string>();
  private readonly expandedChips = new Set<string>();
  private activeHighlightKey: string | null = null;

  constructor(private readonly opts: OverviewOptions) {
    super();
    this.opts.container.classList.add('overview-container');
  }

  public get opened(): boolean {
    return this.isOpen;
  }

  public setData(nodes: readonly CanvasNodeData[], analysis: GraphAnalysisPayload): void {
    this.analysis = analysis;
    this.timings = computeTimingStats(nodes);
    this.nodeNames = new Map(nodes.map((n) => [n.id, n.name]));

    if (this.activeHighlightKey) {
      this.reapplyHighlight();
    }

    if (this.isOpen) this.render();
  }

  public updateTimings(nodes: readonly CanvasNodeData[]): void {
    this.timings = computeTimingStats(nodes);

    if (this.isOpen) this.render();
  }

  public setPatterns(enabled: boolean): void {
    if (this.isOpen) {
      this.opts.canvas.setPatternStyles(enabled);
      this.render();
    }
  }

  public toggle(): void {
    if (this.isOpen) {
      this.close();
    } else {
      this.open();
    }
  }

  public open(): void {
    this.isOpen = true;
    this.opts.container.classList.add('open');
    this.render();
  }

  public close(): void {
    this.isOpen = false;
    this.opts.container.classList.remove('open');
    if (this.activeHighlightKey) {
      this.clearHighlight();
    }
  }

  public override dispose(): void {
    if (this.activeHighlightKey) {
      this.clearHighlight();
    }
    this.expandedGroups.clear();
    this.expandedChips.clear();
    this.nodeNames.clear();
    super.dispose();
  }

  private selectMode(mode: string): void {
    this.modeFilter = mode !== 'all' ? (mode.toUpperCase() as FilterModeName) : null;
    this.opts.canvas.setModeFilter(this.modeFilter);
    this.render();
  }

  private togglePatterns(): void {
    const next = !this.opts.canvas.isShowingPatternStyles();
    this.opts.canvas.setPatternStyles(next);
    this.opts.onPatternsChanged(next);
    this.render();
  }

  private toggleMediaOnly(): void {
    this.mediaOnly = !this.mediaOnly;
    this.opts.canvas.setMediaOnly(this.mediaOnly);
    this.render();
  }

  private toggleGroup(key: string): void {
    if (this.expandedGroups.has(key)) {
      this.expandedGroups.delete(key);
    } else {
      this.expandedGroups.add(key);
    }
    this.render();
  }

  private toggleChips(key: string): void {
    if (this.expandedChips.has(key)) {
      this.expandedChips.delete(key);
    } else {
      this.expandedChips.add(key);
    }
    this.render();
  }

  private toggleGroupHighlight(key: string, nodeIds: readonly string[]): void {
    if (this.activeHighlightKey === key) {
      this.clearHighlight();
    } else {
      this.activeHighlightKey = key;
      this.opts.canvas.highlightNodeGroup(nodeIds);
    }
    this.render();
  }

  private clearHighlight(): void {
    this.activeHighlightKey = null;
    this.opts.canvas.highlightNodeGroup(null);
  }

  private reapplyHighlight(): void {
    if (!this.analysis) {
      this.clearHighlight();
      return;
    }

    const wGroup = this.analysis.warningGroups.find(
      (g) => `w:${g.code}` === this.activeHighlightKey,
    );
    if (wGroup) {
      this.opts.canvas.highlightNodeGroup(wGroup.nodeIds);
      return;
    }

    const tGroup = this.analysis.transitionGroups.find(
      (g) => `t:${g.signature}` === this.activeHighlightKey,
    );
    if (tGroup) {
      this.opts.canvas.highlightNodeGroup(tGroup.targetNodeIds);
      return;
    }

    this.clearHighlight();
  }

  private render(): void {
    if (!this.analysis || !this.timings) {
      render(nothing, this.opts.container);
      return;
    }

    render(
      html`
        <div class="overview-panel" role="dialog" aria-label="Graph overview">
          <div class="overview-header">
            <span class="overview-title">Graph Overview</span>
            <button class="overview-close" title="Close overview" @click=${() => this.close()}>
              &times;
            </button>
          </div>
          <div class="overview-body">
            ${this.renderStats(this.analysis, this.timings)} ${this.renderModes(this.analysis)}
            ${this.renderPatterns(this.analysis)} ${this.renderWarnings(this.analysis)}
            ${this.renderTransitions(this.analysis)}
          </div>
        </div>
      `,
      this.opts.container,
    );
  }

  private row(label: string, value: string): TemplateResult {
    return html`<div class="overview-label">${label}</div>
      <div class="overview-value">${value}</div>`;
  }

  private renderStats(analysis: GraphAnalysisPayload, timings: TimingStats): TemplateResult {
    const topNamespaces = timings.namespaces
      .slice(0, 3)
      .map((ns) => `${ns.name} (${ns.count})`)
      .join(', ');

    const formats = analysis.formats.map((f) => `${f.name} (${f.count})`).join(', ');

    return html`
      <div class="overview-section">
        <div class="overview-section-title">Graph</div>
        <div class="overview-grid">
          ${this.row('Nodes', String(analysis.nodeCount))}
          ${this.row('Edges', String(analysis.edgeCount))}
          ${this.row('Depth', String(analysis.depth))}
          ${this.row('Total time', formatTiming(timings.totalTimeNs) || 'no profiling data')}
          ${this.row('Root time', formatTiming(timings.rootTimeNs) || 'no profiling data')}
          ${this.row('Top plugins', topNamespaces || 'none')}
          ${this.row('Formats', formats || 'none')}
        </div>
      </div>
    `;
  }

  private renderModes(analysis: GraphAnalysisPayload): TemplateResult {
    const chips: ModeChip[] = [
      { value: 'all', label: 'All modes', count: analysis.nodeCount },
      ...analysis.modes.map((mode) => ({ value: mode.name, label: mode.name, count: mode.count })),
    ];

    const active = (this.modeFilter ?? 'all').toLowerCase();

    return html`
      <div class="overview-section">
        <div class="overview-section-title">Filter modes</div>
        <div class="overview-chips">
          ${repeat(
            chips,
            (chip) => chip.value,
            (chip) => this.renderModeChip(chip, chip.value.toLowerCase() === active),
          )}
        </div>
      </div>
    `;
  }

  private renderModeChip(chip: ModeChip, isActive: boolean): TemplateResult {
    return html`
      <button
        class=${classMap({ 'overview-chip': true, active: isActive })}
        @click=${() => this.selectMode(chip.value)}
      >
        ${chip.label} <span class="overview-chip-count">${chip.count}</span>
      </button>
    `;
  }

  private renderPatterns(analysis: GraphAnalysisPayload): TemplateResult {
    const list = analysis.patterns.map((p) => `${p.name} (${p.count})`).join(', ');
    const active = this.opts.canvas.isShowingPatternStyles();

    return html`
      <div class="overview-section">
        <div class="overview-section-title">Request patterns</div>
        <div class="overview-empty">${list || 'none'}</div>
        <button
          class=${classMap({ 'overview-chip': true, active })}
          @click=${() => this.togglePatterns()}
        >
          ${active ? 'Hide pattern labels' : 'Show pattern labels'}
        </button>
      </div>
    `;
  }

  private renderWarnings(analysis: GraphAnalysisPayload): TemplateResult {
    if (analysis.warningCount === 0) {
      return html`
        <div class="overview-section">
          <div class="overview-section-title">Warnings</div>
          <div class="overview-empty">No concurrency or request-pattern warnings.</div>
        </div>
      `;
    }

    return html`
      <div class="overview-section">
        <div class="overview-section-title">Warnings (${analysis.warningCount})</div>
        <div class="overview-groups">
          ${repeat(
            analysis.warningGroups,
            (group) => group.code,
            (group) => this.renderWarningGroup(group),
          )}
        </div>
      </div>
    `;
  }

  private renderWarningGroup(group: WarningGroup): TemplateResult {
    return this.renderAccordionGroup({
      key: `w:${group.code}`,
      title: group.title,
      count: group.count,
      countUnit: 'occurrences',
      highlightLabel: 'affected nodes',
      nodeIds: group.nodeIds,
      description: group.description,
      severityClass: `severity-${group.severity}`,
      chipLabelPrefix: 'Focus node',
    });
  }

  private renderTransitions(analysis: GraphAnalysisPayload): TemplateResult {
    if (analysis.transitionCount === 0) {
      return html`
        <div class="overview-section">
          <div class="overview-section-title">Media changes</div>
          <div class="overview-empty">Media info is identical across every edge.</div>
        </div>
      `;
    }

    return html`
      <div class="overview-section">
        <div class="overview-section-title">Media changes (${analysis.transitionCount})</div>
        <button
          class=${classMap({ 'overview-chip': true, active: this.mediaOnly })}
          style="margin-bottom: 8px;"
          @click=${() => this.toggleMediaOnly()}
        >
          ${this.mediaOnly ? 'Show every node' : 'Dim unchanged nodes'}
        </button>
        <div class="overview-groups">
          ${repeat(
            analysis.transitionGroups,
            (group) => group.signature,
            (group) => this.renderTransitionGroup(group),
          )}
        </div>
      </div>
    `;
  }

  private renderTransitionGroup(group: TransitionGroup): TemplateResult {
    const count = group.transitions.length;
    return this.renderAccordionGroup({
      key: `t:${group.signature}`,
      title: group.signature,
      count,
      countUnit: 'transitions',
      highlightLabel: 'target nodes',
      nodeIds: group.targetNodeIds,
      description: `Changes ${group.fields.join(', ')} across ${count} edge${count > 1 ? 's' : ''}.`,
      chipLabelPrefix: 'Focus target node',
    });
  }

  private renderAccordionGroup(config: AccordionGroupConfig): TemplateResult {
    const isOpen = this.expandedGroups.has(config.key);
    const isHighlighted = this.activeHighlightKey === config.key;
    const highlightTitle = isHighlighted
      ? 'Clear canvas highlight'
      : `Highlight ${config.highlightLabel} on canvas`;

    return html`
      <div
        class=${classMap({
          'overview-group': true,
          open: isOpen,
          ...(config.severityClass ? { [config.severityClass]: true } : {}),
        })}
      >
        <div class="overview-group-header" @click=${() => this.toggleGroup(config.key)}>
          <div class="overview-group-info">
            <span class="overview-group-chevron">▶</span>
            <span class="overview-group-title" title="${config.title}">${config.title}</span>
            <span class="overview-group-count" title="${config.count} ${config.countUnit}"
              >${config.count}</span
            >
          </div>
          <div class="overview-group-actions" @click=${(e: Event) => e.stopPropagation()}>
            <button
              class=${classMap({ 'overview-group-action-btn': true, active: isHighlighted })}
              title="${highlightTitle}"
              @click=${() => this.toggleGroupHighlight(config.key, config.nodeIds)}
            >
              ${isHighlighted ? 'Highlighted' : 'Highlight'}
            </button>
          </div>
        </div>
        ${when(isOpen, () => this.renderGroupBody(config))}
      </div>
    `;
  }

  private renderGroupBody(config: AccordionGroupConfig): TemplateResult {
    return html`
      <div class="overview-group-body">
        <div class="overview-group-desc">${config.description}</div>
        ${this.renderNodeChips(config.key, config.nodeIds, config.chipLabelPrefix ?? 'Focus node')}
      </div>
    `;
  }

  private renderNodeChips(
    key: string,
    nodeIds: readonly string[],
    chipPrefix: string,
  ): TemplateResult {
    const isExpanded = this.expandedChips.has(key);
    const visibleIds = isExpanded ? nodeIds : nodeIds.slice(0, CHIP_LIMIT);
    const hasMore = nodeIds.length > CHIP_LIMIT;

    return html`
      <div class="overview-node-chips">
        ${repeat(
          visibleIds,
          (nodeId) => nodeId,
          (nodeId) => this.renderNodeChip(nodeId, chipPrefix),
        )}
        ${when(hasMore, () => this.renderChipToggle(key, isExpanded, nodeIds.length - CHIP_LIMIT))}
      </div>
    `;
  }

  private renderNodeChip(nodeId: string, chipPrefix: string): TemplateResult {
    const label = this.nodeNames.get(nodeId) ?? nodeId;
    return html`
      <button
        class="overview-node-chip"
        title="${chipPrefix} ${label} (${nodeId})"
        @click=${() => this.opts.onFocusNode(nodeId)}
      >
        ${label}
      </button>
    `;
  }

  private renderChipToggle(key: string, isExpanded: boolean, remaining: number): TemplateResult {
    return html`
      <button class="overview-chip-more" @click=${() => this.toggleChips(key)}>
        ${isExpanded ? 'Show less' : `+${remaining} more`}
      </button>
    `;
  }
}

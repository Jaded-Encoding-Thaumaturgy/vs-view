import './hud.css';
import { type TemplateResult, html, render } from 'lit-html';
import { classMap } from 'lit-html/directives/class-map.js';
import { live } from 'lit-html/directives/live.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { when } from 'lit-html/directives/when.js';
import { Disposable } from 'vsview-shared';

import type { CytoscapeCanvas } from '../canvas/cytoscape';
import type { LayoutDirection, OutputItem } from '../types';
import { isEmptyQuery, matchesNode, parseSearchQuery } from '../utils/search';

interface HudOptions {
  container: HTMLElement;
  canvas: CytoscapeCanvas;
  onReloadRequested: () => void;
  onOutputSelected: (index: number) => void;
  onNodeMatched?: (nodeId: string) => void;
  onPatternsToggled?: (enabled: boolean) => void;
  onOverviewRequested?: () => void;
}

export const DEFAULT_SEARCH_TOOLTIP =
  'Matches against node names, IDs, labels, call stack functions, namespaces, argument names, and argument values:\n' +
  '• Words: Space-separated terms use AND matching (e.g. std crop left 8)\n' +
  '• Regex: Enclose in slashes with optional flags (e.g. /crop$/i). Invalid syntax displays !\n' +
  '• Press Enter / Shift+Enter (or ▲/▼) to cycle matches, Esc to clear';

export class HudController extends Disposable {
  private outputs: OutputItem[] = [];
  private direction: LayoutDirection = 'TB';
  private heatmap = false;
  private patterns = false;

  private searchQuery = '';
  private searchError: string | null = null;
  private searchMatches: string[] = [];
  private searchIndex = -1;

  constructor(private readonly opts: HudOptions) {
    super();
    this.render();
  }

  public setOutputs(outputs: OutputItem[]): void {
    this.outputs = outputs;
    this.render();
  }

  public syncOptions(options: {
    direction: LayoutDirection;
    heatmap: boolean;
    patterns?: boolean;
  }): void {
    this.direction = options.direction;
    this.heatmap = options.heatmap;
    this.patterns = options.patterns ?? false;
    this.render();
  }

  public focusSearch(): void {
    const input = this.opts.container.querySelector<HTMLInputElement>('#hud-search-input');
    input?.focus();
    input?.select();
  }

  public refreshSearch(centerFirst = false): void {
    if (!this.searchQuery.trim()) return;

    const previous = this.searchMatches[this.searchIndex];
    this.performSearch(centerFirst);

    if (previous) {
      const idx = this.searchMatches.indexOf(previous);
      if (idx >= 0) {
        this.searchIndex = idx;
      }
    }
    this.render();
  }

  private handleOutputChange(event: Event): void {
    const select = event.target as HTMLSelectElement;
    const idx = parseInt(select.value, 10);
    if (!Number.isNaN(idx)) {
      this.opts.onOutputSelected(idx);
    }
  }

  private toggleDirection(): void {
    const next: LayoutDirection = this.opts.canvas.direction === 'TB' ? 'LR' : 'TB';
    this.opts.canvas.direction = next;
    this.direction = next;
    this.render();
  }

  private toggleHeatmap(): void {
    const nextActive = !this.opts.canvas.heatmapActive;
    this.opts.canvas.heatmapActive = nextActive;
    this.heatmap = nextActive;
    this.render();
  }

  private togglePatterns(): void {
    const next = !this.opts.canvas.isShowingPatternStyles();
    this.opts.canvas.setPatternStyles(next);
    this.patterns = next;
    this.opts.onPatternsToggled?.(next);
    this.render();
  }

  private handleSearchInput(event: Event): void {
    this.searchQuery = (event.target as HTMLInputElement).value;
    this.performSearch();
    this.render();
  }

  private handleSearchKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.cycleMatch(event.shiftKey ? -1 : 1);
    } else if (event.key === 'Escape') {
      this.searchQuery = '';
      this.performSearch();
      this.render();
      (event.target as HTMLInputElement).blur();
    }
  }

  private performSearch(centerFirst = true): void {
    const query = parseSearchQuery(this.searchQuery);
    this.searchError = query.error;

    if (query.error !== null || isEmptyQuery(query)) {
      this.searchMatches = [];
      this.searchIndex = -1;
      this.opts.canvas.clearSearch();
      return;
    }

    this.searchMatches = this.opts.canvas
      .getNodes()
      .filter((node) => matchesNode(node, query))
      .map((node) => node.id);

    this.opts.canvas.highlightSearchMatches(this.searchMatches);

    if (this.searchMatches.length > 0) {
      this.searchIndex = 0;
      const firstMatch = this.searchMatches[0];
      if (firstMatch && centerFirst) {
        if (this.opts.onNodeMatched) {
          this.opts.onNodeMatched(firstMatch);
        } else {
          this.opts.canvas.centerNode(firstMatch);
        }
      }
    } else {
      this.searchIndex = -1;
    }
  }

  private cycleMatch(direction: 1 | -1): void {
    if (this.searchMatches.length === 0) return;

    this.searchIndex =
      (this.searchIndex + direction + this.searchMatches.length) % this.searchMatches.length;
    this.render();

    const targetId = this.searchMatches[this.searchIndex];
    if (targetId) {
      if (this.opts.onNodeMatched) {
        this.opts.onNodeMatched(targetId);
      } else {
        this.opts.canvas.centerNode(targetId);
      }
    }
  }

  private getSearchCountLabel(): string {
    if (this.searchError !== null) return '!';
    if (!this.searchQuery.trim()) return '';
    if (this.searchMatches.length === 0) return '0/0';
    return `${this.searchIndex + 1}/${this.searchMatches.length}`;
  }

  private render(): void {
    const hasMatches = this.searchError === null && this.searchMatches.length > 0;

    render(
      html`
        <div class="hud-bar">
          ${this.renderOutputSelect()}
          <div class="hud-separator"></div>
          ${this.renderCanvasControls()}
          <div class="hud-separator"></div>
          ${this.renderToolActions()}
          <div class="hud-separator"></div>
          ${this.renderSearchBar(hasMatches)}
        </div>
      `,
      this.opts.container,
    );
  }

  private renderOutputSelect(): TemplateResult {
    function renderOutputOption(out: OutputItem): TemplateResult {
      return html`
        <option value=${String(out.index)} ?selected=${out.isActive}>
          [${out.index}] ${out.name || 'Video'}
        </option>
      `;
    }

    return html`
      <div class="hud-group">
        <select
          id="hud-output-select"
          class="hud-select"
          title="Select workspace output to inspect"
          ?disabled=${this.outputs.length === 0}
          @change=${(e: Event) => this.handleOutputChange(e)}
        >
          ${when(
            this.outputs.length === 0,
            () => html`<option value="-1">No outputs</option>`,
            () =>
              repeat(
                this.outputs,
                (out) => out.index,
                (out) => renderOutputOption(out),
              ),
          )}
        </select>
      </div>
    `;
  }

  private renderCanvasControls(): TemplateResult {
    return html`
      <div class="hud-group">
        <button
          id="hud-zoom-in"
          class="hud-btn"
          title="Zoom In"
          @click=${() => this.opts.canvas.zoomIn()}
        >
          +
        </button>
        <button
          id="hud-zoom-out"
          class="hud-btn"
          title="Zoom Out"
          @click=${() => this.opts.canvas.zoomOut()}
        >
          −
        </button>
        <button
          id="hud-fit"
          class="hud-btn"
          title="Fit Entire Graph"
          @click=${() => this.opts.canvas.fit()}
        >
          Fit
        </button>
        <button
          id="hud-reset"
          class="hud-btn"
          title="Reset View (1:1)"
          @click=${() => this.opts.canvas.reset()}
        >
          1:1
        </button>
        <button
          id="hud-direction"
          class="hud-btn"
          title="Toggle Layout Direction (Vertical / Horizontal)"
          @click=${() => this.toggleDirection()}
        >
          ${this.direction === 'TB' ? '↕ TB' : '↔ LR'}
        </button>
      </div>
    `;
  }

  private renderToolActions(): TemplateResult {
    return html`
      <div class="hud-group">
        <button
          id="hud-heatmap"
          class=${classMap({ 'hud-btn': true, active: this.heatmap })}
          title="Toggle profiling heatmap (Teal: fast → Amber: moderate → Red: bottleneck)"
          @click=${() => this.toggleHeatmap()}
        >
          Heatmap
        </button>
        <button
          id="hud-patterns"
          class=${classMap({ 'hud-btn': true, active: this.patterns })}
          title="Show request pattern labels on edges"
          @click=${() => this.togglePatterns()}
        >
          Patterns
        </button>
        <button
          id="hud-overview"
          class="hud-btn"
          title="Graph overview, warnings and exports"
          @click=${() => this.opts.onOverviewRequested?.()}
        >
          Overview
        </button>
        <button
          id="hud-refresh"
          class="hud-btn"
          title="Re-inspect the graph and refresh timings"
          @click=${() => this.opts.onReloadRequested()}
        >
          ↻
        </button>
      </div>
    `;
  }

  private renderSearchBar(hasMatches: boolean): TemplateResult {
    return html`
      <div class="hud-group hud-search-group">
        <span class="hud-search-icon" title=${DEFAULT_SEARCH_TOOLTIP}>🔍</span>
        <input
          id="hud-search-input"
          class=${classMap({ 'hud-search-input': true, invalid: this.searchError !== null })}
          type="text"
          placeholder="Search filter..."
          title=${this.searchError ?? DEFAULT_SEARCH_TOOLTIP}
          .value=${live(this.searchQuery)}
          @input=${(e: Event) => this.handleSearchInput(e)}
          @keydown=${(e: KeyboardEvent) => this.handleSearchKeyDown(e)}
        />
        <span id="hud-search-count" class="hud-search-count"> ${this.getSearchCountLabel()} </span>
        <button
          id="hud-search-prev"
          class="hud-search-btn"
          title="Previous match"
          ?disabled=${!hasMatches}
          @click=${() => this.cycleMatch(-1)}
        >
          ▲
        </button>
        <button
          id="hud-search-next"
          class="hud-search-btn"
          title="Next match"
          ?disabled=${!hasMatches}
          @click=${() => this.cycleMatch(1)}
        >
          ▼
        </button>
      </div>
    `;
  }
}

import { type TemplateResult, html, nothing, render } from 'lit-html';
import { classMap } from 'lit-html/directives/class-map.js';
import { repeat } from 'lit-html/directives/repeat.js';
import { when } from 'lit-html/directives/when.js';
import { Disposable, Result } from 'vsview-shared';

import type {
  CallFrame,
  CanvasNodeData,
  EdgeData,
  IncomingConnection,
  NodeCopyTarget,
  NodeData,
  TimingUnit,
} from '../types';
import { formatFps, formatProcessingTime } from '../utils/format';

interface InspectorDrawerOptions {
  container: HTMLElement;
  onCopyNode: (id: string, target: NodeCopyTarget, frameIdx?: number) => void;
  resolveNode: (id: string) => CanvasNodeData | null;
  getIncomingConnections: (id: string) => IncomingConnection[];
  onFocusNode: (id: string) => void;
}

interface EdgeInspectionState {
  edge: EdgeData;
  source: CanvasNodeData;
  target: CanvasNodeData;
}

export class InspectorDrawer extends Disposable {
  private static readonly DEFAULT_WIDTH = 400;
  private static readonly MIN_WIDTH = 280;
  private static readonly VIEWPORT_MARGIN = 80;

  private isOpen = false;
  private showTimings = true;
  private timingUnit: TimingUnit = 'auto';
  private lastNode: NodeData | null = null;
  private lastEdge: EdgeInspectionState | null = null;
  private openFrames = new Set<number>([0]);
  private dragController: AbortController | null = null;

  constructor(private readonly opts: InspectorDrawerOptions) {
    super();
    this.render();
  }

  public get opened(): boolean {
    return this.isOpen;
  }

  public override dispose(): void {
    this.stopDragging();
    render(nothing, this.opts.container);
    super.dispose();
  }

  public inspectNode(node: NodeData): void {
    if (this.lastNode?.id !== node.id) {
      this.openFrames = new Set([0]);
    }
    this.lastNode = node;
    this.lastEdge = null;
    this.render();
    this.open();
  }

  public inspectEdge(edge: EdgeData, source: CanvasNodeData, target: CanvasNodeData): void {
    this.lastNode = null;
    this.lastEdge = { edge, source, target };
    this.render();
    this.open();
  }

  public setTimingOptions(show: boolean, unit: TimingUnit): void {
    if (this.showTimings === show && this.timingUnit === unit) return;

    this.showTimings = show;
    this.timingUnit = unit;

    if (this.lastNode) {
      this.render();
    }
  }

  public open(): void {
    if (this.isOpen) return;
    this.isOpen = true;
    this.opts.container.classList.add('open');
  }

  public close(): void {
    if (!this.isOpen) return;
    this.stopDragging();
    this.isOpen = false;
    this.opts.container.classList.remove('open');
  }

  public resetWidth(): void {
    this.opts.container.style.removeProperty('--inspector-width');
  }

  private stopDragging(): void {
    this.dragController?.abort();
    this.dragController = null;
  }

  private handleResizerPointerDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    e.preventDefault();

    this.stopDragging();

    const { pointerId } = e;
    const resizer = e.currentTarget instanceof HTMLElement ? e.currentTarget : null;
    const startX = e.clientX;
    const rect = this.opts.container.getBoundingClientRect();
    const startWidth = rect.width > 0 ? rect.width : InspectorDrawer.DEFAULT_WIDTH;
    const parentWidth = this.opts.container.parentElement?.clientWidth ?? window.innerWidth;
    const maxWidth = Math.max(
      InspectorDrawer.MIN_WIDTH,
      parentWidth - InspectorDrawer.VIEWPORT_MARGIN,
    );

    this.opts.container.classList.add('resizing');
    resizer?.classList.add('dragging');

    const prevUserSelect = document.body.style.userSelect;
    const prevCursor = document.body.style.cursor;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    const ac = new AbortController();
    this.dragController = ac;

    const cleanup = (): void => {
      this.opts.container.classList.remove('resizing');
      resizer?.classList.remove('dragging');
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = prevCursor;
      if (this.dragController === ac) {
        this.dragController = null;
      }
    };

    ac.signal.addEventListener('abort', cleanup, { once: true });

    const finish = (finishEv: PointerEvent): void => {
      if (finishEv.pointerId !== pointerId) return;
      ac.abort();
    };

    window.addEventListener(
      'pointermove',
      (moveEv: PointerEvent): void => {
        if (moveEv.pointerId !== pointerId) return;
        const deltaX = startX - moveEv.clientX;
        const newWidth = Math.max(
          InspectorDrawer.MIN_WIDTH,
          Math.min(maxWidth, Math.round(startWidth + deltaX)),
        );

        this.opts.container.style.setProperty('--inspector-width', `${newWidth}px`);
      },
      { signal: ac.signal },
    );

    window.addEventListener('pointerup', finish, { signal: ac.signal });
    window.addEventListener('pointercancel', finish, { signal: ac.signal });
  }

  private copyNodeId(): void {
    if (this.lastNode) this.opts.onCopyNode(this.lastNode.id, 'id');
  }

  private copyFilterCall(frameIdx: number): void {
    if (this.lastNode) this.opts.onCopyNode(this.lastNode.id, 'call', frameIdx);
  }

  private copyReport(): void {
    if (this.lastNode) this.opts.onCopyNode(this.lastNode.id, 'report');
  }

  private toggleFrame(idx: number): void {
    if (this.openFrames.has(idx)) {
      this.openFrames.delete(idx);
    } else {
      this.openFrames.add(idx);
    }
    this.render();
  }

  private render(): void {
    const title = this.lastNode
      ? this.lastNode.name
      : this.lastEdge
        ? `${this.lastEdge.source.name} → ${this.lastEdge.target.name}`
        : 'Filter';

    render(
      html`
        <div
          class="inspector-resizer"
          id="inspector-resizer"
          title="Drag to resize inspector (double-click to reset)"
          @pointerdown=${(e: PointerEvent) => this.handleResizerPointerDown(e)}
          @dblclick=${() => this.resetWidth()}
        ></div>
        ${this.renderHeader(title)}
        <div class="inspector-body" id="inspector-content">${this.renderBody()}</div>
      `,
      this.opts.container,
    );
  }

  private renderHeader(title: string): TemplateResult {
    return html`
      <div class="inspector-header">
        <div class="inspector-title-row">
          <span class="inspector-title" id="inspector-name">${title}</span>
          <div class="inspector-title-actions">
            ${this.renderReportButton()}
            <button
              class="inspector-close-btn"
              id="inspector-close"
              title="Close inspector"
              @click=${() => this.close()}
            >
              &times;
            </button>
          </div>
        </div>
        <div class="inspector-badges" id="inspector-badges">${this.renderBadges()}</div>
      </div>
    `;
  }

  private renderReportButton(): TemplateResult | typeof nothing {
    if (!this.lastNode) return nothing;

    return html`
      <button
        class="inspector-header-btn"
        id="inspector-copy-report"
        title="Copy a full text report"
        @click=${() => this.copyReport()}
      >
        Report
      </button>
    `;
  }

  private renderBadges(): TemplateResult | typeof nothing {
    if (this.lastNode) {
      const node = this.lastNode;

      return html`
        <button
          type="button"
          class="badge badge-mono badge-clickable"
          id="inspector-copy-id"
          title="Click to copy node ID"
          @click=${() => this.copyNodeId()}
        >
          ${node.id}
        </button>
        ${when(node.is_root, () => html`<span class="badge badge-amber">Output Root</span>`)}
        <span class="badge badge-mode">${node.filter_mode_name}</span>
      `;
    }

    if (this.lastEdge) {
      const { edge } = this.lastEdge;
      return html`
        <span class="badge badge-mode">${edge.pattern_name}</span>
        ${when(edge.media_label, () => html`<span class="badge badge-amber">${edge.media_label}</span>`)}
      `;
    }

    return nothing;
  }

  private renderBody(): TemplateResult | typeof nothing {
    if (this.lastNode) {
      return html`
        ${this.renderInputsCard(this.lastNode)} ${this.renderMediaCard(this.lastNode)}
        ${this.renderPerformanceCard(this.lastNode)}
        ${this.renderCallStackCard(this.lastNode.call_stack)}
      `;
    }

    if (this.lastEdge) {
      const { edge, source, target } = this.lastEdge;
      return html`
        <div class="inspector-card">
          <div class="card-title">Connection</div>
          <div class="specs-grid">
            ${this.gridRow('Source', `${source.name} [${edge.source}]`)}
            ${this.gridRow('Target', `${target.name} [${edge.target}]`)}
            ${this.gridRow('Input Slot', `#${edge.index}`)}
            ${this.gridRow('Request Pattern', edge.pattern_name)}
            ${this.gridRow('Media Change', edge.media_label ?? 'unchanged')}
            ${this.gridRow('Edge ID', edge.id)}
          </div>
        </div>
      `;
    }

    return nothing;
  }

  private gridRow(label: string, value: string): TemplateResult {
    return html`<div class="spec-label">${label}</div>
      <div class="spec-value">${value}</div>`;
  }

  private renderInputsCard(node: NodeData): TemplateResult {
    const incomingConnections = this.opts.getIncomingConnections(node.id);

    return html`
      <div class="inspector-card">
        <div class="card-title">Inputs</div>
        ${when(
          incomingConnections.length > 0,
          () => html`
            <div class="badge-list">
              ${repeat(
                incomingConnections,
                (conn) => conn.edge.id,
                (conn) => this.renderInputBadge(conn),
              )}
            </div>
          `,
          () => html`<span class="card-empty">None (Source)</span>`,
        )}
      </div>
    `;
  }

  private renderInputBadge(conn: IncomingConnection): TemplateResult {
    const srcName = conn.source.name;
    const srcId = conn.source.id;

    return html`
      <button
        type="button"
        class="badge badge-input badge-clickable"
        title="Navigate to ${srcName} [${srcId}] (Slot #${conn.edge.index})"
        @click=${(e: Event) => {
          e.stopPropagation();
          this.opts.onFocusNode(srcId);
        }}
      >
        [#${conn.edge.index}] ← ${srcName}
      </button>
    `;
  }

  private renderMediaCard(node: NodeData): TemplateResult {
    const v = node.media_info;
    const fps = formatFps(v.fps_num, v.fps_den);

    return html`
      <div class="inspector-card">
        <div class="card-title">Video Format Specs</div>
        <div class="specs-grid">
          ${this.gridRow('Resolution', `${v.width} × ${v.height}`)}
          ${this.gridRow('Format', v.format.name)}
          ${this.gridRow('Frame Rate', `${v.fps_num}/${v.fps_den} (${fps} fps)`)}
          ${this.gridRow('Total Frames', v.num_frames.toLocaleString())}
          ${when(v.gpu_resident !== undefined, () =>
            this.gridRow('GPU Resident', v.gpu_resident ? 'Yes' : 'No'),
          )}
        </div>
      </div>
    `;
  }

  private renderPerformanceCard(node: NodeData): TemplateResult {
    return html`
      <div class="inspector-card">
        <div class="card-title">Execution Performance</div>
        <div class="specs-grid">
          ${when(this.showTimings, () =>
            this.gridRow(
              'Processing Time',
              formatProcessingTime(node.processing_time_ns, this.timingUnit),
            ),
          )}
          ${this.gridRow('Concurrency Mode', node.filter_mode_name)}
        </div>
      </div>
    `;
  }

  private renderCallStackCard(stack: CallFrame[]): TemplateResult {
    const title = `Call Stack (${stack.length} frame${stack.length === 1 ? '' : 's'})`;

    if (stack.length === 0) {
      return html`
        <div class="inspector-card">
          <div class="card-title">${title}</div>
          <div class="card-empty">No call stack information recorded.</div>
        </div>
      `;
    }

    return html`
      <div class="inspector-card">
        <div class="card-title">${title}</div>
        <div class="accordion">
          ${repeat(
            stack,
            (frame) => frame.level,
            (frame, idx) => this.renderCallFrame(frame, idx),
          )}
        </div>
      </div>
    `;
  }

  private renderCallFrame(frame: CallFrame, idx: number): TemplateResult {
    const isActive = this.openFrames.has(idx);

    return html`
      <div class=${classMap({ 'accordion-item': true, active: isActive })}>
        <div class="accordion-header-row">
          <button class="accordion-header" @click=${() => this.toggleFrame(idx)}>
            <span class="accordion-arrow">${isActive ? '▼' : '▶'}</span>
            <span class="accordion-frame-lvl">#${frame.level}</span>
            <span class="accordion-frame-func">${frame.plugin_ns}.${frame.function} </span>
            <span class="accordion-plugin-id">${frame.plugin_id}</span>
          </button>
          <button
            class="card-action-btn accordion-copy-btn"
            data-frame-idx=${idx}
            title="Copy this frame's filter call as Python"
            @click=${(e: Event) => {
              e.stopPropagation();
              this.copyFilterCall(idx);
            }}
          >
            Copy Call
          </button>
        </div>
        <div class="accordion-body">${this.renderArgumentsTable(frame.arguments)}</div>
      </div>
    `;
  }

  private renderArgumentsTable(args: Record<string, unknown>): TemplateResult {
    const entries = Object.entries(args);

    if (entries.length === 0) {
      return html`<div class="no-args">No arguments</div>`;
    }

    return html`
      <table class="args-table">
        <thead>
          <tr>
            <th>Argument</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          ${repeat(
            entries,
            ([key]) => key,
            ([key, val]) => this.renderArgumentRow(key, val),
          )}
        </tbody>
      </table>
    `;
  }

  private renderArgumentRow(key: string, val: unknown): TemplateResult {
    return html`
      <tr>
        <td class="arg-key">${key}</td>
        <td class="arg-val">${this.renderArgumentValue(key, val)}</td>
      </tr>
    `;
  }

  private renderArgumentValue(key: string, val: unknown): TemplateResult | string {
    if (isNodeRef(val)) {
      return this.renderClipBadge(String(val.id));
    }

    if (isFrameRef(val)) {
      return html`<span class="badge badge-mono">Frame [${String(val.id)}]</span>`;
    }

    if (isFuncRef(val)) {
      return html`<span class="badge badge-mono">Func [${String(val.id)}]</span>`;
    }

    if (Array.isArray(val)) {
      if (val.length === 0) return '[]';
      if (val.some((item) => isNodeRef(item) || isFrameRef(item) || isFuncRef(item))) {
        return html`
          <div class="badge-list">
            ${val.map((item, idx) => html`${this.renderArgumentValue(`${key}[${idx}]`, item)}`)}
          </div>
        `;
      }
    }

    return this.formatArgumentValue(key, val);
  }

  private renderClipBadge(nodeId: string): TemplateResult {
    const resolved = this.opts.resolveNode(nodeId);
    const nodeName = resolved?.name ?? 'Clip';

    return html`
      <button
        type="button"
        class="badge badge-mono badge-clickable"
        title="Navigate to node ${nodeId}"
        @click=${(e: Event) => {
          e.stopPropagation();
          this.opts.onFocusNode(nodeId);
        }}
      >
        ${nodeName} <span class="arg-val-id">[${nodeId}]</span>
      </button>
    `;
  }

  private formatArgumentValue(key: string, val: unknown): string {
    if (typeof val !== 'object' || val === null) {
      return String(val);
    }

    return Result.fromThrowable(() => JSON.stringify(val))
      .mapErr((err) => {
        console.warn(`Failed to serialize argument '${key}':`, err);
        return '[unserializable]';
      })
      .unwrap();
  }
}

function isNodeRef(val: unknown): val is { id: string | number; type: 'vnode' } {
  return (
    typeof val === 'object' &&
    val !== null &&
    'type' in val &&
    (val as { type: string }).type === 'vnode' &&
    'id' in val
  );
}

function isFrameRef(val: unknown): val is { id: string | number; type: 'vframe' } {
  return (
    typeof val === 'object' &&
    val !== null &&
    'type' in val &&
    (val as { type: string }).type === 'vframe' &&
    'id' in val
  );
}

function isFuncRef(val: unknown): val is { id: string | number; type: 'func' } {
  return (
    typeof val === 'object' &&
    val !== null &&
    'type' in val &&
    (val as { type: string }).type === 'func' &&
    'id' in val
  );
}

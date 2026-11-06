import type { ThemeTokens } from './utils/theme';

/** Layout orientation used by the dagre layout. */
export type LayoutDirection = 'TB' | 'LR';
/** Layout algorithm supported by the canvas. */
export type LayoutKind = 'dagre' | 'breadthfirst' | 'concentric' | 'grid';
/** Text formats produced by `vsgraph`, plus the canvas-rendered PNG. */
export type ExportFormat = 'dot' | 'mermaid' | 'ascii' | 'json' | 'png';
/** Unit used when rendering node processing times. */
export type TimingUnit = 'auto' | 'ms' | 'ns';
/** Target field copied to the system clipboard for an inspected node. */
export type NodeCopyTarget = 'id' | 'call' | 'report';
/** Filter mode identifier matching VapourSynth's `vs.FilterMode`. */
export type FilterModeName = 'PARALLEL' | 'PARALLEL_REQUESTS' | 'UNORDERED' | 'FRAME_STATE';

/** VapourSynth node request pattern. */
export type RequestPatternName =
  | 'GENERAL'
  | 'NO_FRAME_REUSE'
  | 'STRICT_SPATIAL'
  | 'FRAME_REUSE_LAST_ONLY';

type WarningCode = 'framestate' | 'unordered' | 'no-frame-reuse' | 'strict-spatial';
type WarningSeverity = 'warning' | 'info';
type MediaField = 'format' | 'resolution' | 'fps' | 'frames';

interface CountedName {
  name: string;
  count: number;
}

export interface WarningGroup {
  readonly code: WarningCode;
  readonly severity: WarningSeverity;
  readonly title: string;
  readonly description: string;
  readonly nodeIds: readonly string[];
  readonly count: number;
}

interface MediaDelta {
  fields: MediaField[];
  label: string;
}

interface MediaTransition {
  edgeId: string;
  sourceId: string;
  targetId: string;
  delta: MediaDelta;
}

export interface TransitionGroup {
  readonly signature: string;
  readonly fields: readonly MediaField[];
  readonly transitions: readonly MediaTransition[];
  readonly targetNodeIds: readonly string[];
}

export interface GraphAnalysisPayload {
  nodeCount: number;
  edgeCount: number;
  depth: number;
  modes: CountedName[];
  patterns: CountedName[];
  formats: CountedName[];
  warningCount: number;
  warningGroups: WarningGroup[];
  transitionCount: number;
  transitionGroups: TransitionGroup[];
}

export interface VideoFormatInfo {
  id: number;
  name: string;
  color_family: number;
  sample_type: number;
  bits_per_sample: number;
  bytes_per_sample: number;
  subsampling_w: number;
  subsampling_h: number;
  num_planes: number;
}

export interface MediaInfo {
  format_id: number;
  format: VideoFormatInfo;
  width: number;
  height: number;
  fps_num: number;
  fps_den: number;
  num_frames: number;
  gpu_resident?: boolean;
  type: 'vnode';
}

export interface VideoNodeRef {
  id: string;
  type: 'vnode';
}

export interface VideoFrameRef {
  id: string;
  type: 'vframe';
}

export interface FuncRef {
  id: string;
  type: 'func';
}

export interface CallFrame {
  level: number;
  frame_id: string;
  plugin_id: string;
  plugin_ns: string;
  function: string;
  arguments: Record<string, unknown>;
}

export interface NodeData {
  id: string;
  label: string;
  name: string;
  filter_mode: number;
  filter_mode_name: FilterModeName;
  processing_time_ns: number;
  processing_time_ms: number;
  is_root: boolean;
  media_info: MediaInfo;
  call_stack: CallFrame[];
}

export interface CanvasNodeData extends NodeData {
  source_label: string;
  node_width: number;
  node_height: number;
}

export interface EdgeData {
  id: string;
  source: string;
  target: string;
  pattern: number;
  pattern_name: RequestPatternName;
  label: string;
  index: number;
  media_label?: string;
  media_fields?: MediaField[];
}

export interface IncomingConnection {
  edge: EdgeData;
  source: CanvasNodeData;
}

export interface CytoscapeNodeElement {
  data: NodeData;
}

export interface CytoscapeEdgeElement {
  data: EdgeData;
}

export interface CytoscapePayload {
  nodes: CytoscapeNodeElement[];
  edges: CytoscapeEdgeElement[];
  analysis: GraphAnalysisPayload;
}

export interface OutputItem {
  index: number;
  name: string;
  isActive: boolean;
}

export interface SetOutputsPayload {
  outputs: OutputItem[];
}

export interface GraphOptionsPayload {
  direction: LayoutDirection;
  layout: LayoutKind;
  rankSep: number;
  nodeSep: number;
  heatmap: boolean;
  showTimings: boolean;
  timingUnit: TimingUnit;
  autoFitOnReload: boolean;
}

export interface NodeTimingsUpdate {
  id: string;
  processing_time_ns: number;
  processing_time_ms: number;
}

export interface UpdateTimingsPayload {
  timings: NodeTimingsUpdate[];
}

export interface SelectNodePayload {
  id: string;
}

export interface KeybindingRule {
  action: string;
  key: string;
  ctrl: boolean;
  shift: boolean;
  alt: boolean;
  meta: boolean;
}

export interface SetKeybindingsPayload {
  keybindings: KeybindingRule[];
}

export interface ExecActionPayload {
  action: string;
  cycle?: boolean;
}

/**
 * Inbound command mapping (Python -> JavaScript)
 */
export interface PyToJsCommandMap {
  'graph.setData': CytoscapePayload;
  'graph.setOptions': GraphOptionsPayload;
  'graph.updateTimings': UpdateTimingsPayload;
  'graph.setOutputs': SetOutputsPayload;
  'graph.selectNode': SelectNodePayload;
  'theme.update': ThemeTokens;
  'graph.setKeybindings': SetKeybindingsPayload;
  'graph.fitView': Record<string, never>;
  'graph.resetView': Record<string, never>;
  'graph.requestPngExport':
    | {
        destination: 'clipboard';
      }
    | {
        destination: 'file';
        path: string;
      };
}

/**
 * Outbound command mapping (JavaScript -> Python)
 */
export interface JsToPyCommandMap {
  'graph.reload': Record<string, never>;
  'output.select': { index: number };
  'node.select': { id: string };
  'node.deselect': Record<string, never>;
  'node.copy': { id: string; target: NodeCopyTarget; frameIdx?: number };
  'graph.exportGraph': { format: 'dot' | 'mermaid' | 'ascii' | 'json'; clipboard: boolean };
  'graph.saveImage': {
    format: 'png';
    dataBase64: string;
    destination: 'file' | 'clipboard';
    path?: string;
  };
  'contextmenu.request': Record<string, never>;
}

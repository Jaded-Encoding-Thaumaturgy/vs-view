import type {
  CanvasNodeData,
  EdgeData,
  GraphAnalysisPayload,
  MediaInfo,
  NodeData,
  VideoFormatInfo,
} from '../types';

export function makeVideoFormat(overrides: Partial<VideoFormatInfo> = {}): VideoFormatInfo {
  return {
    id: 1,
    name: 'yuv420p8',
    color_family: 1,
    sample_type: 0,
    bits_per_sample: 8,
    bytes_per_sample: 1,
    subsampling_w: 1,
    subsampling_h: 1,
    num_planes: 3,
    ...overrides,
  };
}

export type MediaInfoOverrides = Omit<Partial<MediaInfo>, 'format'> & {
  format?: Partial<VideoFormatInfo>;
};

export function makeVideoInfo(overrides: MediaInfoOverrides = {}): MediaInfo {
  const { format, ...rest } = overrides;
  return {
    type: 'vnode',
    format_id: format?.id ?? 1,
    format: makeVideoFormat(format),
    width: 1920,
    height: 1080,
    fps_num: 24000,
    fps_den: 1001,
    num_frames: 1000,
    ...rest,
  };
}

export function makeNode(overrides: Partial<NodeData> = {}): NodeData {
  return {
    id: 'node_1',
    label: 'BlankClip',
    name: 'BlankClip',
    filter_mode: 0,
    filter_mode_name: 'PARALLEL',
    processing_time_ns: 1_000_000,
    processing_time_ms: 1,
    is_root: false,
    depth: 0,
    media_info: makeVideoInfo(),
    call_stack: [
      {
        level: 0,
        id: 'frame_1',
        plugin_id: 'com.vapoursynth.std',
        plugin_ns: 'std',
        function: 'BlankClip',
        arguments: {},
      },
    ],
    ...overrides,
  };
}

export function makeEdge(overrides: Partial<EdgeData> = {}): EdgeData {
  return {
    id: 'e_0',
    source: 'node_1',
    target: 'node_2',
    pattern: 0,
    pattern_name: 'GENERAL',
    label: '',
    index: 0,
    ...overrides,
  };
}

export function makeCanvasNode(overrides: Partial<CanvasNodeData> = {}): CanvasNodeData {
  const base = makeNode(overrides);
  return {
    ...base,
    source_label: overrides.source_label ?? base.label,
    node_width: overrides.node_width ?? 100,
    node_height: overrides.node_height ?? 50,
  };
}

export function makeCanvasEdge(overrides: Partial<EdgeData> = {}): EdgeData {
  return {
    ...makeEdge(overrides),
    ...overrides,
  };
}

export function makeGraphAnalysis(
  overrides: Partial<GraphAnalysisPayload> = {},
): GraphAnalysisPayload {
  return {
    nodeCount: 0,
    edgeCount: 0,
    depth: 0,
    modes: [],
    patterns: [],
    formats: [],
    warningCount: 0,
    warningGroups: [],
    transitionCount: 0,
    transitionGroups: [],
    ...overrides,
  };
}

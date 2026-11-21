import type { BasePythonBridge } from 'vsview-shared';

/**
 * Interface for the Python object exposed via QWebChannel.
 * This should match the MonacoBridge class in web.py.
 */
export interface PythonBridge extends BasePythonBridge {
  /** Get stat metadata for a file on disk. Answers a `StatFileResponse`, to be validated. */
  statFile(filepath: string, callback: (res: unknown) => void): void;

  /** Read content of a file on disk. Answers a `ReadFileResponse`, to be validated. */
  readFile(filepath: string, callback: (res: unknown) => void): void;
}

export interface FileStatResponse {
  type: number; // 1 = File, 2 = Directory
  ctime: number;
  mtime: number;
  size: number;
}

/** Interface for tab state metadata. */
export interface TabInfo {
  uri: string;
  title: string;
  isMain: boolean;
  isDirty: boolean;
  language: string;
  /** Sent only when the tab's version changed since the last message, always with `versionId`. */
  content?: string | undefined;
  versionId?: number | undefined;
}

/** The same `versionId` always means the same text of that tab, even after the tab gets a new model. */
export interface TabSnapshot {
  uri: string;
  versionId: number;
  content: string;
}

export interface EditorOptionsPayload {
  'editor.fontSize'?: number;
  'editor.fontFamily'?: string;
  'editor.tabSize'?: number;
  'editor.insertSpaces'?: boolean;
  'editor.wordWrap'?: 'off' | 'on' | 'wordWrapColumn' | 'bounded';
  'editor.lineNumbers'?: 'on' | 'off' | 'relative' | 'interval';
  'editor.minimap.enabled'?: boolean;
  'editor.renderWhitespace'?: 'none' | 'boundary' | 'selection' | 'trailing' | 'all';
  'editor.cursorBlinking'?: 'blink' | 'smooth' | 'phase' | 'expand' | 'solid';
  'editor.bracketPairColorization.enabled'?: boolean;
}

export interface LspConnectPayload {
  id: string;
  name: string;
  port: number;
  path: string;
  language: string;
  configurationSection?: string | string[];
  progressNotifications?: Array<{ begin: string; end: string }>;
}

/** Commands dispatched from Python to JavaScript. */
export interface PyToJsCommandMap {
  'editor.applyFormat': { uri: string; versionId: number; text: string };
  'editor.setTheme': { theme: string };
  'editor.toggleWordWrap': Record<string, never>;
  'editor.updateLspSettings': { section: string; settingsJson: string };
  'editor.updateOptions': { optionsJson: string };
  'editor.openTab': {
    uri: string;
    content: string;
    language?: string;
    isMain?: boolean;
    replaceUri?: string;
  };
  'editor.selectTab': { uri: string };
  'editor.tabSaved': { uri: string; oldUri: string; versionId?: number };
  'editor.triggerSave': Record<string, never>;
  'editor.triggerSaveAs': Record<string, never>;
  'editor.triggerFormat': Record<string, never>;
  'editor.triggerRun': Record<string, never>;
  'console.append': { stream?: string; text: string };
  'console.toggle': { state?: boolean };
  'lsp.connect': LspConnectPayload;
  'lsp.disconnect': { id?: string };
}

/** Commands dispatched from JavaScript to Python. */
export interface JsToPyCommandMap {
  'editor.mainContentChanged': TabSnapshot;
  'editor.cursorPositionChanged': { line: number; column: number };
  'editor.activeTabChanged': { uri: string };
  'editor.mainTabChanged': { uri: string };
  'editor.tabStateChanged': { tabs: TabInfo[] };
  'editor.saveRequested': TabSnapshot;
  'editor.saveAsRequested': TabSnapshot;
  'editor.formatRequested': TabSnapshot;
  'editor.runRequested': Record<string, never>;
  'editor.generateStubsRequested': Record<string, never>;
  'lsp.restartRequested': Record<string, never>;
  'console.resized': { cols: number };
  'clipboard.copy': { text: string };
}

/**
 * TypeScript definitions for the Python bridge and global objects.
 */

/**
 * Interface for the Python object exposed via QWebChannel.
 * This should match the MonacoBridge class in web.py.
 */
export interface PythonBridge {
  // Slots (Python methods callable from JS)

  /** Notify Python that the Monaco editor is fully initialized. */
  onEditorReady(): void;

  /** Send current editor content to Python. */
  onContentChanged(content: string): void;

  /** Send current Main script content to Python. */
  onMainContentChanged(mainContent: string): void;

  /** Notify Python of cursor position change. */
  onCursorPositionChanged(line: number, column: number): void;

  /** Notify Python that the active tab's URI has changed. */
  onActiveTabChanged(uri: string): void;

  /** Request Python to save the active document. */
  requestSave(): void;

  /** Request Python to save the active document as a new file. */
  requestSaveAs(): void;

  /** Request Python to format the active document. */
  requestFormat(): void;

  /** Request Python to regenerate VapourSynth stubs. */
  requestGenerateStubs(): void;

  /** Notify Python that console viewport width has changed (cols). */
  onConsoleResized(cols: number): void;

  /** Copy text to system clipboard via Python Qt host. */
  copyToClipboard(text: string): void;

  /** Get stat metadata for a file on disk. */
  statFile(filepath: string, callback: (res: FileStatResponse | null) => void): void;

  /** Read content of a file on disk. */
  readFile(filepath: string, callback: (res: string | null) => void): void;

  // Signals (Python signals we connect to in JS)

  /** Generic command dispatch signal emitted by Python (command_id, json_payload). */
  dispatchCommandSignal: QWebSignal<(command: string, payloadJson: string) => void>;
}

/** Utility type for Qt's QWebChannel signals. */
export interface QWebSignal<T extends (...args: never[]) => void> {
  connect(callback: T): void;
  disconnect(callback: T): void;
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
}

export interface EditorOptionsPayload {
  "editor.fontSize"?: number;
  "editor.fontFamily"?: string;
  "editor.tabSize"?: number;
  "editor.insertSpaces"?: boolean;
  "editor.wordWrap"?: "off" | "on" | "wordWrapColumn" | "bounded";
  "editor.lineNumbers"?: "on" | "off" | "relative" | "interval";
  "editor.minimap.enabled"?: boolean;
  "editor.renderWhitespace"?: "none" | "boundary" | "selection" | "trailing" | "all";
  "editor.cursorBlinking"?: "blink" | "smooth" | "phase" | "expand" | "solid";
  "editor.bracketPairColorization.enabled"?: boolean;
}

/** Global window extensions for Qt and vsview. */
declare global {
  interface Window {
    /** Injected environment variables via QWebEngineScript. */
    ENV?: {
      VSVIEW_DEBUG?: boolean;
      [key: string]: unknown;
    };

    /** Injected by QWebEngineView when a web channel is present. */
    qt: {
      webChannelTransport: unknown;
    };

    /** The QWebChannel constructor, loaded via <script> tag. */
    QWebChannel: new (
      transport: unknown,
      callback: (channel: { objects: Record<string, unknown> }) => void,
    ) => void;
  }
}

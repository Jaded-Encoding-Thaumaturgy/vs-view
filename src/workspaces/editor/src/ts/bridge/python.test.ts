import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { MockEventEmitter, mockWorkspaceConfig } = vi.hoisted(() => {
  class MockEventEmitter<T> {
    private listeners: Array<(e: T) => void> = [];

    public event = (listener: (e: T) => void) => {
      this.listeners.push(listener);
      return {
        dispose: () => {
          const idx = this.listeners.indexOf(listener);
          if (idx !== -1) this.listeners.splice(idx, 1);
        },
      };
    };

    public fire = (data: T): void => {
      for (const l of [...this.listeners]) {
        l(data);
      }
    };

    public dispose = (): void => {
      this.listeners = [];
    };
  }

  const mockWorkspaceConfig = {
    update: vi.fn().mockResolvedValue(undefined),
    get: vi.fn(),
  };

  return { MockEventEmitter, mockWorkspaceConfig };
});

vi.mock("vscode", () => {
  return {
    EventEmitter: MockEventEmitter,
    ConfigurationTarget: {
      Workspace: 2,
    },
    workspace: {
      getConfiguration: vi.fn(() => mockWorkspaceConfig),
    },
  };
});

import type { EditorService } from "../editor/service";
import type { LspService } from "../lsp/service";
import type { FileStatResponse, PythonBridge, QWebSignal } from "../types";
import type { ConsolePanelService } from "../ui/console";
import { DOM_IDS } from "../ui/constants";
import { BridgeService } from "./python";

interface MockEditorServiceFixture {
  service: EditorService;
  getValue: ReturnType<typeof vi.fn>;
  getMainValue: ReturnType<typeof vi.fn>;
  setValue: ReturnType<typeof vi.fn>;
  setTheme: ReturnType<typeof vi.fn>;
  setFontSize: ReturnType<typeof vi.fn>;
  setLanguage: ReturnType<typeof vi.fn>;
  toggleWordWrap: ReturnType<typeof vi.fn>;
  openTab: ReturnType<typeof vi.fn>;
  closeTab: ReturnType<typeof vi.fn>;
  selectTab: ReturnType<typeof vi.fn>;
  setMainTab: ReturnType<typeof vi.fn>;
  markTabSaved: ReturnType<typeof vi.fn>;
  requestSave: ReturnType<typeof vi.fn>;
  requestSaveAs: ReturnType<typeof vi.fn>;
  requestFormat: ReturnType<typeof vi.fn>;
  updateOptionsFromMap: ReturnType<typeof vi.fn>;
  layout: ReturnType<typeof vi.fn>;
  emitContentChange: () => void;
  emitActiveTabChange: (uri: string | null) => void;
  emitMainTabChange: (uri: string | null) => void;
  emitCursorPositionChange: (line: number, column: number) => void;
  emitSaveRequest: () => void;
  emitSaveAsRequest: () => void;
  emitFormatRequest: () => void;
}

function createMockEditorService(): MockEditorServiceFixture {
  const contentChangeEmitter = new MockEventEmitter<void>();
  const activeTabChangeEmitter = new MockEventEmitter<string | null>();
  const mainTabChangeEmitter = new MockEventEmitter<string | null>();
  const cursorPositionChangeEmitter = new MockEventEmitter<{
    position: { lineNumber: number; column: number };
  }>();
  const saveRequestEmitter = new MockEventEmitter<void>();
  const saveAsRequestEmitter = new MockEventEmitter<void>();
  const formatRequestEmitter = new MockEventEmitter<void>();

  const getValue = vi.fn().mockReturnValue("active content");
  const getMainValue = vi.fn().mockReturnValue("main content");
  const setValue = vi.fn();
  const setTheme = vi.fn();
  const setFontSize = vi.fn();
  const setLanguage = vi.fn();
  const toggleWordWrap = vi.fn();
  const openTab = vi.fn();
  const closeTab = vi.fn();
  const selectTab = vi.fn();
  const setMainTab = vi.fn();
  const markTabSaved = vi.fn();
  const requestSave = vi.fn();
  const requestSaveAs = vi.fn();
  const requestFormat = vi.fn();
  const updateOptionsFromMap = vi.fn();
  const layout = vi.fn();

  const service = {
    getValue,
    getMainValue,
    setValue,
    setTheme,
    setFontSize,
    setLanguage,
    toggleWordWrap,
    openTab,
    closeTab,
    selectTab,
    setMainTab,
    markTabSaved,
    requestSave,
    requestSaveAs,
    requestFormat,
    updateOptionsFromMap,
    layout,
    onDidChangeContent: vi.fn((cb: () => void) => contentChangeEmitter.event(cb)),
    onDidChangeActiveTab: vi.fn((cb: (uri: string | null) => void) =>
      activeTabChangeEmitter.event(cb),
    ),
    onDidChangeMainTab: vi.fn((cb: (uri: string | null) => void) => mainTabChangeEmitter.event(cb)),
    onDidChangeCursorPosition: vi.fn(
      (cb: (e: { position: { lineNumber: number; column: number } }) => void) =>
        cursorPositionChangeEmitter.event(cb),
    ),
    onDidRequestSave: vi.fn((cb: () => void) => saveRequestEmitter.event(cb)),
    onDidRequestSaveAs: vi.fn((cb: () => void) => saveAsRequestEmitter.event(cb)),
    onDidRequestFormat: vi.fn((cb: () => void) => formatRequestEmitter.event(cb)),
    dispose: vi.fn(),
  } as unknown as EditorService;

  return {
    service,
    getValue,
    getMainValue,
    setValue,
    setTheme,
    setFontSize,
    setLanguage,
    toggleWordWrap,
    openTab,
    closeTab,
    selectTab,
    setMainTab,
    markTabSaved,
    requestSave,
    requestSaveAs,
    requestFormat,
    updateOptionsFromMap,
    layout,
    emitContentChange: () => contentChangeEmitter.fire(undefined),
    emitActiveTabChange: (uri: string | null) => activeTabChangeEmitter.fire(uri),
    emitMainTabChange: (uri: string | null) => mainTabChangeEmitter.fire(uri),
    emitCursorPositionChange: (line: number, column: number) =>
      cursorPositionChangeEmitter.fire({ position: { lineNumber: line, column } }),
    emitSaveRequest: () => saveRequestEmitter.fire(undefined),
    emitSaveAsRequest: () => saveAsRequestEmitter.fire(undefined),
    emitFormatRequest: () => formatRequestEmitter.fire(undefined),
  };
}

interface MockConsoleServiceFixture {
  service: ConsolePanelService;
  append: ReturnType<typeof vi.fn>;
  toggle: ReturnType<typeof vi.fn>;
  clear: ReturnType<typeof vi.fn>;
  setTheme: ReturnType<typeof vi.fn>;
  emitToggle: (visible: boolean) => void;
  emitResize: (cols: number) => void;
}

function createMockConsoleService(): MockConsoleServiceFixture {
  const toggleEmitter = new MockEventEmitter<boolean>();
  const resizeEmitter = new MockEventEmitter<number>();

  const append = vi.fn();
  const toggle = vi.fn();
  const clear = vi.fn();
  const setTheme = vi.fn();

  const service = {
    append,
    toggle,
    clear,
    setTheme,
    onDidToggle: vi.fn((cb: (visible: boolean) => void) => toggleEmitter.event(cb)),
    onDidResize: vi.fn((cb: (cols: number) => void) => resizeEmitter.event(cb)),
    dispose: vi.fn(),
  } as unknown as ConsolePanelService;

  return {
    service,
    append,
    toggle,
    clear,
    setTheme,
    emitToggle: (visible: boolean) => toggleEmitter.fire(visible),
    emitResize: (cols: number) => resizeEmitter.fire(cols),
  };
}

interface MockLspServiceFixture {
  service: LspService;
  connect: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
}

function createMockLspService(): MockLspServiceFixture {
  const connect = vi.fn().mockResolvedValue(undefined);
  const disconnect = vi.fn().mockResolvedValue(undefined);

  const service = {
    connect,
    disconnect,
    dispose: vi.fn(),
  } as unknown as LspService;

  return {
    service,
    connect,
    disconnect,
  };
}

interface MockPythonBridgeFixture {
  bridge: PythonBridge;
  signalCallbacks: Array<(command: string, payloadJson: string) => void>;
  emitCommand: (command: string, payloadJson: string) => void;
}

function createMockPythonBridge(): MockPythonBridgeFixture {
  const signalCallbacks: Array<(command: string, payloadJson: string) => void> = [];

  const dispatchCommandSignal: QWebSignal<(command: string, payloadJson: string) => void> = {
    connect: vi.fn((cb: (command: string, payloadJson: string) => void) => {
      signalCallbacks.push(cb);
    }),
    disconnect: vi.fn((cb: (command: string, payloadJson: string) => void) => {
      const idx = signalCallbacks.indexOf(cb);
      if (idx !== -1) signalCallbacks.splice(idx, 1);
    }),
  };

  const bridge: PythonBridge = {
    onEditorReady: vi.fn(),
    onContentChanged: vi.fn(),
    onMainContentChanged: vi.fn(),
    onCursorPositionChanged: vi.fn(),
    onActiveTabChanged: vi.fn(),
    onTabStateChanged: vi.fn(),
    onMainTabChanged: vi.fn(),
    requestSave: vi.fn(),
    requestSaveAs: vi.fn(),
    requestFormat: vi.fn(),
    requestGenerateStubs: vi.fn(),
    requestRestartLsp: vi.fn(),
    onConsoleResized: vi.fn(),
    copyToClipboard: vi.fn(),
    statFile: vi.fn(),
    readFile: vi.fn(),
    dispatchCommandSignal,
  };

  return {
    bridge,
    signalCallbacks,
    emitCommand: (command: string, payloadJson: string) => {
      for (const cb of [...signalCallbacks]) {
        cb(command, payloadJson);
      }
    },
  };
}

describe("BridgeService", () => {
  let mockEditor: MockEditorServiceFixture;
  let mockConsole: MockConsoleServiceFixture;
  let mockLsp: MockLspServiceFixture;
  let mockBridgeFixture: MockPythonBridgeFixture;
  let overlayEl: HTMLElement;

  beforeEach(() => {
    mockEditor = createMockEditorService();
    mockConsole = createMockConsoleService();
    mockLsp = createMockLspService();
    mockBridgeFixture = createMockPythonBridge();

    document.body.innerHTML = "";
    overlayEl = document.createElement("div");
    overlayEl.id = DOM_IDS.LOADING_OVERLAY;
    document.body.appendChild(overlayEl);

    class MockQWebChannel {
      constructor(
        _transport: unknown,
        callback: (channel: { objects: Record<string, unknown> }) => void,
      ) {
        callback({ objects: { bridge: mockBridgeFixture.bridge } });
      }
    }

    Object.assign(window, {
      qt: {
        webChannelTransport: {},
      },
      QWebChannel: MockQWebChannel,
    });

    mockWorkspaceConfig.update.mockClear();
    mockWorkspaceConfig.get.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function setupInitializedBridgeService(): {
    bridgeService: BridgeService;
    bridge: PythonBridge;
    emitCommand: (cmd: string, json: string) => void;
  } {
    const bridgeService = new BridgeService(
      mockEditor.service,
      mockLsp.service,
      mockConsole.service,
    );
    bridgeService.initBridge();
    return {
      bridgeService,
      bridge: mockBridgeFixture.bridge,
      emitCommand: mockBridgeFixture.emitCommand,
    };
  }

  describe("1. Command Bus Dispatching", () => {
    it("dispatches editor.setValue", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("editor.setValue", JSON.stringify({ text: "print('hello')" }));
      expect(mockEditor.setValue).toHaveBeenCalledWith("print('hello')");

      // Non-string payload ignored
      mockEditor.setValue.mockClear();
      mockBridgeFixture.emitCommand("editor.setValue", JSON.stringify({ text: 123 }));
      expect(mockEditor.setValue).not.toHaveBeenCalled();
    });

    it("dispatches editor.setTheme to both editor and console services", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("editor.setTheme", JSON.stringify({ theme: "vs-dark" }));
      expect(mockEditor.setTheme).toHaveBeenCalledWith("vs-dark");
      expect(mockConsole.setTheme).toHaveBeenCalledWith("vs-dark");

      // Non-string theme ignored
      mockEditor.setTheme.mockClear();
      mockConsole.setTheme.mockClear();
      mockBridgeFixture.emitCommand("editor.setTheme", JSON.stringify({ theme: null }));
      expect(mockEditor.setTheme).not.toHaveBeenCalled();
      expect(mockConsole.setTheme).not.toHaveBeenCalled();
    });

    it("dispatches editor.setFontSize", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("editor.setFontSize", JSON.stringify({ size: 16 }));
      expect(mockEditor.setFontSize).toHaveBeenCalledWith(16);

      // Non-number size ignored
      mockEditor.setFontSize.mockClear();
      mockBridgeFixture.emitCommand("editor.setFontSize", JSON.stringify({ size: "16" }));
      expect(mockEditor.setFontSize).not.toHaveBeenCalled();
    });

    it("dispatches editor.setLanguage", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("editor.setLanguage", JSON.stringify({ lang: "python" }));
      expect(mockEditor.setLanguage).toHaveBeenCalledWith("python");

      // Non-string language ignored
      mockEditor.setLanguage.mockClear();
      mockBridgeFixture.emitCommand("editor.setLanguage", JSON.stringify({ lang: true }));
      expect(mockEditor.setLanguage).not.toHaveBeenCalled();
    });

    it("dispatches editor.toggleWordWrap", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("editor.toggleWordWrap", "{}");
      expect(mockEditor.toggleWordWrap).toHaveBeenCalledOnce();
    });

    it("dispatches editor.openTab with explicit and default arguments", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand(
        "editor.openTab",
        JSON.stringify({
          uri: "file:///script.py",
          content: "import vapoursynth as vs",
          language: "python",
          isMain: true,
        }),
      );
      expect(mockEditor.openTab).toHaveBeenCalledWith(
        "file:///script.py",
        "import vapoursynth as vs",
        "python",
        true,
      );

      // Default values when optional params omitted
      mockEditor.openTab.mockClear();
      mockBridgeFixture.emitCommand(
        "editor.openTab",
        JSON.stringify({
          uri: "file:///sub.py",
          content: "x = 1",
        }),
      );
      expect(mockEditor.openTab).toHaveBeenCalledWith("file:///sub.py", "x = 1", "python", false);

      // Missing required params ignored
      mockEditor.openTab.mockClear();
      mockBridgeFixture.emitCommand(
        "editor.openTab",
        JSON.stringify({ uri: "file:///script.py" }), // missing content
      );
      expect(mockEditor.openTab).not.toHaveBeenCalled();
    });

    it("dispatches editor.closeTab and editor.selectTab", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand(
        "editor.closeTab",
        JSON.stringify({ uri: "file:///script.py" }),
      );
      expect(mockEditor.closeTab).toHaveBeenCalledWith("file:///script.py");

      mockBridgeFixture.emitCommand(
        "editor.selectTab",
        JSON.stringify({ uri: "file:///script.py" }),
      );
      expect(mockEditor.selectTab).toHaveBeenCalledWith("file:///script.py");

      // Non-string uri ignored
      mockEditor.closeTab.mockClear();
      mockEditor.selectTab.mockClear();
      mockBridgeFixture.emitCommand("editor.closeTab", JSON.stringify({ uri: 123 }));
      mockBridgeFixture.emitCommand("editor.selectTab", JSON.stringify({}));
      expect(mockEditor.closeTab).not.toHaveBeenCalled();
      expect(mockEditor.selectTab).not.toHaveBeenCalled();
    });

    it("dispatches editor.setMainTab and immediately flushes content", () => {
      const { bridgeService, bridge } = setupInitializedBridgeService();
      using fixture = bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand(
        "editor.setMainTab",
        JSON.stringify({ uri: "file:///main.py" }),
      );
      expect(mockEditor.setMainTab).toHaveBeenCalledWith("file:///main.py");
      expect(bridge.onContentChanged).toHaveBeenCalledWith("active content");
      expect(bridge.onMainContentChanged).toHaveBeenCalledWith("main content");
    });

    it("dispatches editor.tabSaved with and without oldUri", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand(
        "editor.tabSaved",
        JSON.stringify({ uri: "file:///new.py", oldUri: "file:///old.py" }),
      );
      expect(mockEditor.markTabSaved).toHaveBeenCalledWith("file:///new.py", "file:///old.py");

      mockEditor.markTabSaved.mockClear();
      mockBridgeFixture.emitCommand("editor.tabSaved", JSON.stringify({ uri: "file:///saved.py" }));
      expect(mockEditor.markTabSaved).toHaveBeenCalledWith("file:///saved.py", undefined);

      // Missing uri ignored
      mockEditor.markTabSaved.mockClear();
      mockBridgeFixture.emitCommand(
        "editor.tabSaved",
        JSON.stringify({ oldUri: "file:///old.py" }),
      );
      expect(mockEditor.markTabSaved).not.toHaveBeenCalled();
    });

    it("dispatches editor.triggerSave, editor.triggerSaveAs, editor.triggerFormat with flush", () => {
      const { bridgeService, bridge } = setupInitializedBridgeService();
      using fixture = bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("editor.triggerSave", "{}");
      expect(mockEditor.requestSave).toHaveBeenCalledOnce();
      expect(bridge.onContentChanged).toHaveBeenCalledWith("active content");

      mockBridgeFixture.emitCommand("editor.triggerSaveAs", "{}");
      expect(mockEditor.requestSaveAs).toHaveBeenCalledOnce();

      mockBridgeFixture.emitCommand("editor.triggerFormat", "{}");
      expect(mockEditor.requestFormat).toHaveBeenCalledOnce();
    });

    it("dispatches editor.updateOptions", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      const validOptions = { "editor.fontSize": 18, "editor.tabSize": 2 };
      mockBridgeFixture.emitCommand(
        "editor.updateOptions",
        JSON.stringify({ optionsJson: JSON.stringify(validOptions) }),
      );
      expect(mockEditor.updateOptionsFromMap).toHaveBeenCalledWith(validOptions);

      // Malformed optionsJson inside payload does not throw or call service
      mockEditor.updateOptionsFromMap.mockClear();
      mockBridgeFixture.emitCommand(
        "editor.updateOptions",
        JSON.stringify({ optionsJson: "{invalid_json" }),
      );
      expect(mockEditor.updateOptionsFromMap).not.toHaveBeenCalled();
    });

    it("dispatches editor.updateLspSettings and updates workspace configuration", async () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      const settings = { "basedpyright.analysis.typeCheckingMode": "strict" };
      mockBridgeFixture.emitCommand(
        "editor.updateLspSettings",
        JSON.stringify({ section: "basedpyright", settingsJson: JSON.stringify(settings) }),
      );

      // Allow async promise chain to run
      await Promise.resolve();
      await Promise.resolve();

      expect(mockWorkspaceConfig.update).toHaveBeenCalledWith(
        "basedpyright.analysis.typeCheckingMode",
        "strict",
        2,
      );
    });

    it("handles corrupted settingsJson in editor.updateLspSettings without throwing", async () => {
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand(
        "editor.updateLspSettings",
        JSON.stringify({ section: "basedpyright", settingsJson: "{bad_json" }),
      );

      await Promise.resolve();
      expect(consoleErrorSpy).toHaveBeenCalledWith(
        expect.stringContaining("Failed to parse LSP settings for section 'basedpyright'"),
        expect.any(Error),
      );
    });

    it("dispatches console commands (append, toggle, clear)", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("console.append", JSON.stringify({ text: "Hello\n" }));
      expect(mockConsole.append).toHaveBeenCalledWith("Hello\n");

      mockBridgeFixture.emitCommand("console.toggle", JSON.stringify({ state: true }));
      expect(mockConsole.toggle).toHaveBeenCalledWith(true);

      mockBridgeFixture.emitCommand("console.toggle", JSON.stringify({ state: false }));
      expect(mockConsole.toggle).toHaveBeenCalledWith(false);

      mockBridgeFixture.emitCommand("console.toggle", "{}");
      expect(mockConsole.toggle).toHaveBeenCalledWith(undefined);

      mockBridgeFixture.emitCommand("console.clear", "{}");
      expect(mockConsole.clear).toHaveBeenCalledOnce();
    });

    it("dispatches lsp.connect with complete and partial configs", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand(
        "lsp.connect",
        JSON.stringify({
          id: "basedpyright",
          name: "Basedpyright Server",
          port: 9000,
          language: "python",
          fileEventsPattern: "**/*.py",
          configurationSection: "basedpyright",
          progressNotifications: [{ begin: "startProgress", end: "endProgress" }],
        }),
      );

      expect(mockLsp.connect).toHaveBeenCalledWith({
        id: "basedpyright",
        name: "Basedpyright Server",
        port: 9000,
        language: "python",
        fileEventsPattern: "**/*.py",
        configurationSection: "basedpyright",
        progressNotifications: [{ begin: "startProgress", end: "endProgress" }],
      });

      // With array configurationSection and missing optional fields
      mockLsp.connect.mockClear();
      mockBridgeFixture.emitCommand(
        "lsp.connect",
        JSON.stringify({
          id: "pylsp",
          name: "Python LSP",
          port: 9001,
          language: "python",
          configurationSection: ["pylsp", "python"],
        }),
      );

      expect(mockLsp.connect).toHaveBeenCalledWith({
        id: "pylsp",
        name: "Python LSP",
        port: 9001,
        language: "python",
        fileEventsPattern: undefined,
        configurationSection: ["pylsp", "python"],
        progressNotifications: undefined,
      });

      // Missing required port/id ignored
      mockLsp.connect.mockClear();
      mockBridgeFixture.emitCommand(
        "lsp.connect",
        JSON.stringify({ id: "pylsp", name: "Python LSP" }),
      );
      expect(mockLsp.connect).not.toHaveBeenCalled();
    });

    it("dispatches lsp.disconnect with and without id", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockBridgeFixture.emitCommand("lsp.disconnect", JSON.stringify({ id: "basedpyright" }));
      expect(mockLsp.disconnect).toHaveBeenCalledWith("basedpyright");

      mockBridgeFixture.emitCommand("lsp.disconnect", "{}");
      expect(mockLsp.disconnect).toHaveBeenCalledWith(undefined);
    });

    it("logs warning on unrecognized commands without throwing", () => {
      const consoleWarnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      expect(() => {
        mockBridgeFixture.emitCommand("custom.nonexistentAction", "{}");
      }).not.toThrow();

      expect(consoleWarnSpy).toHaveBeenCalledWith(
        "Unknown bridge command: custom.nonexistentAction",
      );
    });
  });

  describe("2. JSON Payload Resilience", () => {
    it("safely handles corrupted/invalid JSON strings without throwing", () => {
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      expect(() => {
        mockBridgeFixture.emitCommand("editor.setValue", "{malformed JSON syntax");
      }).not.toThrow();

      expect(consoleErrorSpy).toHaveBeenCalledWith("Error parsing the JSON", expect.any(Error));
      // Handlers receive empty payload {} on syntax error
      expect(mockEditor.setValue).not.toHaveBeenCalled();
    });

    it("safely handles empty string or undefined JSON payload", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      expect(() => {
        mockBridgeFixture.emitCommand("console.clear", "");
      }).not.toThrow();
      expect(mockConsole.clear).toHaveBeenCalledOnce();
    });

    it("safely handles non-object JSON payloads (e.g. primitives)", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      expect(() => {
        mockBridgeFixture.emitCommand("editor.setValue", "123");
        mockBridgeFixture.emitCommand("editor.setValue", '"just a string"');
        mockBridgeFixture.emitCommand("editor.setValue", "null");
        mockBridgeFixture.emitCommand("editor.setValue", "true");
      }).not.toThrow();

      expect(mockEditor.setValue).not.toHaveBeenCalled();
    });

    it("safely ignores payloads with invalid property types", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      expect(() => {
        mockBridgeFixture.emitCommand(
          "editor.openTab",
          JSON.stringify({ uri: 123, content: ["not", "a", "string"] }),
        );
        mockBridgeFixture.emitCommand("console.append", JSON.stringify({ text: { object: true } }));
        mockBridgeFixture.emitCommand("editor.setFontSize", JSON.stringify({ size: null }));
      }).not.toThrow();

      expect(mockEditor.openTab).not.toHaveBeenCalled();
      expect(mockConsole.append).not.toHaveBeenCalled();
      expect(mockEditor.setFontSize).not.toHaveBeenCalled();
    });
  });

  describe("3. Static Bridge Helpers", () => {
    describe("BridgeService.active", () => {
      it("returns err when BridgeService is not instantiated", () => {
        const result = BridgeService.active;
        expect(result.ok).toBe(false);
        if (!result.ok) {
          expect(result.error.message).toBe("BridgeService instance is not available");
        }
      });

      it("returns ok when BridgeService is initialized", () => {
        using service = new BridgeService(mockEditor.service, mockLsp.service, mockConsole.service);
        service.initBridge();

        const result = BridgeService.active;
        expect(result.ok).toBe(true);
        if (result.ok) {
          expect(result.value).toBe(mockBridgeFixture.bridge);
        }
      });

      it("returns err after BridgeService is disposed", () => {
        const service = new BridgeService(mockEditor.service, mockLsp.service, mockConsole.service);
        service.initBridge();
        expect(BridgeService.active.ok).toBe(true);

        service.dispose();
        const result = BridgeService.active;
        expect(result.ok).toBe(false);
        if (!result.ok) {
          expect(result.error.message).toBe("BridgeService instance is not available");
        }
      });
    });

    describe("BridgeService.readFile", () => {
      it("returns ok with file content on success", async () => {
        using service = new BridgeService(mockEditor.service, mockLsp.service, mockConsole.service);
        service.initBridge();

        vi.mocked(mockBridgeFixture.bridge.readFile).mockImplementation(
          (_path: string, cb: (res: string | null) => void) => {
            cb("print('hello world')");
          },
        );

        const res = await BridgeService.readFile("d:/test.py");
        expect(res.ok).toBe(true);
        expect(res.unwrap()).toBe("print('hello world')");
        expect(mockBridgeFixture.bridge.readFile).toHaveBeenCalledWith(
          "d:/test.py",
          expect.any(Function),
        );
      });

      it("returns err when bridge callback returns null", async () => {
        using service = new BridgeService(mockEditor.service, mockLsp.service, mockConsole.service);
        service.initBridge();

        vi.mocked(mockBridgeFixture.bridge.readFile).mockImplementation(
          (_path: string, cb: (res: string | null) => void) => {
            cb(null);
          },
        );

        const res = await BridgeService.readFile("d:/nonexistent.py");
        expect(res.ok).toBe(false);
        if (!res.ok) {
          expect(res.error.message).toBe("Failed to read file: d:/nonexistent.py");
        }
      });

      it("returns err when no active bridge instance exists", async () => {
        const res = await BridgeService.readFile("d:/test.py");
        expect(res.ok).toBe(false);
        if (!res.ok) {
          expect(res.error.message).toBe("BridgeService instance is not available");
        }
      });
    });

    describe("BridgeService.statFile", () => {
      it("returns ok with file metadata on success", async () => {
        using service = new BridgeService(mockEditor.service, mockLsp.service, mockConsole.service);
        service.initBridge();

        const expectedStat: FileStatResponse = {
          type: 1,
          ctime: 1000,
          mtime: 2000,
          size: 4096,
        };

        vi.mocked(mockBridgeFixture.bridge.statFile).mockImplementation(
          (_path: string, cb: (res: FileStatResponse | null) => void) => {
            cb(expectedStat);
          },
        );

        const res = await BridgeService.statFile("d:/test.py");
        expect(res.ok).toBe(true);
        expect(res.unwrap()).toEqual(expectedStat);
        expect(mockBridgeFixture.bridge.statFile).toHaveBeenCalledWith(
          "d:/test.py",
          expect.any(Function),
        );
      });

      it("returns err when stat callback returns null", async () => {
        using service = new BridgeService(mockEditor.service, mockLsp.service, mockConsole.service);
        service.initBridge();

        vi.mocked(mockBridgeFixture.bridge.statFile).mockImplementation(
          (_path: string, cb: (res: FileStatResponse | null) => void) => {
            cb(null);
          },
        );

        const res = await BridgeService.statFile("d:/missing.py");
        expect(res.ok).toBe(false);
        if (!res.ok) {
          expect(res.error.message).toBe("File not found: d:/missing.py");
        }
      });

      it("returns err when no active bridge instance exists", async () => {
        const res = await BridgeService.statFile("d:/test.py");
        expect(res.ok).toBe(false);
        if (!res.ok) {
          expect(res.error.message).toBe("BridgeService instance is not available");
        }
      });
    });
  });

  describe("4. Debounced Content Synchronization", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("batches rapid content changes with a 300ms debounce timer", () => {
      const { bridgeService, bridge } = setupInitializedBridgeService();
      using fixture = bridgeService;
      void fixture;

      mockEditor.emitContentChange();
      mockEditor.emitContentChange();
      mockEditor.emitContentChange();

      // Should not be called immediately
      expect(bridge.onContentChanged).not.toHaveBeenCalled();
      expect(bridge.onMainContentChanged).not.toHaveBeenCalled();

      // Advance by 200ms (still within 300ms window)
      vi.advanceTimersByTime(200);
      expect(bridge.onContentChanged).not.toHaveBeenCalled();

      // Trigger another content change to reset debounce
      mockEditor.emitContentChange();
      vi.advanceTimersByTime(200);
      expect(bridge.onContentChanged).not.toHaveBeenCalled();

      // Complete the remaining 100ms
      vi.advanceTimersByTime(100);
      expect(bridge.onContentChanged).toHaveBeenCalledOnce();
      expect(bridge.onContentChanged).toHaveBeenCalledWith("active content");
      expect(bridge.onMainContentChanged).toHaveBeenCalledOnce();
      expect(bridge.onMainContentChanged).toHaveBeenCalledWith("main content");
    });

    it("flushes pending content changes immediately on tab changes", () => {
      const { bridgeService, bridge } = setupInitializedBridgeService();
      using fixture = bridgeService;
      void fixture;

      // Start debounce timer
      mockEditor.emitContentChange();
      expect(bridge.onContentChanged).not.toHaveBeenCalled();

      // Active tab changed triggers immediate flush
      mockEditor.emitActiveTabChange("file:///new_tab.py");
      expect(bridge.onActiveTabChanged).toHaveBeenCalledWith("file:///new_tab.py");
      expect(bridge.onContentChanged).toHaveBeenCalledOnce();
      expect(bridge.onMainContentChanged).toHaveBeenCalledOnce();

      // Timer should have been cleared; advancing 300ms does not fire duplicate call
      vi.advanceTimersByTime(300);
      expect(bridge.onContentChanged).toHaveBeenCalledOnce();
    });

    it("flushes pending content changes immediately on main tab changes", () => {
      const { bridgeService, bridge } = setupInitializedBridgeService();
      using fixture = bridgeService;
      void fixture;

      mockEditor.emitContentChange();
      expect(bridge.onContentChanged).not.toHaveBeenCalled();

      mockEditor.emitMainTabChange("file:///main_script.py");
      expect(bridge.onMainTabChanged).toHaveBeenCalledWith("file:///main_script.py");
      expect(bridge.onContentChanged).toHaveBeenCalledOnce();
      expect(bridge.onMainContentChanged).toHaveBeenCalledOnce();

      vi.advanceTimersByTime(300);
      expect(bridge.onContentChanged).toHaveBeenCalledOnce();
    });
  });

  describe("5. Lifecycle & Disposal", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("initializes bridge, notifies ready, and removes loading overlay with delay", () => {
      const bridgeService = new BridgeService(
        mockEditor.service,
        mockLsp.service,
        mockConsole.service,
      );

      const readySpy = vi.fn();
      bridgeService.onDidReady(readySpy);

      bridgeService.initBridge();

      expect(readySpy).toHaveBeenCalledWith(mockBridgeFixture.bridge);
      expect(mockBridgeFixture.bridge.onEditorReady).toHaveBeenCalledOnce();
      expect(mockBridgeFixture.bridge.dispatchCommandSignal.connect).toHaveBeenCalledOnce();

      // Loading overlay removal delayed by 200ms
      expect(overlayEl.classList.contains("fade-out")).toBe(false);
      vi.advanceTimersByTime(200);
      expect(overlayEl.classList.contains("fade-out")).toBe(true);

      overlayEl.dispatchEvent(new Event("transitionend"));
      expect(document.getElementById(DOM_IDS.LOADING_OVERLAY)).toBeNull();

      bridgeService.dispose();
    });

    it("handles missing bridge object in channel gracefully", () => {
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

      class MockEmptyQWebChannel {
        constructor(
          _transport: unknown,
          callback: (channel: { objects: Record<string, unknown> }) => void,
        ) {
          callback({ objects: {} }); // no 'bridge'
        }
      }

      Object.assign(window, {
        qt: { webChannelTransport: {} },
        QWebChannel: MockEmptyQWebChannel,
      });

      const bridgeService = new BridgeService(
        mockEditor.service,
        mockLsp.service,
        mockConsole.service,
      );

      const readySpy = vi.fn();
      bridgeService.onDidReady(readySpy);

      bridgeService.initBridge();

      expect(consoleErrorSpy).toHaveBeenCalledWith("Bridge object not found in channel.");
      expect(readySpy).not.toHaveBeenCalled();

      // Overlay still scheduled for removal
      vi.advanceTimersByTime(200);
      expect(overlayEl.classList.contains("fade-out")).toBe(true);

      bridgeService.dispose();
    });

    it("forwards editor service events to bridge slots", () => {
      const { bridgeService, bridge } = setupInitializedBridgeService();
      using fixture = bridgeService;
      void fixture;

      mockEditor.emitCursorPositionChange(42, 10);
      expect(bridge.onCursorPositionChanged).toHaveBeenCalledWith(42, 10);

      mockEditor.emitSaveRequest();
      expect(bridge.requestSave).toHaveBeenCalledOnce();

      mockEditor.emitSaveAsRequest();
      expect(bridge.requestSaveAs).toHaveBeenCalledOnce();

      mockEditor.emitFormatRequest();
      expect(bridge.requestFormat).toHaveBeenCalledOnce();
    });

    it("forwards console service toggle and resize events to editor layout", () => {
      using fixture = setupInitializedBridgeService().bridgeService;
      void fixture;

      mockConsole.emitToggle(false);
      expect(mockEditor.layout).toHaveBeenCalledOnce();

      mockConsole.emitResize(80);
      expect(mockEditor.layout).toHaveBeenCalledTimes(2);
    });

    it("cleans up signal listeners and active timers on dispose()", () => {
      const { bridgeService, bridge } = setupInitializedBridgeService();

      // Start debounce timer
      mockEditor.emitContentChange();

      bridgeService.dispose();

      // Signal disconnected
      expect(bridge.dispatchCommandSignal.disconnect).toHaveBeenCalledOnce();

      // Active instance cleared
      expect(BridgeService.active.ok).toBe(false);

      // Debounce timer cancelled, no bridge calls when time advances
      vi.advanceTimersByTime(500);
      expect(bridge.onContentChanged).not.toHaveBeenCalled();

      // Events from editor service no longer trigger anything
      mockEditor.emitCursorPositionChange(1, 1);
      expect(bridge.onCursorPositionChanged).not.toHaveBeenCalled();
    });

    it("supports Symbol.dispose", () => {
      const bridgeService = new BridgeService(
        mockEditor.service,
        mockLsp.service,
        mockConsole.service,
      );
      bridgeService.initBridge();
      expect(BridgeService.active.ok).toBe(true);

      bridgeService[Symbol.dispose]();
      expect(BridgeService.active.ok).toBe(false);
    });
  });
});

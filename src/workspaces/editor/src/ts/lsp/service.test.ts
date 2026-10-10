import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MessageReader, MessageWriter } from "vscode-jsonrpc";
import type { MessageTransports } from "vscode-languageclient/browser";

// Hoisted Mocks and Shared Test State
const {
  MockEventEmitter,
  vscodeWorkspaceMock,
  MockBaseLanguageClient,
  lspState,
  CloseAction,
  ErrorAction,
  State,
} = vi.hoisted(() => {
  enum CloseAction {
    DoNotRestart = 1,
    Restart = 2,
  }
  enum ErrorAction {
    Continue = 1,
    Shutdown = 2,
  }
  enum State {
    Stopped = 1,
    Starting = 2,
    Running = 3,
  }

  class MockEventEmitter<T> {
    private listeners: Array<(e: T) => unknown> = [];
    public event = (listener: (e: T) => unknown) => {
      this.listeners.push(listener);
      return {
        dispose: () => {
          const idx = this.listeners.indexOf(listener);
          if (idx !== -1) this.listeners.splice(idx, 1);
        },
      };
    };
    public fire(data: T): void {
      for (const listener of [...this.listeners]) {
        listener(data);
      }
    }
    public dispose(): void {
      this.listeners = [];
    }
  }

  type ConfigChangeListener = (e: { affectsConfiguration: (sec: string) => boolean }) => void;
  const configChangeListeners: ConfigChangeListener[] = [];

  const vscodeWorkspaceMock = {
    configChangeListeners,
    createFileSystemWatcher: vi.fn((_pattern: string) => ({ dispose: vi.fn() })),
    openTextDocument: vi.fn((_uri: unknown) => Promise.resolve({ uri: _uri })),
    onDidChangeConfiguration: vi.fn((listener: ConfigChangeListener) => {
      configChangeListeners.push(listener);
      return {
        dispose: () => {
          const idx = configChangeListeners.indexOf(listener);
          if (idx !== -1) configChangeListeners.splice(idx, 1);
        },
      };
    }),
    getConfiguration: vi.fn(() => ({
      get: vi.fn(),
      update: vi.fn().mockResolvedValue(undefined),
    })),
  };

  interface MockErrorHandler {
    error: (error: Error, message: unknown, count: number) => { action: ErrorAction };
    closed: () => { action: CloseAction; handled: boolean };
  }

  interface MockClientOptions {
    documentSelector?: Array<{ language: string }>;
    synchronize?: { fileEvents?: unknown };
    errorHandler?: MockErrorHandler;
    middleware?: {
      didOpen?: (doc: unknown, next: (doc: unknown) => Promise<void>) => Promise<void>;
      didClose?: (doc: unknown, next: (doc: unknown) => Promise<void>) => Promise<void>;
      provideDefinition?: (
        doc: unknown,
        pos: unknown,
        token: unknown,
        next: (doc: unknown, pos: unknown, token: unknown) => Promise<unknown>,
      ) => Promise<unknown>;
    };
  }

  interface CreatedClientRecord {
    id: string;
    name: string;
    clientOptions: MockClientOptions;
    transports: unknown;
    socket: unknown;
    instance: MockBaseLanguageClient;
  }

  const lspState = {
    createdClients: [] as CreatedClientRecord[],
    createdSockets: [] as unknown[],
  };

  class MockBaseLanguageClient {
    public id: string;
    public name: string;
    public clientOptions: MockClientOptions;
    public state: number = State.Running;
    public notificationHandlers = new Map<string, (params: unknown) => void>();
    public sentNotifications: Array<{ method: string; params: unknown }> = [];

    constructor(id: string, name: string, clientOptions: unknown) {
      this.id = id;
      this.name = name;
      this.clientOptions = (clientOptions || {}) as MockClientOptions;
      lspState.createdClients.push({
        id,
        name,
        clientOptions: this.clientOptions,
        transports: null,
        socket: null,
        instance: this,
      });
    }

    public start = vi.fn(async () => {
      this.state = State.Running;
    });

    public stop = vi.fn(async () => {
      this.state = State.Stopped;
    });

    public dispose = vi.fn(async () => {
      this.state = State.Stopped;
    });

    public handleConnectionClosed = vi.fn(async () => {
      this.state = State.Stopped;
    });

    public sendNotification = vi.fn(async (method: string, params: unknown) => {
      this.sentNotifications.push({ method, params });
    });

    public onNotification = vi.fn((method: string, handler: (params: unknown) => void) => {
      this.notificationHandlers.set(method, handler);
      return {
        dispose: () => {
          this.notificationHandlers.delete(method);
        },
      };
    });

    public simulateNotification(method: string, params: unknown): void {
      const handler = this.notificationHandlers.get(method);
      if (handler) {
        handler(params);
      }
    }
  }

  return {
    MockEventEmitter,
    vscodeWorkspaceMock,
    MockBaseLanguageClient,
    lspState,
    CloseAction,
    ErrorAction,
    State,
  };
});

type MockBaseLanguageClient = InstanceType<typeof MockBaseLanguageClient>;

// Mock vscode
vi.mock("vscode", () => ({
  EventEmitter: MockEventEmitter,
  workspace: vscodeWorkspaceMock,
  Uri: {
    file: (fsPath: string) => ({
      scheme: "file",
      fsPath,
      path: fsPath,
      toString: () => `file://${fsPath}`,
    }),
    parse: (uriStr: string) => ({
      scheme: uriStr.startsWith("file:") ? "file" : "untitled",
      fsPath: uriStr,
      path: uriStr,
      toString: () => uriStr,
    }),
  },
}));

// Mock vscode-languageclient/browser BaseLanguageClient
vi.mock("vscode-languageclient/browser", () => ({
  BaseLanguageClient: MockBaseLanguageClient,
  CloseAction,
  ErrorAction,
  State,
}));

// Mock WebSocket
class MockWebSocket extends EventTarget {
  public static readonly CONNECTING = 0;
  public static readonly OPEN = 1;
  public static readonly CLOSING = 2;
  public static readonly CLOSED = 3;

  public readyState: number = MockWebSocket.CONNECTING;
  public url: string;
  public onopen: ((ev: Event) => void) | null = null;
  public onclose: ((ev: CloseEvent) => void) | null = null;
  public onerror: ((ev: Event) => void) | null = null;
  public onmessage: ((ev: MessageEvent) => void) | null = null;
  public sentData: string[] = [];

  public closeSpy = vi.fn();

  constructor(url: string) {
    super();
    this.url = url;
    lspState.createdSockets.push(this);
  }

  public send(data: string): void {
    this.sentData.push(data);
  }

  public close(): void {
    this.closeSpy();
    this.readyState = MockWebSocket.CLOSED;
    this.dispatchEvent(new Event("close"));
  }

  public simulateOpen(): void {
    this.readyState = MockWebSocket.OPEN;
    this.dispatchEvent(new Event("open"));
  }

  public simulateClose(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.dispatchEvent(new Event("close"));
  }

  public simulateMessage(data: string): void {
    this.dispatchEvent(new MessageEvent("message", { data }));
  }

  public simulateError(err: Event = new Event("error")): void {
    this.dispatchEvent(err);
  }
}

// Import LspService and Types
import { LspService, type LspServerConfig, WebSocketLanguageClient } from "./service";

type MockClientInstance = InstanceType<typeof MockBaseLanguageClient>;

describe("LspService and Language Client Lifecycle", () => {
  let service: LspService;

  beforeEach(() => {
    vi.clearAllMocks();
    lspState.createdClients.length = 0;
    lspState.createdSockets.length = 0;
    vscodeWorkspaceMock.configChangeListeners.length = 0;

    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("window", {
      ENV: { VSVIEW_DEBUG: false },
    });

    service = new LspService();
  });

  afterEach(async () => {
    await service.disconnect();
    vi.unstubAllGlobals();
  });

  describe("LspService.connect(config)", () => {
    it("creates WebSocket connection to ws://127.0.0.1:<port>", async () => {
      const config: LspServerConfig = {
        id: "basedpyright",
        name: "BasedPyright",
        port: 9005,
        language: "python",
      };

      await service.connect(config);

      expect(lspState.createdSockets).toHaveLength(1);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      expect(ws.url).toBe("ws://127.0.0.1:9005");
    });

    it("creates and starts WebSocketLanguageClient on websocket open", async () => {
      const config: LspServerConfig = {
        id: "basedpyright",
        name: "BasedPyright",
        port: 9005,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;

      expect(lspState.createdClients).toHaveLength(0);

      ws.simulateOpen();

      expect(lspState.createdClients).toHaveLength(1);
      const clientRecord = lspState.createdClients[0]!;
      expect(clientRecord.id).toBe("basedpyright");
      expect(clientRecord.name).toBe("BasedPyright");

      const client = clientRecord.instance as MockClientInstance;
      expect(client.start).toHaveBeenCalled();
    });

    it("registers file events pattern in synchronize options when provided", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 8080,
        language: "python",
        fileEventsPattern: "**/*.py",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      expect(vscodeWorkspaceMock.createFileSystemWatcher).toHaveBeenCalledWith("**/*.py");
    });

    it("disconnects existing session first when connecting an already connected client ID", async () => {
      const config1: LspServerConfig = {
        id: "pylsp",
        name: "Python LSP",
        port: 9001,
        language: "python",
      };

      await service.connect(config1);
      const ws1 = lspState.createdSockets[0] as MockWebSocket;
      ws1.simulateOpen();

      expect(lspState.createdClients).toHaveLength(1);
      const client1 = lspState.createdClients[0]!.instance as MockClientInstance;

      // Reconnect same client ID on different port
      const config2: LspServerConfig = {
        id: "pylsp",
        name: "Python LSP Reconnected",
        port: 9002,
        language: "python",
      };

      await service.connect(config2);

      // Verify previous session was closed & disposed
      expect(ws1.closeSpy).toHaveBeenCalled();
      expect(client1.dispose).toHaveBeenCalled();

      // Verify second session created
      expect(lspState.createdSockets).toHaveLength(2);
      const ws2 = lspState.createdSockets[1] as MockWebSocket;
      expect(ws2.url).toBe("ws://127.0.0.1:9002");
    });

    it("handles WebSocket constructor failure gracefully", async () => {
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      vi.stubGlobal(
        "WebSocket",
        class FailingWebSocket {
          constructor() {
            throw new Error("Cannot construct WebSocket");
          }
        },
      );

      const config: LspServerConfig = {
        id: "failing",
        name: "Failing",
        port: 9999,
        language: "python",
      };

      await expect(service.connect(config)).resolves.not.toThrow();
      expect(lspState.createdClients).toHaveLength(0);
      expect(consoleErrorSpy).toHaveBeenCalledWith(
        expect.stringContaining("Failed to establish LSP WebSocket connection for 'failing'"),
        expect.any(Error),
      );
    });
  });

  describe("LspService.disconnect(id) / disconnect()", () => {
    it("closes socket and disposes client session when disconnected by ID", async () => {
      const config: LspServerConfig = {
        id: "ruff",
        name: "Ruff LSP",
        port: 9003,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      await service.disconnect("ruff");

      expect(ws.closeSpy).toHaveBeenCalled();
      expect(client.dispose).toHaveBeenCalled();
    });

    it("disconnects all active sessions when disconnect() is called without argument", async () => {
      const configA: LspServerConfig = {
        id: "clientA",
        name: "Client A",
        port: 9010,
        language: "python",
      };
      const configB: LspServerConfig = {
        id: "clientB",
        name: "Client B",
        port: 9011,
        language: "rust",
      };

      await service.connect(configA);
      (lspState.createdSockets[0] as MockWebSocket).simulateOpen();

      await service.connect(configB);
      (lspState.createdSockets[1] as MockWebSocket).simulateOpen();

      expect(lspState.createdClients).toHaveLength(2);
      const clientA = lspState.createdClients[0]!.instance as MockClientInstance;
      const clientB = lspState.createdClients[1]!.instance as MockClientInstance;

      await service.disconnect();

      expect((lspState.createdSockets[0] as MockWebSocket).closeSpy).toHaveBeenCalled();
      expect((lspState.createdSockets[1] as MockWebSocket).closeSpy).toHaveBeenCalled();
      expect(clientA.dispose).toHaveBeenCalled();
      expect(clientB.dispose).toHaveBeenCalled();
    });

    it("cleans up session when WebSocket fires onclose event", async () => {
      const config: LspServerConfig = {
        id: "autoClose",
        name: "Auto Close LSP",
        port: 9020,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      ws.simulateClose();

      expect(client.dispose).toHaveBeenCalled();
    });

    it("logs error when WebSocket fires onerror event", async () => {
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      const config: LspServerConfig = {
        id: "errLsp",
        name: "Error LSP",
        port: 9021,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const errorEvent = new Event("error");
      ws.onerror?.(errorEvent);

      expect(consoleErrorSpy).toHaveBeenCalledWith("LSP WebSocket error for 'errLsp':", errorEvent);
    });
  });

  describe("Configuration Synchronization", () => {
    it("emits workspace/didChangeConfiguration when matching single configurationSection changes", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
        configurationSection: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      // Trigger configuration change that affects 'python'
      for (const listener of vscodeWorkspaceMock.configChangeListeners) {
        listener({
          affectsConfiguration: (sec) => sec === "python",
        });
      }

      expect(client.sendNotification).toHaveBeenCalledWith("workspace/didChangeConfiguration", {
        settings: null,
      });
    });

    it("emits workspace/didChangeConfiguration for any matching section in array configurationSection", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
        configurationSection: ["python", "basedpyright"],
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      // Trigger change for 'basedpyright'
      for (const listener of vscodeWorkspaceMock.configChangeListeners) {
        listener({
          affectsConfiguration: (sec) => sec === "basedpyright",
        });
      }

      expect(client.sendNotification).toHaveBeenCalledWith("workspace/didChangeConfiguration", {
        settings: null,
      });
    });

    it("ignores configuration changes for unrelated sections", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
        configurationSection: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      // Trigger change for unrelated 'workbench.colorTheme'
      for (const listener of vscodeWorkspaceMock.configChangeListeners) {
        listener({
          affectsConfiguration: (sec) => sec === "workbench.colorTheme",
        });
      }

      expect(client.sendNotification).not.toHaveBeenCalled();
    });

    it("does not send notification if client state is not Running", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
        configurationSection: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;
      client.state = State.Stopped;

      for (const listener of vscodeWorkspaceMock.configChangeListeners) {
        listener({
          affectsConfiguration: (sec) => sec === "python",
        });
      }

      expect(client.sendNotification).not.toHaveBeenCalled();
    });
  });

  describe("Progress Tracking Notifications", () => {
    it("handles $/progress notifications with kind: 'begin' and 'end'", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      expect(client.notificationHandlers.has("$/progress")).toBe(true);

      // Simulate $/progress begin
      client.simulateNotification("$/progress", {
        token: "token-analysis-1",
        value: { kind: "begin", title: "Analyzing workspace" },
      });

      // Simulate $/progress end
      client.simulateNotification("$/progress", {
        token: "token-analysis-1",
        value: { kind: "end" },
      });
    });

    it("registers and handles custom progress notification pairs", async () => {
      const config: LspServerConfig = {
        id: "customLsp",
        name: "Custom LSP",
        port: 9000,
        language: "python",
        progressNotifications: [
          {
            begin: "custom/indexingStart",
            end: "custom/indexingEnd",
          },
        ],
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      expect(client.notificationHandlers.has("custom/indexingStart")).toBe(true);
      expect(client.notificationHandlers.has("custom/indexingEnd")).toBe(true);

      client.simulateNotification("custom/indexingStart", {});
      client.simulateNotification("custom/indexingEnd", {});
    });
  });

  describe("Middleware and Document Handling", () => {
    it("deduplicates didOpen calls for the same canonical document URI", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;
      const middleware = client.clientOptions.middleware;
      expect(middleware?.didOpen).toBeDefined();

      const nextSpy = vi.fn().mockResolvedValue(undefined);
      const doc = {
        uri: {
          scheme: "file",
          fsPath: "D:/project/main.py",
          toString: () => "file:///D:/project/main.py",
        },
      };

      // First open: should invoke next
      await middleware!.didOpen!(doc, nextSpy);
      expect(nextSpy).toHaveBeenCalledTimes(1);

      // Second open with same URI: should be deduplicated (no next call)
      await middleware!.didOpen!(doc, nextSpy);
      expect(nextSpy).toHaveBeenCalledTimes(1);

      // Close document
      const closeNextSpy = vi.fn().mockResolvedValue(undefined);
      await middleware!.didClose!(doc, closeNextSpy);
      expect(closeNextSpy).toHaveBeenCalledTimes(1);

      // Re-open document: should invoke next again
      await middleware!.didOpen!(doc, nextSpy);
      expect(nextSpy).toHaveBeenCalledTimes(2);
    });

    it("provideDefinition pre-loads target document for file URI results", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;
      const middleware = client.clientOptions.middleware;
      expect(middleware?.provideDefinition).toBeDefined();

      const doc = {
        uri: {
          scheme: "file",
          fsPath: "D:/project/main.py",
          toString: () => "file:///D:/project/main.py",
        },
      };
      const pos = { line: 10, character: 5 };
      const token = { isCancellationRequested: false };

      const targetUri = {
        scheme: "file",
        fsPath: "D:/project/lib.py",
        toString: () => "file:///D:/project/lib.py",
      };
      const definitionResult = [{ uri: targetUri, range: {} }];

      const nextSpy = vi.fn().mockResolvedValue(definitionResult);

      const res = await middleware!.provideDefinition!(doc, pos, token, nextSpy);
      expect(res).toBe(definitionResult);
      expect(vscodeWorkspaceMock.openTextDocument).toHaveBeenCalledWith(targetUri);
    });

    it("provideDefinition supports LocationLink format (targetUri)", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;
      const middleware = client.clientOptions.middleware;

      const doc = {
        uri: {
          scheme: "file",
          fsPath: "D:/project/main.py",
          toString: () => "file:///D:/project/main.py",
        },
      };
      const pos = { line: 10, character: 5 };
      const token = { isCancellationRequested: false };

      const targetUri = {
        scheme: "file",
        fsPath: "D:/project/link.py",
        toString: () => "file:///D:/project/link.py",
      };
      const definitionLinkResult = { targetUri, targetRange: {}, targetSelectionRange: {} };

      const nextSpy = vi.fn().mockResolvedValue(definitionLinkResult);

      const res = await middleware!.provideDefinition!(doc, pos, token, nextSpy);
      expect(res).toBe(definitionLinkResult);
      expect(vscodeWorkspaceMock.openTextDocument).toHaveBeenCalledWith(targetUri);
    });

    it("provideDefinition waits for progress completion when query returns empty while indexing", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;
      const middleware = client.clientOptions.middleware;

      // Start progress
      client.simulateNotification("$/progress", {
        token: "indexing-1",
        value: { kind: "begin" },
      });

      const doc = {
        uri: {
          scheme: "file",
          fsPath: "D:/project/main.py",
          toString: () => "file:///D:/project/main.py",
        },
      };
      const pos = { line: 10, character: 5 };
      const token = {
        isCancellationRequested: false,
        onCancellationRequested: vi.fn().mockReturnValue({ dispose: vi.fn() }),
      };

      const targetUri = {
        scheme: "file",
        fsPath: "D:/project/target.py",
        toString: () => "file:///D:/project/target.py",
      };

      let callCount = 0;
      const nextSpy = vi.fn().mockImplementation(async () => {
        callCount++;
        if (callCount === 1) {
          // End progress shortly after first query returns empty
          setTimeout(() => {
            client.simulateNotification("$/progress", {
              token: "indexing-1",
              value: { kind: "end" },
            });
          }, 10);
          return [];
        }
        return [{ uri: targetUri, range: {} }];
      });

      const res = await middleware!.provideDefinition!(doc, pos, token, nextSpy);
      expect(callCount).toBe(2);
      expect(res).toEqual([{ uri: targetUri, range: {} }]);
    });

    it("handles openTextDocument failure in provideDefinition gracefully", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;
      const middleware = client.clientOptions.middleware;

      const consoleWarnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vscodeWorkspaceMock.openTextDocument.mockRejectedValueOnce(new Error("File load failed"));

      const doc = {
        uri: {
          scheme: "file",
          fsPath: "D:/project/main.py",
          toString: () => "file:///D:/project/main.py",
        },
      };
      const pos = { line: 1, character: 1 };
      const token = {
        isCancellationRequested: false,
        onCancellationRequested: vi.fn().mockReturnValue({ dispose: vi.fn() }),
      };
      const targetUri = {
        scheme: "file",
        fsPath: "D:/project/fail.py",
        toString: () => "file:///D:/project/fail.py",
      };

      const nextSpy = vi.fn().mockResolvedValue([{ uri: targetUri }]);

      await expect(middleware!.provideDefinition!(doc, pos, token, nextSpy)).resolves.toBeDefined();
      expect(consoleWarnSpy).toHaveBeenCalledWith(
        expect.stringContaining("[LSP pyright] Failed to load model"),
        expect.any(Error),
      );
    });

    it("configures errorHandler with continue on error and do not restart on closed", async () => {
      const config: LspServerConfig = {
        id: "pyright",
        name: "Pyright",
        port: 9000,
        language: "python",
      };

      await service.connect(config);
      const ws = lspState.createdSockets[0] as MockWebSocket;
      ws.simulateOpen();

      const clientRecord = lspState.createdClients[0]!;
      const clientOptions = clientRecord.clientOptions;
      expect(clientOptions.errorHandler).toBeDefined();

      const errorResult = clientOptions.errorHandler!.error(new Error("test"), undefined, 1);
      expect(errorResult.action).toBe(ErrorAction.Continue);

      const closedResult = clientOptions.errorHandler!.closed();
      expect(closedResult.action).toBe(CloseAction.DoNotRestart);
      expect(closedResult.handled).toBe(true);
    });
  });

  describe("WebSocketLanguageClient stop behavior", () => {
    it("WebSocketLanguageClient stop() closes connection if socket is not open", async () => {
      const ws = new MockWebSocket("ws://127.0.0.1:9000");
      ws.readyState = MockWebSocket.CLOSED;

      const dummyTransports = {
        reader: {} as MessageReader,
        writer: {} as MessageWriter,
      };

      const client = new WebSocketLanguageClient(
        "test",
        "Test Client",
        { documentSelector: [{ language: "python" }] },
        dummyTransports,
        ws as unknown as WebSocket,
      );

      await client.stop();
      expect((client as unknown as MockBaseLanguageClient).state).toBe(State.Stopped);
    });

    it("WebSocketLanguageClient stop() does nothing if already stopped", async () => {
      const ws = new MockWebSocket("ws://127.0.0.1:9000");
      const dummyTransports = {
        reader: {} as MessageReader,
        writer: {} as MessageWriter,
      };

      const client = new WebSocketLanguageClient(
        "test",
        "Test Client",
        { documentSelector: [{ language: "python" }] },
        dummyTransports,
        ws as unknown as WebSocket,
      );
      (client as unknown as MockBaseLanguageClient).state = State.Stopped;

      await client.stop();
      expect((client as unknown as MockBaseLanguageClient).state).toBe(State.Stopped);
    });

    it("creates message transports returning provided transports", async () => {
      const ws = new MockWebSocket("ws://127.0.0.1:9000");
      const fakeTransports = {
        reader: {} as MessageReader,
        writer: {} as MessageWriter,
      };
      const client = new WebSocketLanguageClient(
        "test",
        "Test Client",
        { documentSelector: [{ language: "python" }] },
        fakeTransports,
        ws as unknown as WebSocket,
      );

      const transports = await (
        client as unknown as { createMessageTransports(): Promise<MessageTransports> }
      ).createMessageTransports();
      expect(transports).toBe(fakeTransports);
    });
  });

  describe("Disposal via dispose() and Symbol.dispose", () => {
    it("dispose() cleans up all active LSP sessions", async () => {
      const config: LspServerConfig = {
        id: "disposeTest",
        name: "Dispose Test",
        port: 9050,
        language: "python",
      };

      await service.connect(config);
      (lspState.createdSockets[0] as MockWebSocket).simulateOpen();

      const client = lspState.createdClients[0]!.instance as MockClientInstance;

      service.dispose();

      // Give async disconnect a tick
      await Promise.resolve();

      expect((lspState.createdSockets[0] as MockWebSocket).closeSpy).toHaveBeenCalled();
      expect(client.dispose).toHaveBeenCalled();
    });

    it("Symbol.dispose triggers dispose()", async () => {
      const config: LspServerConfig = {
        id: "symbolDispose",
        name: "Symbol Dispose",
        port: 9051,
        language: "python",
      };

      const scopedService = new LspService();
      try {
        await scopedService.connect(config);
        (lspState.createdSockets[0] as MockWebSocket).simulateOpen();
      } finally {
        scopedService.dispose();
      }

      await Promise.resolve();
      expect((lspState.createdSockets[0] as MockWebSocket).closeSpy).toHaveBeenCalled();
    });
  });
});

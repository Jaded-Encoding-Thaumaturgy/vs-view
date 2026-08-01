import * as vscode from "vscode";
import {
  BaseLanguageClient,
  CloseAction,
  ErrorAction,
  type LanguageClientOptions,
  type MessageTransports,
  State,
} from "vscode-languageclient/browser";

import { DisposableStore } from "../utils/disposables";
import { Result } from "../utils/result";
import { WebSocketMessageReader, WebSocketMessageWriter } from "./connection";

export class WebSocketLanguageClient extends BaseLanguageClient {
  constructor(
    id: string,
    name: string,
    clientOptions: LanguageClientOptions,
    private transports: MessageTransports,
  ) {
    super(id, name, clientOptions);
  }

  protected override createMessageTransports(): Promise<MessageTransports> {
    return Promise.resolve(this.transports);
  }

  protected override async handleConnectionClosed(): Promise<void> {
    if (this.state === State.Stopped) return;
    const resStop = await Result.fromPromise(this.stop());
    if (!resStop.ok) {
      console.debug(resStop.error);
    }
  }
}

/**
 * Configuration for an LSP server.
 */
export interface LspServerConfig {
  id: string;
  name: string;
  port: number;
  language: string;
  fileEventsPattern?: string | undefined;
  configurationSection?: string | string[] | undefined;
}

/**
 * Represents an active LSP client session.
 */
interface LspClientSession {
  client: BaseLanguageClient;
  socket: WebSocket;
  disposables: DisposableStore;
}

/**
 * Service for managing LSP WebSocket connections and language client lifecycle.
 */
export class LspService implements vscode.Disposable {
  private sessions = new Map<string, LspClientSession>();

  /** Connect to an LSP WebSocket bridge on specified port using given configuration. */
  public async connect(config: LspServerConfig): Promise<void> {
    if (this.sessions.has(config.id)) {
      console.debug(
        `LSP Client '${config.id}' already connected. Disconnecting existing client...`,
      );
      await this.disconnect(config.id);
    }

    const url = `ws://127.0.0.1:${config.port}`;
    console.debug(`Connecting LSP WebSocket client '${config.id}' to ${url}...`);

    const wsResult = Result.fromThrowable(() => new WebSocket(url));
    if (!wsResult.ok) {
      console.error(
        `Failed to establish LSP WebSocket connection for '${config.id}': could not create WebSocket for ${url}`,
        wsResult.error,
      );
      return;
    }

    const webSocket = wsResult.value;

    webSocket.onopen = () => {
      console.debug(`LSP WebSocket connection established for '${config.id}'`);

      const reader = new WebSocketMessageReader(webSocket);
      const writer = new WebSocketMessageWriter(webSocket);

      const synchronizeOptions: NonNullable<LanguageClientOptions["synchronize"]> = {};
      if (config.fileEventsPattern) {
        synchronizeOptions.fileEvents = vscode.workspace.createFileSystemWatcher(
          config.fileEventsPattern,
        );
      }

      const clientOptions: LanguageClientOptions = {
        documentSelector: [{ language: config.language }],
        synchronize: synchronizeOptions,
        middleware: {
          provideDefinition: async (document, position, token, next) => {
            const result = await next(document, position, token);
            if (!result) return result;

            for (const loc of Array.isArray(result) ? result : [result]) {
              const targetUriStr = "uri" in loc ? loc.uri.toString() : loc.targetUri.toString();
              if (targetUriStr.startsWith("file:")) {
                const openRes = await Result.fromPromise(
                  vscode.workspace.openTextDocument(vscode.Uri.parse(targetUriStr)),
                );
                if (!openRes.ok)
                  console.warn(
                    `Failed to pre-open VSCode document for ${targetUriStr}:`,
                    openRes.error,
                  );
              }
            }
            return result;
          },
        },
        errorHandler: {
          error: () => ({ action: ErrorAction.Continue }),
          closed: () => ({ action: CloseAction.DoNotRestart, handled: true }),
        },
      };

      const client = new WebSocketLanguageClient(config.id, config.name, clientOptions, {
        reader,
        writer,
      });

      const sessionDisposables = new DisposableStore();

      if (config.configurationSection !== undefined) {
        const sections = Array.isArray(config.configurationSection)
          ? config.configurationSection
          : [config.configurationSection];

        sessionDisposables.add(
          vscode.workspace.onDidChangeConfiguration((e) => {
            const isAffected = sections.some((sec) => e.affectsConfiguration(sec));
            if (isAffected && client.state === State.Running) {
              void client.sendNotification("workspace/didChangeConfiguration", { settings: null });
            }
          }),
        );
      }

      sessionDisposables.add(
        client.onNotification("$/progress", (params: any) => {
          if (!window.ENV?.VSVIEW_DEBUG) return;
          if (params?.value?.kind === "begin") {
            console.debug(`[LSP ${config.id}] ${params.value.title || "Analysis started"}`);
          } else if (params?.value?.kind === "end") {
            console.debug(`[LSP ${config.id}] Analysis complete.`);
          }
        }),
      );

      this.sessions.set(config.id, { client, socket: webSocket, disposables: sessionDisposables });
      void client.start();
      console.debug(`Native VS Code Language Client '${config.id}' started`);
    };

    webSocket.onerror = (err: Event) =>
      console.error(`LSP WebSocket error for '${config.id}':`, err);
    webSocket.onclose = () => {
      const session = this.sessions.get(config.id);
      if (session && session.socket === webSocket) {
        this.sessions.delete(config.id);
        session.disposables.dispose();
        void session.client.dispose();
      }
      console.debug(`LSP WebSocket connection closed for '${config.id}' (TS)`);
    };
  }

  /** Gracefully disconnect an active LSP language client by ID, or all clients if omitted. */
  public async disconnect(id?: string): Promise<void> {
    if (id !== undefined) {
      const session = this.sessions.get(id);
      if (session) {
        this.sessions.delete(id);
        if (
          session.socket.readyState === WebSocket.OPEN ||
          session.socket.readyState === WebSocket.CONNECTING
        ) {
          session.socket.close();
        }
        session.disposables.dispose();
        await session.client.dispose();
      }
    } else {
      await Promise.all(
        Array.from(this.sessions.keys()).map((sessionId) => this.disconnect(sessionId)),
      );
    }
  }

  public dispose(): void {
    void this.disconnect();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }
}

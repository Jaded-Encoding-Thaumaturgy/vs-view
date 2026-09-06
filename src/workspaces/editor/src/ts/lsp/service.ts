import * as vscode from "vscode";
import {
  BaseLanguageClient,
  CloseAction,
  ErrorAction,
  type LanguageClientOptions,
  type MessageTransports,
  State,
} from "vscode-languageclient/browser";

import { ensureModelLoaded } from "../services/vscode";
import { DisposableStore } from "../utils/disposables";
import { Result } from "../utils/result";
import { WebSocketMessageReader, WebSocketMessageWriter } from "./connection";
import { type LspProgressNotification, LspProgressTracker } from "./progress";

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
 * A pair of custom notification methods indicating the start and end of background analysis.
 */
export interface LspProgressNotificationPair {
  begin: string;
  end: string;
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
  progressNotifications?: LspProgressNotificationPair[] | undefined;
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

      const syncedUris = new Set<string>();
      const canonicalKey = (uri: vscode.Uri): string => {
        return uri.toString().toLowerCase().replace(/%3a/g, ":");
      };

      const progressTracker = new LspProgressTracker();

      const clientOptions: LanguageClientOptions = {
        documentSelector: [{ language: config.language }],
        synchronize: synchronizeOptions,
        middleware: {
          didOpen: async (document, next) => {
            const key = canonicalKey(document.uri);
            if (syncedUris.has(key)) {
              return;
            }
            syncedUris.add(key);
            await next(document);
          },
          didClose: async (document, next) => {
            const key = canonicalKey(document.uri);
            syncedUris.delete(key);
            await next(document);
          },
          provideDefinition: async (document, position, token, next) => {
            if (window.ENV?.VSVIEW_DEBUG) {
              console.debug(
                `[LSP ${config.id}] Requesting definition at ${document.uri.toString()}:${position.line + 1}:${position.character + 1}...`,
              );
            }
            let result = await next(document, position, token);
            const isEmpty = !result || (Array.isArray(result) && result.length === 0);

            if (isEmpty && progressTracker.hasActiveProgress && !token.isCancellationRequested) {
              if (window.ENV?.VSVIEW_DEBUG) {
                console.debug(
                  `[LSP ${config.id}] Definition query returned empty while indexing, waiting for completion...`,
                );
              }
              await progressTracker.waitForProgressOrTimeout(token, 2500);
              if (!token.isCancellationRequested) {
                result = await next(document, position, token);
              }
            }

            if (result) {
              const locs = Array.isArray(result) ? result : [result];
              if (window.ENV?.VSVIEW_DEBUG) {
                console.debug(
                  `[LSP ${config.id}] Definition returned ${locs.length} location(s). Pre-loading model(s)...`,
                );
              }
              await Promise.all(
                locs.map(async (loc) => {
                  const targetUri = "uri" in loc ? loc.uri : loc.targetUri;
                  if (targetUri.scheme === "file") {
                    const loadRes = await ensureModelLoaded(targetUri);
                    if (window.ENV?.VSVIEW_DEBUG) {
                      loadRes.match({
                        ok: () =>
                          console.debug(
                            `[LSP ${config.id}] Pre-loaded model for ${targetUri.toString()}`,
                          ),
                        err: (err) =>
                          console.warn(
                            `[LSP ${config.id}] Failed to load model for ${targetUri.toString()}:`,
                            err,
                          ),
                      });
                    }
                  }
                }),
              );
            } else if (window.ENV?.VSVIEW_DEBUG) {
              console.debug(`[LSP ${config.id}] No definition found.`);
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
      sessionDisposables.add(progressTracker);
      sessionDisposables.add({ dispose: () => syncedUris.clear() });

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
        client.onNotification("$/progress", (params: LspProgressNotification) => {
          const kind = params?.value?.kind;
          const token = params?.token;

          if (token !== undefined) {
            if (kind === "begin") {
              progressTracker.begin(token);
            } else if (kind === "end") {
              progressTracker.end(token);
            }
          }

          if (window.ENV?.VSVIEW_DEBUG) {
            if (kind === "begin") {
              console.debug(`[LSP ${config.id}] ${params.value?.title || "Analysis started"}`);
            } else if (kind === "end") {
              console.debug(`[LSP ${config.id}] Analysis complete.`);
            }
          }
        }),
      );

      if (config.progressNotifications) {
        for (const pair of config.progressNotifications) {
          const token = `__custom_progress_${pair.begin}__`;

          sessionDisposables.add(
            client.onNotification(pair.begin, () => {
              progressTracker.begin(token);
              if (window.ENV?.VSVIEW_DEBUG) {
                console.debug(`[LSP ${config.id}] Analysis started (${pair.begin})`);
              }
            }),
          );

          sessionDisposables.add(
            client.onNotification(pair.end, () => {
              progressTracker.end(token);
              if (window.ENV?.VSVIEW_DEBUG) {
                console.debug(`[LSP ${config.id}] Analysis finished (${pair.end})`);
              }
            }),
          );
        }
      }

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

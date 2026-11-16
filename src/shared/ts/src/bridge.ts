import { Disposable, type IDisposable, toDisposable } from './disposables';
import { Result, ResultAsync } from './result';

/** Utility type for Qt's QWebChannel signals. */
export interface QWebSignal<T extends (...args: never[]) => void> {
  connect(callback: T): void;
  disconnect(callback: T): void;
}

/** Base interface for a Python bridge object exposed via QWebChannel. */
export interface BasePythonBridge {
  dispatchCommandSignal: QWebSignal<(command: string, payloadJson: string) => void>;
  dispatchFromJs(command: string, payloadJson: string): void;
  onWebReady(): void;
}

declare global {
  interface Window {
    ENV?: {
      VSVIEW?: boolean;
      VSVIEW_DEBUG?: boolean;
      [key: string]: unknown;
    };
    qt?: {
      webChannelTransport: unknown;
    };
    QWebChannel: new (
      transport: unknown,
      callback: (channel: { objects: Record<string, unknown> }) => void,
    ) => void;
  }
}

export type CommandHandler<T> = (payload: T) => void;

/**
 * Generic typed QWebChannel command bus for Python <-> JavaScript communication.
 */
export class WebChannelBridge<
  PyToJsMap extends object,
  JsToPyMap extends object,
  TRpc extends BasePythonBridge = BasePythonBridge,
> extends Disposable {
  protected bridge: TRpc | null = null;

  private readonly commandHandlers = new Map<string, Array<CommandHandler<unknown>>>();
  private isDisposed = false;
  private boundSignalHandler: ((command: string, payloadJson: string) => void) | null = null;
  private initResult: ResultAsync<TRpc> | null = null;
  private readonly pendingInvokes = new Set<(err: Error) => void>();

  public initBridge(): ResultAsync<TRpc> {
    if (this.isDisposed) {
      return ResultAsync.fromResult(Result.err(new Error('Cannot initialize a disposed bridge')));
    }

    this.initResult ??= this.connectBridge().mapErr((err) => {
      this.initResult = null;
      return err;
    });
    return this.initResult;
  }

  public onCommand<K extends keyof PyToJsMap & string>(
    command: K,
    handler: CommandHandler<PyToJsMap[K]>,
  ): IDisposable {
    const erasedHandler = handler as CommandHandler<unknown>;
    const handlers = this.commandHandlers.get(command) ?? [];
    handlers.push(erasedHandler);
    this.commandHandlers.set(command, handlers);

    return toDisposable(() => {
      const current = this.commandHandlers.get(command);
      if (current) {
        const index = current.indexOf(erasedHandler);
        if (index !== -1) {
          current.splice(index, 1);
        }
      }
    });
  }

  public sendEvent<K extends keyof JsToPyMap & string>(
    ...[command, payload]: Record<string, never> extends JsToPyMap[K]
      ? [command: K, payload?: JsToPyMap[K]]
      : [command: K, payload: JsToPyMap[K]]
  ): Result<void> {
    if (!this.bridge) {
      const err = new Error(`Cannot send event '${command}': Bridge not ready`);
      console.error(err.message);
      return Result.err(err);
    }

    return Result.fromThrowable(() => {
      const payloadJson = JSON.stringify(payload ?? {});
      this.bridge!.dispatchFromJs(command, payloadJson);
    }).mapErr((err) => {
      console.error(`Failed to send bridge event '${command}':`, err);
      return err;
    });
  }

  public invoke<R>(
    caller: (bridge: TRpc, callback: (res: R | null | undefined) => void) => void,
    errorMsg = 'Bridge RPC call returned no result',
  ): ResultAsync<R> {
    const activeBridge = this.bridge;
    if (!activeBridge) {
      return ResultAsync.fromResult(Result.err(new Error('Bridge not ready')));
    }

    return Result.fromPromise(
      () =>
        new Promise<R>((resolve, reject) => {
          const cancel = (err: Error) => reject(err);
          this.pendingInvokes.add(cancel);

          const called = Result.fromThrowable(() => {
            caller(activeBridge, (data) => {
              this.pendingInvokes.delete(cancel);
              if (data === null || data === undefined) {
                reject(new Error(errorMsg));
              } else {
                resolve(data);
              }
            });
          });
          if (!called.ok) {
            this.pendingInvokes.delete(cancel);
            reject(called.error);
          }
        }),
    );
  }

  public override dispose(): void {
    this.isDisposed = true;
    this.commandHandlers.clear();
    this.detachBridge();
    const pending = [...this.pendingInvokes];
    this.pendingInvokes.clear();
    for (const cancel of pending) {
      cancel(new Error('Bridge was disposed before the RPC call returned'));
    }
    super.dispose();
  }

  protected onBridgeReady(_bridge: TRpc): void {}

  protected onBridgeError(_err: Error): void {}

  protected handleCommand(command: string, payloadJson: string): void {
    if (this.isDisposed) return;

    let payload: unknown = {};
    if (payloadJson) {
      const parseResult = Result.fromThrowable(() => JSON.parse(payloadJson));
      if (!parseResult.ok) {
        console.error(`Failed to parse command payload for ${command}:`, parseResult.error);
        return;
      }
      payload = parseResult.value;
    }

    if (!(typeof payload === 'object' && payload !== null && !Array.isArray(payload))) {
      console.error(`Invalid non-object payload for bridge command ${command}:`, payload);
      return;
    }

    const handlers = this.commandHandlers.get(command);
    if (!handlers || handlers.length === 0) {
      console.debug(`No handler registered for bridge command: ${command}`);
      return;
    }

    for (const handler of [...handlers]) {
      void Result.fromThrowable(() => handler(payload)).mapErr((err) => {
        console.error(`Error in handler for bridge command ${command}:`, err);
      });
    }
  }

  private connectBridge(): ResultAsync<TRpc> {
    return Result.fromPromise(
      () =>
        new Promise<TRpc>((resolve, reject) => {
          const fail = (err: Error) => {
            reject(err);
            console.error(err);
            this.onBridgeError(err);
          };

          const transport = window.qt?.webChannelTransport;
          if (!transport) {
            fail(new Error('Qt WebChannel transport is not available'));
            return;
          }

          const created = Result.fromThrowable(
            () =>
              new window.QWebChannel(transport, (channel) => {
                if (this.isDisposed) {
                  reject(new Error('Bridge was disposed before the Qt WebChannel was ready'));
                  return;
                }

                const pyBridge = channel.objects['bridge'] as TRpc | undefined;
                if (!pyBridge) {
                  fail(new Error('Bridge object not found on Qt WebChannel'));
                  return;
                }

                const attached = Result.fromThrowable(() => this.attachBridge(pyBridge));
                if (!attached.ok) {
                  this.detachBridge();
                  fail(attached.error);
                  return;
                }
                resolve(pyBridge);
              }),
          );
          if (!created.ok) {
            fail(created.error);
          }
        }),
    );
  }

  private attachBridge(pyBridge: TRpc): void {
    this.bridge = pyBridge;
    this.boundSignalHandler = (command: string, payloadJson: string) =>
      this.handleCommand(command, payloadJson);
    pyBridge.dispatchCommandSignal.connect(this.boundSignalHandler);

    this.onBridgeReady(pyBridge);
    pyBridge.onWebReady();
  }

  private detachBridge(): void {
    const { bridge, boundSignalHandler } = this;
    this.bridge = null;
    this.boundSignalHandler = null;

    if (bridge && boundSignalHandler) {
      Result.fromThrowable(() =>
        bridge.dispatchCommandSignal.disconnect(boundSignalHandler),
      ).mapErr((err) => {
        console.error('Failed to disconnect the bridge command signal:', err);
      });
    }
  }
}

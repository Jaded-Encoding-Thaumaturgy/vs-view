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

  public initBridge(): ResultAsync<TRpc> {
    return Result.fromPromise(
      () =>
        new Promise<TRpc>((resolve, reject) => {
          if (!window.qt?.webChannelTransport) {
            const err = new Error('Qt WebChannel transport is not available');
            this.onBridgeError(err);
            reject(err);
            return;
          }

          new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
            const pyBridge = channel.objects['bridge'] as TRpc | undefined;

            if (!pyBridge) {
              const err = new Error('Bridge object not found on Qt WebChannel');
              console.error(err);
              this.onBridgeError(err);
              reject(err);
              return;
            }

            this.bridge = pyBridge;
            this.boundSignalHandler = (command: string, payloadJson: string) => {
              this.handleCommand(command, payloadJson);
            };
            this.bridge.dispatchCommandSignal.connect(this.boundSignalHandler);

            this.onBridgeReady(pyBridge);
            this.bridge.onWebReady();
            resolve(pyBridge);
          });
        }),
    );
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
    caller: (bridge: TRpc, callback: (res: R | null) => void) => void,
    errorMsg = 'Bridge RPC call returned null',
  ): ResultAsync<R> {
    const activeBridge = this.bridge;
    if (!activeBridge) {
      return ResultAsync.fromResult(Result.err(new Error('Bridge not ready')));
    }

    return Result.fromPromise(
      () =>
        new Promise<R>((resolve, reject) => {
          caller(activeBridge, (data) => {
            if (data !== null) {
              resolve(data);
            } else {
              reject(new Error(errorMsg));
            }
          });
        }),
    );
  }

  public override dispose(): void {
    this.isDisposed = true;
    this.commandHandlers.clear();
    if (this.bridge && this.boundSignalHandler) {
      this.bridge.dispatchCommandSignal.disconnect(this.boundSignalHandler);
      this.boundSignalHandler = null;
    }
    this.bridge = null;
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
}

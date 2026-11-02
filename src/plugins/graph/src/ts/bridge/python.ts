import { type IDisposable, Result, type ResultAsync, toDisposable } from 'vsview-shared';

import type { JsToPyCommandMap, PyToJsCommandMap, PythonBridge } from '../types';

type CommandHandler<T> = (payload: T) => void;

export class BridgeService implements IDisposable {
  private bridge: PythonBridge | null = null;
  private readonly commandHandlers = new Map<string, Array<CommandHandler<unknown>>>();
  private isDisposed = false;
  private boundSignalHandler: ((command: string, payloadJson: string) => void) | null = null;

  public initBridge(): ResultAsync<PythonBridge> {
    return Result.fromPromise(
      () =>
        new Promise<PythonBridge>((resolve, reject) => {
          if (!window.qt?.webChannelTransport) {
            reject(new Error('Qt WebChannel transport is not available'));
            return;
          }

          new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
            const pyBridge = channel.objects['bridge'] as PythonBridge | undefined;

            if (!pyBridge) {
              const err = new Error('Bridge object not found on Qt WebChannel');
              console.error(err);
              reject(err);
              return;
            }

            this.bridge = pyBridge;
            this.boundSignalHandler = (command: string, payloadJson: string) => {
              this.handleCommand(command, payloadJson);
            };
            this.bridge.dispatchCommandSignal.connect(this.boundSignalHandler);

            this.bridge.onWebReady();
            resolve(pyBridge);
          });
        }),
    );
  }

  public onCommand<K extends keyof PyToJsCommandMap>(
    command: K,
    handler: CommandHandler<PyToJsCommandMap[K]>,
  ): IDisposable {
    const handlers = this.commandHandlers.get(command) ?? [];
    handlers.push(handler as CommandHandler<unknown>);
    this.commandHandlers.set(command, handlers);

    return toDisposable(() => {
      const current = this.commandHandlers.get(command);
      if (current) {
        const index = current.indexOf(handler as CommandHandler<unknown>);
        if (index !== -1) {
          current.splice(index, 1);
        }
      }
    });
  }

  public sendEvent<K extends keyof JsToPyCommandMap>(
    ...[command, payload]: Record<string, never> extends JsToPyCommandMap[K]
      ? [command: K, payload?: JsToPyCommandMap[K]]
      : [command: K, payload: JsToPyCommandMap[K]]
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

  public dispose(): void {
    this.isDisposed = true;
    this.commandHandlers.clear();
    if (this.bridge && this.boundSignalHandler) {
      this.bridge.dispatchCommandSignal.disconnect(this.boundSignalHandler);
      this.boundSignalHandler = null;
    }
    this.bridge = null;
  }

  private handleCommand(command: string, payloadJson: string): void {
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

    const handlers = this.commandHandlers.get(command);
    if (!handlers || handlers.length === 0) {
      console.debug(`No handler registered for bridge command: ${command}`);
      return;
    }

    for (const handler of handlers) {
      void Result.fromThrowable(() => handler(payload)).mapErr((err) => {
        console.error(`Error in handler for bridge command ${command}:`, err);
      });
    }
  }
}

import { Result } from './result';

export interface IDisposable {
  dispose(): void;
}

/**
 * Utility function to create an `IDisposable` from a teardown callback.
 */
export function toDisposable(fn: () => void): IDisposable {
  let isDisposed = false;
  return {
    dispose: () => {
      if (!isDisposed) {
        isDisposed = true;
        fn();
      }
    },
  };
}

/**
 * Manages a collection of disposables to ensure proper cleanup of resources.
 */
export class DisposableStore implements IDisposable {
  private isDisposed = false;
  private readonly disposables: IDisposable[] = [];

  /** Add a disposable resource to the store. */
  public add<T extends IDisposable>(disposable: T): T {
    if (this.isDisposed) {
      disposable.dispose();
    } else {
      this.disposables.push(disposable);
    }
    return disposable;
  }

  /** Dispose all tracked resources in LIFO order. */
  public dispose(): void {
    if (this.isDisposed) return;
    this.isDisposed = true;

    while (this.disposables.length > 0) {
      void Result.fromThrowable(() => this.disposables.pop()?.dispose()).mapErr((e) =>
        console.error('Error during resource disposal:', e),
      );
    }
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }
}

/**
 * Abstract base class for components managing owned disposables.
 */
export abstract class Disposable implements IDisposable {
  protected readonly _disposables = new DisposableStore();

  public dispose(): void {
    this._disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }
}

import type * as vscode from "vscode";

import { Result } from "./result";

/**
 * Utility function to create a `vscode.Disposable` from a teardown callback.
 */
export function toDisposable(fn: () => void): vscode.Disposable {
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
export class DisposableStore implements vscode.Disposable {
  private isDisposed = false;
  private disposables: vscode.Disposable[] = [];

  /** Add a disposable resource to the store. */
  public add<T extends vscode.Disposable>(disposable: T): T {
    if (this.isDisposed) {
      disposable.dispose();
    } else {
      this.disposables.push(disposable);
    }
    return disposable;
  }

  /** Dispose all tracked resources. */
  public dispose(): void {
    if (this.isDisposed) return;
    this.isDisposed = true;

    while (this.disposables.length > 0) {
      void Result.fromThrowable(() => this.disposables.pop()?.dispose()).mapErr((e) =>
        console.error("Error during resource disposal:", e),
      );
    }
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }
}

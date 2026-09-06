import type * as vscode from "vscode";

export interface LspProgressValue {
  kind?: "begin" | "report" | "end";
  title?: string;
  message?: string;
  percentage?: number;
}

export interface LspProgressNotification {
  token?: string | number;
  value?: LspProgressValue;
}

/**
 * Tracks background LSP server progress (indexing, type-checking)
 * and coordinates retry waits for definition/references queries.
 */
export class LspProgressTracker implements vscode.Disposable {
  private readonly activeTokens = new Set<string | number>();
  private readonly progressEndListeners = new Set<() => void>();

  public get hasActiveProgress(): boolean {
    return this.activeTokens.size > 0;
  }

  public begin(token: string | number): void {
    this.activeTokens.add(token);
  }

  public end(token: string | number): void {
    this.activeTokens.delete(token);
    if (this.activeTokens.size === 0) {
      this.notifyProgressEnded();
    }
  }

  public async waitForProgressOrTimeout(
    cancellationToken: vscode.CancellationToken,
    timeoutMs = 2500,
  ): Promise<void> {
    if (this.activeTokens.size === 0 || cancellationToken.isCancellationRequested) {
      return;
    }

    await new Promise<void>((resolve) => {
      const handles: {
        timer?: ReturnType<typeof setTimeout>;
        cancelSub?: vscode.Disposable;
      } = {};

      const cleanup = (): void => {
        if (handles.timer !== undefined) {
          clearTimeout(handles.timer);
        }
        handles.cancelSub?.dispose();
        this.progressEndListeners.delete(onDone);
      };

      const onDone = (): void => {
        cleanup();
        resolve();
      };

      this.progressEndListeners.add(onDone);

      handles.timer = setTimeout(() => {
        cleanup();
        resolve();
      }, timeoutMs);

      handles.cancelSub = cancellationToken.onCancellationRequested(() => {
        cleanup();
        resolve();
      });
    });
  }

  public dispose(): void {
    this.activeTokens.clear();
    this.notifyProgressEnded();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  private notifyProgressEnded(): void {
    for (const listener of this.progressEndListeners) {
      listener();
    }
    this.progressEndListeners.clear();
  }
}

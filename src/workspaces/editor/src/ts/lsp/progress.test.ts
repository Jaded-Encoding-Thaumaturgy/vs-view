import { describe, expect, it } from "vitest";
import type * as vscode from "vscode";

import { LspProgressTracker } from "./progress";

function createMockCancellationToken(): {
  token: vscode.CancellationToken;
  cancel: () => void;
} {
  let isCancelled = false;
  const listeners: Array<(e: unknown) => unknown> = [];

  const token: vscode.CancellationToken = {
    get isCancellationRequested() {
      return isCancelled;
    },
    onCancellationRequested(listener: (e: unknown) => unknown): vscode.Disposable {
      listeners.push(listener);
      return {
        dispose: () => {
          const idx = listeners.indexOf(listener);
          if (idx !== -1) listeners.splice(idx, 1);
        },
      };
    },
  };

  return {
    token,
    cancel: () => {
      isCancelled = true;
      for (const l of listeners) l(undefined);
    },
  };
}

describe("LspProgressTracker", () => {
  it("tracks active progress tokens correctly", () => {
    using tracker = new LspProgressTracker();

    expect(tracker.hasActiveProgress).toBe(false);

    tracker.begin("token1");
    expect(tracker.hasActiveProgress).toBe(true);

    tracker.begin("token2");
    expect(tracker.hasActiveProgress).toBe(true);

    tracker.end("token1");
    expect(tracker.hasActiveProgress).toBe(true);

    tracker.end("token2");
    expect(tracker.hasActiveProgress).toBe(false);
  });

  it("waitForProgressOrTimeout returns immediately if no progress is active", async () => {
    using tracker = new LspProgressTracker();
    const { token } = createMockCancellationToken();

    const start = Date.now();
    await tracker.waitForProgressOrTimeout(token, 1000);
    expect(Date.now() - start).toBeLessThan(100);
  });

  it("waitForProgressOrTimeout returns immediately if token is already cancelled", async () => {
    using tracker = new LspProgressTracker();
    const { token, cancel } = createMockCancellationToken();
    cancel();

    tracker.begin("token1");
    const start = Date.now();
    await tracker.waitForProgressOrTimeout(token, 1000);
    expect(Date.now() - start).toBeLessThan(100);
  });

  it("waitForProgressOrTimeout resolves when all tokens finish before timeout", async () => {
    using tracker = new LspProgressTracker();
    const { token } = createMockCancellationToken();

    tracker.begin("tokenA");

    setTimeout(() => {
      tracker.end("tokenA");
    }, 20);

    const start = Date.now();
    await tracker.waitForProgressOrTimeout(token, 500);
    expect(Date.now() - start).toBeLessThan(200);
  });

  it("waitForProgressOrTimeout resolves upon cancellation", async () => {
    using tracker = new LspProgressTracker();
    const { token, cancel } = createMockCancellationToken();

    tracker.begin("tokenX");

    setTimeout(() => {
      cancel();
    }, 20);

    const start = Date.now();
    await tracker.waitForProgressOrTimeout(token, 1000);
    expect(Date.now() - start).toBeLessThan(200);
  });

  it("waitForProgressOrTimeout resolves when timeout expires", async () => {
    using tracker = new LspProgressTracker();
    const { token } = createMockCancellationToken();

    tracker.begin("tokenLong");

    const start = Date.now();
    await tracker.waitForProgressOrTimeout(token, 30);
    expect(Date.now() - start).toBeGreaterThanOrEqual(25);
  });

  it("dispose clears all active tokens and notifies pending waiters", async () => {
    const tracker = new LspProgressTracker();
    const { token } = createMockCancellationToken();

    tracker.begin("tokenPending");

    const waitPromise = tracker.waitForProgressOrTimeout(token, 2000);

    tracker.dispose();
    expect(tracker.hasActiveProgress).toBe(false);

    await waitPromise;
  });
});

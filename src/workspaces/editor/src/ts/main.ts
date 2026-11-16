import { DisposableStore, Result, toDisposable } from 'vsview-shared';

import { MonacoManager } from './manager';
import { initVscodeServices } from './services/vscode';
import { STARTUP_TIMEOUT_MS } from './ui/constants';
import { renderAppLayout, renderStartupError } from './ui/layout';

const TIMED_OUT = Symbol('timed out');

/**
 * Main entry point for the Monaco editor workspace.
 */
async function main(): Promise<void> {
  const startup = new DisposableStore();
  const timeout = new Promise<typeof TIMED_OUT>((resolve) => {
    const timer = setTimeout(() => resolve(TIMED_OUT), STARTUP_TIMEOUT_MS);
    startup.add(toDisposable(() => clearTimeout(timer)));
  });

  const result = await Result.fromPromise(() => startEditor(timeout));
  startup.dispose();
  if (!result.ok) {
    failStartup('The editor failed to start.', result.error);
  }
}

async function startEditor(timeout: Promise<typeof TIMED_OUT>): Promise<void> {
  renderAppLayout();
  const serviceResult = await Promise.race([initVscodeServices(), timeout]);
  if (serviceResult === TIMED_OUT) {
    failTimedOut('loading the VS Code services');
    return;
  }
  if (!serviceResult.ok) {
    failStartup('VS Code standalone services failed to initialize.', serviceResult.error);
    return;
  }

  const managerResult = Result.fromThrowable(() => new MonacoManager());
  if (!managerResult.ok) {
    failStartup('The editor failed to start.', managerResult.error);
    return;
  }

  const manager = managerResult.value;
  const shutdown = () => manager.dispose();
  window.addEventListener('pagehide', shutdown, { once: true });
  window.addEventListener('unload', shutdown, { once: true });

  if ((await Promise.race([manager.bridgeService.initBridge(), timeout])) === TIMED_OUT) {
    failTimedOut('connecting to vsview');
  }
}

function failTimedOut(step: string): void {
  failStartup(
    `The editor did not start within ${STARTUP_TIMEOUT_MS / 1000} seconds.`,
    new Error(`Timed out while ${step}.`),
  );
}

function failStartup(message: string, err: unknown): void {
  console.error(message, err);
  renderStartupError(message, err);
}

void main();

import { Result } from 'vsview-shared';

import { MonacoManager } from './manager';
import { initVscodeServices } from './services/vscode';
import { renderAppLayout, renderStartupError } from './ui/layout';

/**
 * Main entry point for the Monaco editor workspace.
 */
async function main(): Promise<void> {
  renderAppLayout();
  const serviceResult = await initVscodeServices();
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
}

function failStartup(message: string, err: unknown): void {
  console.error(message, err);
  renderStartupError(message, err);
}

void Result.fromPromise(main).mapErr((err) => failStartup('The editor failed to start.', err));

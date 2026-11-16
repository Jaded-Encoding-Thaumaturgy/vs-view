import { html, render } from 'lit-html';
import { Result } from 'vsview-shared';

import { GraphManager } from './manager';

const LOADING_OVERLAY_ID = 'loading-overlay';

const REQUIRED_CONTAINERS = [
  'cy',
  'hud',
  'inspector-drawer',
  'overview',
  LOADING_OVERLAY_ID,
  'error-banner',
] as const;

type RequiredContainerId = (typeof REQUIRED_CONTAINERS)[number];

/**
 * Main entry point.
 */
async function main(): Promise<void> {
  const containers = getRequiredElements();
  if (!containers.ok) {
    failStartup(containers.error);
    return;
  }

  const managerResult = Result.fromThrowable(
    () =>
      new GraphManager({
        cyContainer: containers.value['cy'],
        hudContainer: containers.value['hud'],
        inspectorContainer: containers.value['inspector-drawer'],
        overviewContainer: containers.value['overview'],
        loadingOverlay: containers.value[LOADING_OVERLAY_ID],
        errorBanner: containers.value['error-banner'],
      }),
  );
  if (!managerResult.ok) {
    failStartup(managerResult.error);
    return;
  }

  const manager = managerResult.value;
  window.addEventListener('unload', () => manager.dispose(), { once: true });
  await manager.start();
}

function getRequiredElements(): Result<Record<RequiredContainerId, HTMLElement>> {
  const elements = {} as Record<RequiredContainerId, HTMLElement>;

  for (const id of REQUIRED_CONTAINERS) {
    const el = document.getElementById(id);
    if (!el) {
      return Result.err(new Error(`Required DOM element '#${id}' was not found`));
    }
    elements[id] = el;
  }

  return Result.ok(elements);
}

function failStartup(err: unknown): void {
  const message = 'The dependency graph failed to start.';
  console.error(message, err);

  let overlay = document.getElementById(LOADING_OVERLAY_ID);
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = LOADING_OVERLAY_ID;
    document.body.appendChild(overlay);
  }
  overlay.classList.remove('fade-out');
  overlay.classList.add('startup-error');
  overlay.replaceChildren();

  const detail = err instanceof Error ? err.message : String(err);
  // The overlay styles already exist for .loading-text, so the error is readable without new CSS.
  render(
    html`
      <div class="loading-text" role="alert">${message}</div>
      <div class="loading-text">${detail}</div>
    `,
    overlay,
  );
}

void Result.fromPromise(main).mapErr(failStartup);

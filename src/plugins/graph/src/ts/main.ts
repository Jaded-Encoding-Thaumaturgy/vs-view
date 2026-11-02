import { Result } from 'vsview-shared';

import { GraphManager } from './manager';

const REQUIRED_CONTAINERS = [
  'cy',
  'hud',
  'inspector-drawer',
  'overview',
  'loading-overlay',
  'error-banner',
] as const;

type RequiredContainerId = (typeof REQUIRED_CONTAINERS)[number];

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

/**
 * Main entry point.
 */
async function main(): Promise<void> {
  await getRequiredElements().match({
    ok: async (containers) => {
      const manager = new GraphManager({
        cyContainer: containers['cy'],
        hudContainer: containers['hud'],
        inspectorContainer: containers['inspector-drawer'],
        overviewContainer: containers['overview'],
        loadingOverlay: containers['loading-overlay'],
        errorBanner: containers['error-banner'],
      });
      window.addEventListener('unload', () => manager.dispose(), { once: true });
      await manager.start();
    },
    err: async (err) => console.error('Failed to initialize GraphManager:', err),
  });
}

void main().catch(console.error);

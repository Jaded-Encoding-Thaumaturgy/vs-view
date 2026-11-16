import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const { GraphManager } = vi.hoisted(() => ({ GraphManager: vi.fn() }));

vi.mock('./manager', () => ({ GraphManager }));

const PAGE = `
  <div id="app">
    <div id="cy"></div>
    <div id="hud"></div>
    <div id="inspector-drawer"></div>
    <div id="overview"></div>
    <div id="error-banner"></div>
    <div id="loading-overlay">
      <div class="loading-spinner"></div>
      <div class="loading-text">Loading Dependency Graph...</div>
    </div>
  </div>
`;

function overlay(): HTMLElement | null {
  return document.getElementById('loading-overlay');
}

async function runMain(): Promise<void> {
  vi.resetModules();
  await import('./main');
}

describe('graph main startup', () => {
  beforeEach(() => {
    document.body.innerHTML = PAGE;
    GraphManager.mockReset();
    GraphManager.mockImplementation(function () {
      return { dispose: vi.fn(), start: vi.fn().mockResolvedValue(undefined) };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('replaces the spinner with an error when the manager cannot be created', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const failure = new Error('cytoscape failed');
    GraphManager.mockImplementation(function () {
      throw failure;
    });

    await runMain();

    await vi.waitFor(() => expect(overlay()?.textContent).toContain('cytoscape failed'));
    expect(overlay()?.textContent).toContain('The dependency graph failed to start.');
    expect(overlay()?.querySelector('.loading-spinner')).toBeNull();
    expect(overlay()?.querySelector('[role="alert"]')).not.toBeNull();
    expect(overlay()?.classList.contains('startup-error')).toBe(true);
    expect(errorSpy).toHaveBeenCalledWith(expect.any(String), failure);
  });

  it('replaces the spinner with an error when start rejects', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    GraphManager.mockImplementation(function () {
      return { dispose: vi.fn(), start: vi.fn().mockRejectedValue(new Error('start failed')) };
    });

    await runMain();

    await vi.waitFor(() => expect(overlay()?.textContent).toContain('start failed'));
    expect(overlay()?.classList.contains('startup-error')).toBe(true);
  });

  it('shows an error when a required element is missing', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    document.getElementById('hud')!.remove();

    await runMain();

    await vi.waitFor(() => expect(overlay()?.textContent).toContain("'#hud'"));
    expect(overlay()?.classList.contains('startup-error')).toBe(true);
    expect(errorSpy).toHaveBeenCalled();
    expect(GraphManager).not.toHaveBeenCalled();
  });

  it('creates the overlay to show the error when the overlay itself is missing', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    overlay()!.remove();

    await runMain();

    await vi.waitFor(() => expect(overlay()?.textContent).toContain("'#loading-overlay'"));
    expect(overlay()?.classList.contains('startup-error')).toBe(true);
  });

  it('keeps the loading overlay when startup succeeds', async () => {
    await runMain();

    await vi.waitFor(() => expect(GraphManager).toHaveBeenCalledOnce());
    const manager = GraphManager.mock.results[0]!.value as { start: ReturnType<typeof vi.fn> };
    await vi.waitFor(() => expect(manager.start).toHaveBeenCalledOnce());
    expect(overlay()?.querySelector('.loading-spinner')).not.toBeNull();
    expect(overlay()?.classList.contains('startup-error')).toBe(false);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { Result } from 'vsview-shared';

import { DOM_IDS } from './ui/constants';

const { initVscodeServices, MonacoManager } = vi.hoisted(() => ({
  initVscodeServices: vi.fn(),
  MonacoManager: vi.fn(),
}));

vi.mock('./services/vscode', () => ({ initVscodeServices }));
vi.mock('./manager', () => ({ MonacoManager }));

function overlay(): HTMLElement {
  return document.getElementById(DOM_IDS.LOADING_OVERLAY)!;
}

async function runMain(): Promise<void> {
  vi.resetModules();
  await import('./main');
}

describe('main startup', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <main id="${DOM_IDS.APP}"></main>
      <div id="${DOM_IDS.LOADING_OVERLAY}">
        <div class="loading-spinner"></div>
        <div class="loading-text">Loading Editor...</div>
      </div>
    `;
    initVscodeServices.mockReset();
    MonacoManager.mockReset();
    MonacoManager.mockImplementation(function () {
      return { dispose: vi.fn() };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('replaces the spinner with an error when the VS Code services fail', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const failure = new Error('worker failed');
    initVscodeServices.mockResolvedValue(Result.err(failure));

    await runMain();

    await vi.waitFor(() => expect(overlay().textContent).toContain('worker failed'));
    expect(overlay().querySelector('.loading-spinner')).toBeNull();
    expect(overlay().classList.contains('startup-error')).toBe(true);
    expect(errorSpy).toHaveBeenCalledWith(expect.any(String), failure);
    expect(MonacoManager).not.toHaveBeenCalled();
  });

  it('replaces the spinner with an error when the VS Code services reject', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    initVscodeServices.mockRejectedValue(new Error('extension never ready'));

    await runMain();

    await vi.waitFor(() => expect(overlay().textContent).toContain('extension never ready'));
    expect(errorSpy).toHaveBeenCalled();
  });

  it('replaces the spinner with an error when the editor cannot be created', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    initVscodeServices.mockResolvedValue(Result.ok(undefined));
    const failure = new Error("Unsupported theme: 'Nope'");
    MonacoManager.mockImplementation(function () {
      throw failure;
    });

    await runMain();

    await vi.waitFor(() => expect(overlay().textContent).toContain("Unsupported theme: 'Nope'"));
    expect(errorSpy).toHaveBeenCalledWith(expect.any(String), failure);
  });

  it('keeps the loading overlay when startup succeeds', async () => {
    initVscodeServices.mockResolvedValue(Result.ok(undefined));

    await runMain();

    await vi.waitFor(() => expect(MonacoManager).toHaveBeenCalledOnce());
    expect(overlay().querySelector('.loading-spinner')).not.toBeNull();
    expect(overlay().classList.contains('startup-error')).toBe(false);
  });
});

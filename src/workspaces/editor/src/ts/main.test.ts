import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';
import { Result } from 'vsview-shared';

import { DOM_IDS, STARTUP_TIMEOUT_MS } from './ui/constants';

const { initVscodeServices, MonacoManager } = vi.hoisted(() => ({
  initVscodeServices: vi.fn(),
  MonacoManager: vi.fn(),
}));

vi.mock('./services/vscode', () => ({ initVscodeServices }));
vi.mock('./manager', () => ({ MonacoManager }));

function overlay(): HTMLElement {
  return document.getElementById(DOM_IDS.LOADING_OVERLAY)!;
}

function connectedBridge(): Promise<Result<object>> {
  return Promise.resolve(Result.ok({}));
}

function neverSettles<T>(): Promise<T> {
  return new Promise<T>(() => {});
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
      return { dispose: vi.fn(), bridgeService: { initBridge: () => connectedBridge() } };
    });
  });

  afterEach(() => {
    vi.useRealTimers();
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

  describe('timeout', () => {
    it('shows an error when the VS Code services never finish within 30 s', async () => {
      const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      vi.useFakeTimers();
      initVscodeServices.mockReturnValue(neverSettles());

      await runMain();
      await vi.advanceTimersByTimeAsync(STARTUP_TIMEOUT_MS - 1);
      const spinnerBeforeTimeout = overlay().querySelector('.loading-spinner') !== null;
      await vi.advanceTimersByTimeAsync(1);

      expect(STARTUP_TIMEOUT_MS).toBe(30_000);
      expect(spinnerBeforeTimeout).toBe(true);
      expect(overlay().classList.contains('startup-error')).toBe(true);
      expect(overlay().textContent).toContain('The editor did not start within 30 seconds.');
      expect(overlay().textContent).toContain('VS Code services');
      expect(errorSpy).toHaveBeenCalled();
      expect(MonacoManager).not.toHaveBeenCalled();
    });

    it('shows an error when the bridge never connects within 30 s', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      vi.useFakeTimers();
      initVscodeServices.mockResolvedValue(Result.ok(undefined));
      MonacoManager.mockImplementation(function () {
        return { dispose: vi.fn(), bridgeService: { initBridge: () => neverSettles() } };
      });

      await runMain();
      await vi.advanceTimersByTimeAsync(STARTUP_TIMEOUT_MS);

      expect(overlay().classList.contains('startup-error')).toBe(true);
      expect(overlay().textContent).toContain('The editor did not start within 30 seconds.');
      expect(overlay().textContent).toContain('vsview');
    });

    it('stops the timer once startup is done', async () => {
      vi.useFakeTimers();
      initVscodeServices.mockResolvedValue(Result.ok(undefined));

      await runMain();
      await vi.advanceTimersByTimeAsync(0);
      const pendingTimers = vi.getTimerCount();
      await vi.advanceTimersByTimeAsync(STARTUP_TIMEOUT_MS);

      expect(pendingTimers).toBe(0);
      expect(overlay().classList.contains('startup-error')).toBe(false);
    });

    it('stops the timer when startup fails early', async () => {
      vi.spyOn(console, 'error').mockImplementation(() => {});
      vi.useFakeTimers();
      initVscodeServices.mockResolvedValue(Result.err(new Error('worker failed')));

      await runMain();
      await vi.advanceTimersByTimeAsync(0);

      expect(vi.getTimerCount()).toBe(0);
      expect(overlay().textContent).toContain('worker failed');
    });
  });
});

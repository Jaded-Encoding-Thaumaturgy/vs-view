import { afterEach, beforeEach, describe, expect, it, vi } from 'vite-plus/test';

const { calls, lspDisconnect } = vi.hoisted(() => ({
  calls: [] as string[],
  lspDisconnect: vi.fn<() => Promise<void>>(),
}));

vi.mock('./lsp/service', () => ({
  LspService: class {
    public beginShutdown = vi.fn(() => calls.push('lsp.beginShutdown'));
    public disconnect = vi.fn(() => {
      calls.push('lsp.disconnect');
      return lspDisconnect();
    });
    public dispose = vi.fn(() => calls.push('lsp.dispose'));
  },
}));

vi.mock('./editor/service', () => ({
  EditorService: class {
    public dispose = vi.fn(() => calls.push('editor.dispose'));
  },
}));

vi.mock('./ui/console', () => ({
  ConsolePanelService: class {
    public fit = vi.fn();
    public dispose = vi.fn(() => calls.push('console.dispose'));
  },
}));

vi.mock('./bridge/python', () => ({
  BridgeService: class {
    public flushState = vi.fn(() => calls.push('bridge.flushState'));
    public onDidReady = vi.fn(() => ({ dispose: vi.fn() }));
    public initBridge = vi.fn();
    public dispose = vi.fn(() => calls.push('bridge.dispose'));
  },
}));

import { MonacoManager } from './manager';

describe('MonacoManager shutdown', () => {
  beforeEach(() => {
    calls.length = 0;
    lspDisconnect.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('flushes, disposes document owners, then disconnects the language clients', async () => {
    lspDisconnect.mockResolvedValue(undefined);
    const manager = new MonacoManager();

    const result = await manager.shutdown();

    expect(result.ok).toBe(true);
    expect(calls).toEqual([
      'bridge.flushState',
      'lsp.beginShutdown',
      'bridge.dispose',
      'console.dispose',
      'editor.dispose',
      'lsp.disconnect',
      'bridge.dispose',
      'console.dispose',
      'editor.dispose',
      'lsp.dispose',
    ]);
  });

  it('still disposes everything and logs when the language client disconnect fails', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const failure = new Error('disconnect failed');
    lspDisconnect.mockRejectedValue(failure);
    const manager = new MonacoManager();

    manager.dispose();

    await vi.waitFor(() =>
      expect(errorSpy).toHaveBeenCalledWith('MonacoManager shutdown failed', failure),
    );
    expect(calls.at(-1)).toBe('lsp.dispose');
  });

  it('runs the shutdown once for repeated calls', async () => {
    lspDisconnect.mockResolvedValue(undefined);
    const manager = new MonacoManager();

    await Promise.all([manager.shutdown(), manager.shutdown()]);

    expect(calls.filter((c) => c === 'lsp.disconnect')).toHaveLength(1);
  });
});

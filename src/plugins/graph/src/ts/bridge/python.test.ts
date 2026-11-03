import { afterEach, describe, expect, it, vi } from 'vite-plus/test';

import { BridgeService } from './python';

interface MockBridge {
  dispatchCommandSignal: {
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
  };
  dispatchFromJs: ReturnType<typeof vi.fn>;
  onWebReady: ReturnType<typeof vi.fn>;
}

function setupMockWebChannel(provideBridge = true): {
  mockBridge: MockBridge;
  emitRawCommand: (command: string, payloadJson: string) => void;
  emitCommand: (command: string, payload: unknown) => void;
} {
  let signalHandler: ((command: string, payloadJson: string) => void) | null = null;

  const mockBridge: MockBridge = {
    dispatchCommandSignal: {
      connect: vi.fn((fn: (cmd: string, json: string) => void) => {
        signalHandler = fn;
      }),
      disconnect: vi.fn(() => {
        signalHandler = null;
      }),
    },
    dispatchFromJs: vi.fn(),
    onWebReady: vi.fn(),
  };

  window.qt = { webChannelTransport: {} };
  window.QWebChannel = class {
    constructor(_transport: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: provideBridge ? { bridge: mockBridge } : {} });
    }
  };

  return {
    mockBridge,
    emitRawCommand: (command: string, payloadJson: string) => {
      signalHandler?.(command, payloadJson);
    },
    emitCommand: (command: string, payload: unknown) => {
      signalHandler?.(command, JSON.stringify(payload));
    },
  };
}

describe('BridgeService', () => {
  afterEach(() => {
    delete window.qt;
    delete (window as unknown as { QWebChannel?: unknown }).QWebChannel;
    vi.restoreAllMocks();
  });

  it('returns Err from initBridge when Qt WebChannel transport or bridge object is missing', async () => {
    const service = new BridgeService();
    const noTransport = await service.initBridge();
    expect(noTransport.ok).toBe(false);
    expect(noTransport.match({ ok: () => '', err: (e) => e.message })).toContain(
      'Qt WebChannel transport is not available',
    );

    vi.spyOn(console, 'error').mockImplementation(() => {});
    setupMockWebChannel(false);
    const noBridgeObj = await service.initBridge();
    expect(noBridgeObj.ok).toBe(false);
    expect(noBridgeObj.match({ ok: () => '', err: (e) => e.message })).toContain(
      'Bridge object not found on Qt WebChannel',
    );
  });

  it('initializes bridge, dispatches commands to subscribers, and supports unsubscription', async () => {
    const { mockBridge, emitCommand, emitRawCommand } = setupMockWebChannel();
    const service = new BridgeService();

    const initResult = await service.initBridge();
    expect(initResult.ok).toBe(true);
    expect(mockBridge.onWebReady).toHaveBeenCalledTimes(1);
    expect(mockBridge.dispatchCommandSignal.connect).toHaveBeenCalledTimes(1);

    const handler = vi.fn();
    const subscription = service.onCommand('graph.setData', handler);

    emitCommand('graph.setData', { nodes: [], edges: [] });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ nodes: [], edges: [] });

    // Empty JSON string defaults to empty object payload
    emitRawCommand('graph.setData', '');
    expect(handler).toHaveBeenCalledTimes(2);
    expect(handler).toHaveBeenLastCalledWith({});

    // Unsubscribing stops receiving subsequent events
    vi.spyOn(console, 'debug').mockImplementation(() => {});
    subscription.dispose();
    emitCommand('graph.setData', { nodes: [], edges: [] });
    expect(handler).toHaveBeenCalledTimes(2);

    service.dispose();
  });

  it('rejects malformed JSON or non-object payloads and isolates throwing command handlers', async () => {
    const { emitCommand, emitRawCommand } = setupMockWebChannel();
    const service = new BridgeService();
    await service.initBridge();

    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const throwingHandler = vi.fn(() => {
      throw new Error('Handler failure');
    });
    const succeedingHandler = vi.fn();

    service.onCommand('graph.selectNode', throwingHandler);
    service.onCommand('graph.selectNode', succeedingHandler);

    // Malformed JSON logs error and does not invoke handlers
    emitRawCommand('graph.selectNode', '{malformed');
    expect(throwingHandler).not.toHaveBeenCalled();
    expect(succeedingHandler).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalledWith(
      'Failed to parse command payload for graph.selectNode:',
      expect.any(Error),
    );

    // Non-object JSON payloads (null, arrays, primitives) are rejected at the boundary
    emitRawCommand('graph.selectNode', 'null');
    expect(throwingHandler).not.toHaveBeenCalled();
    expect(succeedingHandler).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalledWith(
      'Invalid non-object payload for bridge command graph.selectNode:',
      null,
    );

    // Valid command invokes both handlers despite the first throwing
    emitCommand('graph.selectNode', { id: 'node_1' });
    expect(throwingHandler).toHaveBeenCalledWith({ id: 'node_1' });
    expect(succeedingHandler).toHaveBeenCalledWith({ id: 'node_1' });
    expect(errorSpy).toHaveBeenCalledWith(
      'Error in handler for bridge command graph.selectNode:',
      expect.any(Error),
    );

    service.dispose();
  });

  it('sends events to Python when ready and returns Err when uninitialized or failing', async () => {
    const service = new BridgeService();
    vi.spyOn(console, 'error').mockImplementation(() => {});

    const uninitResult = service.sendEvent('graph.ready');
    expect(uninitResult.ok).toBe(false);
    expect(uninitResult.match({ ok: () => '', err: (e) => e.message })).toContain(
      'Bridge not ready',
    );

    const { mockBridge } = setupMockWebChannel();
    await service.initBridge();

    const okResult = service.sendEvent('output.select', { index: 2 });
    expect(okResult.ok).toBe(true);
    expect(mockBridge.dispatchFromJs).toHaveBeenCalledWith(
      'output.select',
      JSON.stringify({ index: 2 }),
    );

    mockBridge.dispatchFromJs.mockImplementationOnce(() => {
      throw new Error('IPC channel closed');
    });
    const throwResult = service.sendEvent('graph.reload');
    expect(throwResult.ok).toBe(false);
    expect(throwResult.match({ ok: () => '', err: (e) => e.message })).toBe('IPC channel closed');

    service.dispose();
  });

  it('disconnects signal and clears handlers when disposed', async () => {
    const { mockBridge, emitCommand } = setupMockWebChannel();
    const service = new BridgeService();
    await service.initBridge();

    const handler = vi.fn();
    service.onCommand('graph.selectNode', handler);

    service.dispose();
    expect(mockBridge.dispatchCommandSignal.disconnect).toHaveBeenCalledTimes(1);

    emitCommand('graph.selectNode', { id: 'node_99' });
    expect(handler).not.toHaveBeenCalled();
  });
});

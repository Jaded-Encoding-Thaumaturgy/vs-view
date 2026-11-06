import { afterEach, describe, expect, it, vi } from 'vite-plus/test';

import { type BasePythonBridge, WebChannelBridge } from './bridge';

interface TestPyToJsMap {
  'test.setData': { items: number[] };
  'test.ping': Record<string, never>;
}

interface TestJsToPyMap {
  'test.ready': Record<string, never>;
  'test.select': { index: number };
}

interface TestRpcBridge extends BasePythonBridge {
  readData(key: string, cb: (res: string | null) => void): void;
}

interface MockBridge {
  dispatchCommandSignal: {
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
  };
  dispatchFromJs: ReturnType<typeof vi.fn>;
  onWebReady: ReturnType<typeof vi.fn>;
  readData: ReturnType<typeof vi.fn>;
}

const fakeWindow = globalThis as unknown as {
  qt?: { webChannelTransport: unknown };
  QWebChannel?: unknown;
};

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
    readData: vi.fn(),
  };

  fakeWindow.qt = { webChannelTransport: {} };
  fakeWindow.QWebChannel = class {
    constructor(_transport: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: provideBridge ? { bridge: mockBridge } : {} });
    }
  };
  (globalThis as Record<string, unknown>).window = fakeWindow;

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

describe('WebChannelBridge', () => {
  afterEach(() => {
    delete fakeWindow.qt;
    delete fakeWindow.QWebChannel;
    vi.restoreAllMocks();
  });

  it('returns Err from initBridge when Qt WebChannel transport or bridge object is missing', async () => {
    (globalThis as Record<string, unknown>).window = fakeWindow;
    const service = new WebChannelBridge<TestPyToJsMap, TestJsToPyMap, TestRpcBridge>();
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
    const service = new WebChannelBridge<TestPyToJsMap, TestJsToPyMap, TestRpcBridge>();

    const initResult = await service.initBridge();
    expect(initResult.ok).toBe(true);
    expect(mockBridge.onWebReady).toHaveBeenCalledTimes(1);
    expect(mockBridge.dispatchCommandSignal.connect).toHaveBeenCalledTimes(1);

    const handler = vi.fn();
    const subscription = service.onCommand('test.setData', handler);

    emitCommand('test.setData', { items: [1, 2] });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ items: [1, 2] });

    emitRawCommand('test.setData', '');
    expect(handler).toHaveBeenCalledTimes(2);
    expect(handler).toHaveBeenLastCalledWith({});

    vi.spyOn(console, 'debug').mockImplementation(() => {});
    subscription.dispose();
    emitCommand('test.setData', { items: [3] });
    expect(handler).toHaveBeenCalledTimes(2);

    service.dispose();
  });

  it('rejects malformed JSON or non-object payloads and isolates throwing command handlers', async () => {
    const { emitCommand, emitRawCommand } = setupMockWebChannel();
    const service = new WebChannelBridge<TestPyToJsMap, TestJsToPyMap, TestRpcBridge>();
    await service.initBridge();

    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const throwingHandler = vi.fn(() => {
      throw new Error('Handler failure');
    });
    const succeedingHandler = vi.fn();

    service.onCommand('test.setData', throwingHandler);
    service.onCommand('test.setData', succeedingHandler);

    emitRawCommand('test.setData', '{malformed');
    expect(throwingHandler).not.toHaveBeenCalled();
    expect(succeedingHandler).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalledWith(
      'Failed to parse command payload for test.setData:',
      expect.any(Error),
    );

    emitRawCommand('test.setData', 'null');
    expect(throwingHandler).not.toHaveBeenCalled();
    expect(succeedingHandler).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalledWith(
      'Invalid non-object payload for bridge command test.setData:',
      null,
    );

    emitCommand('test.setData', { items: [1] });
    expect(throwingHandler).toHaveBeenCalledWith({ items: [1] });
    expect(succeedingHandler).toHaveBeenCalledWith({ items: [1] });
    expect(errorSpy).toHaveBeenCalledWith(
      'Error in handler for bridge command test.setData:',
      expect.any(Error),
    );

    service.dispose();
  });

  it('sends events to Python and supports RPC invoke()', async () => {
    const service = new WebChannelBridge<TestPyToJsMap, TestJsToPyMap, TestRpcBridge>();
    vi.spyOn(console, 'error').mockImplementation(() => {});

    expect(service.sendEvent('test.ready').ok).toBe(false);
    expect((await service.invoke((b, cb) => b.readData('k', cb))).ok).toBe(false);

    const { mockBridge } = setupMockWebChannel();
    await service.initBridge();

    const okResult = service.sendEvent('test.select', { index: 2 });
    expect(okResult.ok).toBe(true);
    expect(mockBridge.dispatchFromJs).toHaveBeenCalledWith(
      'test.select',
      JSON.stringify({ index: 2 }),
    );

    mockBridge.readData.mockImplementationOnce((_k: string, cb: (r: string | null) => void) =>
      cb('value'),
    );
    const rpcOk = await service.invoke<string>((b, cb) => b.readData('k', cb));
    expect(rpcOk.ok).toBe(true);
    expect(rpcOk.unwrap()).toBe('value');

    mockBridge.readData.mockImplementationOnce((_k: string, cb: (r: string | null) => void) =>
      cb(null),
    );
    const rpcErr = await service.invoke<string>((b, cb) => b.readData('k', cb), 'Not found');
    expect(rpcErr.ok).toBe(false);
    expect(() => rpcErr.unwrap()).toThrow('Not found');

    service.dispose();
  });
});

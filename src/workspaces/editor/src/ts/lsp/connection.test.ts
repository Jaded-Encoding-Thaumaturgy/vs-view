import { describe, expect, it, vi } from "vitest";
import type { Message } from "vscode-jsonrpc";

import { WebSocketMessageReader, WebSocketMessageWriter } from "./connection";

class MockWebSocket extends EventTarget {
  public readyState: number = WebSocket.CONNECTING;
  public sentData: string[] = [];

  public send(data: string): void {
    if (this.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket is not open: readyState " + this.readyState);
    }
    this.sentData.push(data);
  }

  public close(): void {
    this.readyState = WebSocket.CLOSED;
    this.dispatchEvent(new Event("close"));
  }

  public simulateOpen(): void {
    this.readyState = WebSocket.OPEN;
    this.dispatchEvent(new Event("open"));
  }

  public simulateMessage(data: string): void {
    this.dispatchEvent(new MessageEvent("message", { data }));
  }

  public simulateError(error: unknown = new Error("Simulated WS error")): void {
    this.dispatchEvent(new ErrorEvent("error", { error }));
  }
}

describe("WebSocket JSON-RPC Connection", () => {
  describe("WebSocketMessageReader", () => {
    it("buffers messages received before listen() and drains them once listened", () => {
      const socket = new MockWebSocket() as unknown as WebSocket;
      const reader = new WebSocketMessageReader(socket);

      const msg1 = { jsonrpc: "2.0", method: "test1" } as Message;
      const msg2 = { jsonrpc: "2.0", method: "test2" } as Message;

      // Simulate messages arriving before listen is called
      (socket as unknown as MockWebSocket).simulateMessage(JSON.stringify(msg1));
      (socket as unknown as MockWebSocket).simulateMessage(JSON.stringify(msg2));

      const received: Message[] = [];
      reader.listen((msg) => {
        received.push(msg);
      });

      expect(received).toEqual([msg1, msg2]);
      reader.dispose();
    });

    it("dispatches messages directly when listen() is already registered", () => {
      const socket = new MockWebSocket() as unknown as WebSocket;
      const reader = new WebSocketMessageReader(socket);

      const received: Message[] = [];
      reader.listen((msg) => received.push(msg));

      const msg = { jsonrpc: "2.0", id: 1, result: "ok" } as Message;
      (socket as unknown as MockWebSocket).simulateMessage(JSON.stringify(msg));

      expect(received).toEqual([msg]);
      reader.dispose();
    });

    it("fires error event when receiving malformed JSON", () => {
      const socket = new MockWebSocket() as unknown as WebSocket;
      const reader = new WebSocketMessageReader(socket);

      const errors: unknown[] = [];
      reader.onError((err) => errors.push(err));

      (socket as unknown as MockWebSocket).simulateMessage("NOT_VALID_JSON{");

      expect(errors.length).toBe(1);
      reader.dispose();
    });

    it("fires close event when websocket closes", () => {
      const socket = new MockWebSocket() as unknown as WebSocket;
      const reader = new WebSocketMessageReader(socket);

      const closeSpy = vi.fn();
      reader.onClose(closeSpy);

      (socket as unknown as MockWebSocket).close();

      expect(closeSpy).toHaveBeenCalledOnce();
      reader.dispose();
    });
  });

  describe("WebSocketMessageWriter", () => {
    it("sends message immediately if WebSocket is OPEN", async () => {
      const mockWs = new MockWebSocket();
      mockWs.readyState = WebSocket.OPEN;

      const writer = new WebSocketMessageWriter(mockWs as unknown as WebSocket);
      const msg = { jsonrpc: "2.0", method: "initialized" } as Message;

      await writer.write(msg);

      expect(mockWs.sentData).toHaveLength(1);
      expect(JSON.parse(mockWs.sentData[0]!)).toEqual(msg);
      writer.dispose();
    });

    it("queues message and sends it when WebSocket finishes CONNECTING and opens", async () => {
      const mockWs = new MockWebSocket();
      mockWs.readyState = WebSocket.CONNECTING;

      const writer = new WebSocketMessageWriter(mockWs as unknown as WebSocket);
      const msg = { jsonrpc: "2.0", method: "queued" } as Message;

      const writePromise = writer.write(msg);
      expect(mockWs.sentData).toHaveLength(0);

      mockWs.simulateOpen();
      await writePromise;

      expect(mockWs.sentData).toHaveLength(1);
      expect(JSON.parse(mockWs.sentData[0]!)).toEqual(msg);
      writer.dispose();
    });

    it("ignores write if socket is already closed or closing", async () => {
      const mockWs = new MockWebSocket();
      mockWs.readyState = WebSocket.CLOSED;

      const writer = new WebSocketMessageWriter(mockWs as unknown as WebSocket);
      const msg = { jsonrpc: "2.0", method: "ignored" } as Message;

      await writer.write(msg);

      expect(mockWs.sentData).toHaveLength(0);
      writer.dispose();
    });

    it("fires close event when end() or socket close is triggered", () => {
      const mockWs = new MockWebSocket();
      const writer = new WebSocketMessageWriter(mockWs as unknown as WebSocket);

      const closeSpy = vi.fn();
      writer.onClose(closeSpy);

      writer.end();
      expect(closeSpy).toHaveBeenCalledOnce();
      writer.dispose();
    });
  });
});

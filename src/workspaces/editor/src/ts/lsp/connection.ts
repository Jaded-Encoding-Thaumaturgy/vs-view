import type * as vscode from "vscode";
import {
  AbstractMessageReader,
  AbstractMessageWriter,
  type DataCallback,
  type Message,
  type MessageReader,
  type MessageWriter,
} from "vscode-jsonrpc";

import { Result } from "../utils/result";

/**
 * MessageReader for WebSocket integration with VS Code LanguageClient.
 * Buffers incoming messages until a data callback is attached via `listen()`.
 */
export class WebSocketMessageReader
  extends AbstractMessageReader
  implements MessageReader, vscode.Disposable
{
  private callback: DataCallback | null = null;
  private messageBuffer: Message[] = [];
  private isDisposed = false;

  private readonly handleMessage = (event: MessageEvent): void => {
    if (this.isDisposed) return;
    const data = event.data;

    if (typeof data === "string") {
      const msgRes = Result.fromThrowable(() => JSON.parse(data) as Message);
      if (msgRes.ok) {
        const msg = msgRes.value;
        if (this.callback) {
          this.callback(msg);
        } else {
          this.messageBuffer.push(msg);
        }
      } else {
        const e = msgRes.error;
        this.fireError(e instanceof Error ? e : new Error(String(e)));
      }
    }
  };

  private readonly handleClose = (): void => {
    if (!this.isDisposed) {
      this.fireClose();
    }
  };

  private readonly handleError = (e: Event): void => {
    if (!this.isDisposed) {
      this.fireError(e);
    }
  };

  constructor(private socket: WebSocket) {
    super();
    this.socket.addEventListener("message", this.handleMessage);
    this.socket.addEventListener("close", this.handleClose);
    this.socket.addEventListener("error", this.handleError);
  }

  public override listen(callback: DataCallback): vscode.Disposable {
    if (this.isDisposed) {
      return { dispose: () => {} };
    }
    this.callback = callback;

    // Flush any buffered messages that arrived before listen() was invoked
    while (this.messageBuffer.length > 0 && this.callback) {
      const msg = this.messageBuffer.shift();
      if (msg) {
        const res = Result.fromThrowable(() => this.callback!(msg));
        if (!res.ok) {
          const e = res.error;
          this.fireError(e instanceof Error ? e : new Error(String(e)));
        }
      }
    }

    return {
      dispose: () => {
        this.callback = null;
      },
    };
  }

  public override dispose(): void {
    if (this.isDisposed) return;
    this.isDisposed = true;
    this.callback = null;
    this.messageBuffer = [];
    this.socket.removeEventListener("message", this.handleMessage);
    this.socket.removeEventListener("close", this.handleClose);
    this.socket.removeEventListener("error", this.handleError);
    super.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }
}

/**
 * MessageWriter for WebSocket integration with VS Code LanguageClient
 */
export class WebSocketMessageWriter
  extends AbstractMessageWriter
  implements MessageWriter, vscode.Disposable
{
  private isDisposed = false;

  private readonly handleClose = (): void => {
    if (!this.isDisposed) {
      this.isDisposed = true;
      this.fireClose();
    }
  };

  private readonly handleError = (e: Event): void => {
    if (!this.isDisposed) {
      this.fireError(e);
    }
  };

  constructor(private socket: WebSocket) {
    super();
    this.socket.addEventListener("close", this.handleClose);
    this.socket.addEventListener("error", this.handleError);
  }

  public write(msg: Message): Promise<void> {
    if (
      this.isDisposed ||
      this.socket.readyState === WebSocket.CLOSED ||
      this.socket.readyState === WebSocket.CLOSING
    ) {
      return Promise.resolve();
    }

    if (this.socket.readyState === WebSocket.OPEN) {
      const resSend = Result.fromThrowable(() => this.socket.send(JSON.stringify(msg)));
      if (!resSend.ok) {
        const err = resSend.error;
        this.fireError(err instanceof Error ? err : new Error(String(err)));
      }
      return Promise.resolve();
    }

    // WebSocket is CONNECTING, queue sending once open
    return new Promise((resolve, reject) => {
      const onOpen = () => {
        cleanup();
        const resSend = Result.fromThrowable(() => this.socket.send(JSON.stringify(msg)));
        if (!resSend.ok) {
          const err = resSend.error;
          this.fireError(err instanceof Error ? err : new Error(String(err)));
        }
        resolve();
      };
      const onError = (e: Event) => {
        cleanup();
        reject(e);
      };
      const onClose = () => {
        cleanup();
        resolve();
      };
      const cleanup = () => {
        this.socket.removeEventListener("open", onOpen);
        this.socket.removeEventListener("error", onError);
        this.socket.removeEventListener("close", onClose);
      };
      this.socket.addEventListener("open", onOpen);
      this.socket.addEventListener("error", onError);
      this.socket.addEventListener("close", onClose);
    });
  }

  public end(): void {
    if (!this.isDisposed) {
      this.isDisposed = true;
      this.fireClose();
    }
  }

  public override dispose(): void {
    if (this.isDisposed) return;
    this.isDisposed = true;
    this.socket.removeEventListener("close", this.handleClose);
    this.socket.removeEventListener("error", this.handleError);
    super.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }
}

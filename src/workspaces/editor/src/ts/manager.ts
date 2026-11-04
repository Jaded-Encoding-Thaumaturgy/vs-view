import type * as vscode from 'vscode';
import { DisposableStore } from 'vsview-shared';

import { BridgeService } from './bridge/python';
import { EditorService } from './editor/service';
import { LspService } from './lsp/service';
import { ConsolePanelService } from './ui/console';

export class MonacoManager implements vscode.Disposable {
  public readonly lspService: LspService;
  public readonly editorService: EditorService;
  public readonly consoleService: ConsolePanelService;
  public readonly bridgeService: BridgeService;

  private readonly disposables = new DisposableStore();
  private shutdownPromise: Promise<void> | null = null;

  constructor() {
    this.lspService = this.disposables.add(new LspService());
    this.editorService = this.disposables.add(new EditorService());
    this.consoleService = this.disposables.add(new ConsolePanelService());
    this.bridgeService = this.disposables.add(
      new BridgeService(this.editorService, this.lspService, this.consoleService),
    );

    this.disposables.add(this.bridgeService.onDidReady(() => this.consoleService.fit()));
    this.bridgeService.initBridge();
  }

  public dispose(): void {
    void this.shutdown().catch((err) => console.error('MonacoManager shutdown failed', err));
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  public async [Symbol.asyncDispose](): Promise<void> {
    await this.shutdown();
  }

  /** Dispose document owners before disconnecting their language clients. */
  public shutdown(): Promise<void> {
    this.shutdownPromise ??= (async () => {
      try {
        this.bridgeService.flushState();
        this.lspService.beginShutdown();
        this.bridgeService.dispose();
        this.consoleService.dispose();
        this.editorService.dispose();
        await this.lspService.disconnect();
      } finally {
        this.disposables.dispose();
      }
    })();
    return this.shutdownPromise;
  }
}

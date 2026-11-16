import type * as vscode from 'vscode';
import { DisposableStore, Result, ResultAsync } from 'vsview-shared';

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
  private shutdownResult: ResultAsync<void> | null = null;

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
    void this.shutdown().mapErr(logShutdownError);
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }

  public async [Symbol.asyncDispose](): Promise<void> {
    await this.shutdown().mapErr(logShutdownError);
  }

  /** Dispose document owners before disconnecting their language clients. */
  public shutdown(): ResultAsync<void> {
    this.shutdownResult ??= ResultAsync.fromResult(
      Result.fromPromise(async () => {
        this.bridgeService.flushState();
        this.lspService.beginShutdown();
        this.bridgeService.dispose();
        this.consoleService.dispose();
        this.editorService.dispose();
        await this.lspService.disconnect();
      }).then((result) => {
        this.disposables.dispose();
        return result;
      }),
    );
    return this.shutdownResult;
  }
}

function logShutdownError(err: Error): void {
  console.error('MonacoManager shutdown failed', err);
}

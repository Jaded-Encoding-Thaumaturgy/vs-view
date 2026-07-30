import type * as vscode from "vscode";

import { BridgeService } from "./bridge/python";
import { EditorService } from "./editor/service";
import { LspService } from "./lsp/service";
import { ConsolePanelService } from "./ui/console";
import { DisposableStore } from "./utils/disposables";

export class MonacoManager implements vscode.Disposable {
  private readonly disposables = new DisposableStore();
  public readonly editorService: EditorService;
  public readonly lspService: LspService;
  public readonly consoleService: ConsolePanelService;
  public readonly bridgeService: BridgeService;

  constructor() {
    this.editorService = this.disposables.add(new EditorService());
    this.lspService = this.disposables.add(new LspService());
    this.consoleService = this.disposables.add(new ConsolePanelService());
    this.bridgeService = this.disposables.add(
      new BridgeService(this.editorService, this.lspService, this.consoleService),
    );

    this.disposables.add(
      this.bridgeService.onDidReady(() => {
        this.consoleService.fit();
      }),
    );
    this.bridgeService.initBridge();
  }

  public dispose(): void {
    this.disposables.dispose();
  }

  public [Symbol.dispose](): void {
    this.dispose();
  }
}

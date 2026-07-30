import { MonacoManager } from "./manager";
import { initVscodeServices } from "./services/vscode";
import { renderAppLayout } from "./ui/layout";

/**
 * Main entry point for the Monaco editor workspace.
 */
async function main(): Promise<void> {
  renderAppLayout();
  const serviceResult = await initVscodeServices();
  if (!serviceResult.ok) {
    console.error("VS Code standalone services failed to initialize.", serviceResult.error);
    return;
  }
  new MonacoManager();
}

void main().catch(console.error);

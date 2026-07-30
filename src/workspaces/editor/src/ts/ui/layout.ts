import { DOM_IDS } from "./constants";

export function renderAppLayout(): void {
  const appContainer = document.getElementById(DOM_IDS.APP);
  if (!appContainer) {
    console.error(`Root container #${DOM_IDS.APP} not found in document.`);
    return;
  }

  appContainer.innerHTML = `
    <div id="${DOM_IDS.TAB_BAR}"></div>
    <div id="${DOM_IDS.EDITOR}"></div>
    <div id="${DOM_IDS.CONSOLE_RESIZER}" class="console-resizer"></div>
    <div id="${DOM_IDS.CONSOLE_PANEL}" class="console-panel">
      <div class="console-header">
        <div class="console-title">Output Console</div>
        <div class="console-actions">
          <button id="${DOM_IDS.CONSOLE_CLEAR_BTN}" class="console-btn" title="Clear Console">Clear</button>
          <button id="${DOM_IDS.CONSOLE_CLOSE_BTN}" class="console-btn" title="Hide Console">✕</button>
        </div>
      </div>
      <div id="${DOM_IDS.XTERM_CONTAINER}" class="xterm-container"></div>
    </div>
  `;
}

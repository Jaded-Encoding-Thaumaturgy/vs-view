import type { IConfigurationNode } from "@codingame/monaco-vscode-configuration-service-override";
import type * as monaco from "monaco-editor";

export const defaultEditorOptions: monaco.editor.IStandaloneEditorConstructionOptions = {
  // IGlobalEditorOptions
  tabSize: 4,
  insertSpaces: true,
  theme: "vs-dark",
  // IEditorOptions
  lineNumbers: "on",
  glyphMargin: true,
  minimap: { enabled: true },
  cursorBlinking: "smooth",
  mouseWheelZoom: true,
  cursorSmoothCaretAnimation: "on",
  fontLigatures: false,
  scrollBeyondLastLine: false,
  smoothScrolling: true,
  automaticLayout: true,
  wordWrap: "off",
  links: true,
  contextmenu: true,
  padding: { top: 8 },
  folding: true,
  renderWhitespace: "selection",
  fontFamily:
    "'Cascadia Mono', 'SF Mono', Monaco, Menlo, Consolas, 'Ubuntu Mono', 'Liberation Mono', 'DejaVu Sans Mono', monospace",
  fontSize: 14,
  guides: {
    bracketPairs: true,
    indentation: true,
  },
  bracketPairColorization: { enabled: true },
  // IStandaloneEditorConstructionOptions
  value: "",
  language: "python",
};

export const BASED_PYRIGHT_SETTINGS_DEFS: NonNullable<IConfigurationNode["properties"]> = {
  "basedpyright.analysis.typeCheckingMode": {},
  "basedpyright.disableLanguageServices": {},
  "basedpyright.analysis.autoImportCompletions": {},
  "basedpyright.analysis.inlayHints.variableTypes": {},
  "basedpyright.analysis.inlayHints.callArgumentNames": {},
  "basedpyright.analysis.inlayHints.functionReturnTypes": {},
  "basedpyright.analysis.inlayHints.genericTypes": {},
  "basedpyright.analysis.diagnosticMode": {},
  "basedpyright.analysis.extraPaths": {},
};

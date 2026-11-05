import type { IConfigurationNode } from '@codingame/monaco-vscode-configuration-service-override';
import type * as monaco from 'monaco-editor';

export const defaultEditorOptions: monaco.editor.IStandaloneEditorConstructionOptions = {
  // IGlobalEditorOptions
  tabSize: 4,
  insertSpaces: true,
  theme: 'vs-dark',
  // IEditorOptions
  lineNumbers: 'on',
  glyphMargin: true,
  minimap: { enabled: true },
  cursorBlinking: 'smooth',
  mouseWheelZoom: true,
  cursorSmoothCaretAnimation: 'on',
  fontLigatures: false,
  scrollBeyondLastLine: false,
  smoothScrolling: true,
  automaticLayout: true,
  wordWrap: 'off',
  links: true,
  contextmenu: true,
  padding: { top: 8 },
  folding: true,
  renderWhitespace: 'selection',
  fontFamily:
    "'Cascadia Mono', 'SF Mono', Monaco, Menlo, Consolas, 'Ubuntu Mono', 'Liberation Mono', 'DejaVu Sans Mono', monospace",
  fontSize: 14,
  guides: {
    bracketPairs: true,
    indentation: true,
  },
  bracketPairColorization: { enabled: true },
  // IStandaloneEditorConstructionOptions
  value: '',
  language: 'python',
};

export const PYREFLY_SETTINGS_DEFS: NonNullable<IConfigurationNode['properties']> = {
  'python.pyrefly.typeCheckingMode': {},
  'python.pyrefly.disableLanguageServices': {},
  'python.analysis.autoImportCompletions': {},
  'python.analysis.inlayHints.variableTypes': {},
  'python.analysis.inlayHints.callArgumentNames': {},
  'python.analysis.inlayHints.functionReturnTypes': {},
  'python.pyrefly.diagnosticMode': {},
  'python.pyrefly.extraSearchPaths': {},
};

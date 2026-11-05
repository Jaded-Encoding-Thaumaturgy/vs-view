import { describe, expect, it, test } from 'vite-plus/test';

import { PYREFLY_SETTINGS_DEFS, defaultEditorOptions } from './config';

describe('Editor configuration', () => {
  describe('defaultEditorOptions', () => {
    it('configures correct indentation and formatting options', () => {
      expect(defaultEditorOptions.tabSize).toBe(4);
      expect(defaultEditorOptions.insertSpaces).toBe(true);
    });

    it('configures correct default theme and appearance options', () => {
      expect(defaultEditorOptions.theme).toBe('vs-dark');
      expect(defaultEditorOptions.lineNumbers).toBe('on');
      expect(defaultEditorOptions.glyphMargin).toBe(true);
      expect(defaultEditorOptions.wordWrap).toBe('off');
      expect(defaultEditorOptions.renderWhitespace).toBe('selection');
      expect(defaultEditorOptions.minimap).toEqual({ enabled: true });
    });

    it('configures font and typography defaults', () => {
      expect(defaultEditorOptions.fontSize).toBe(14);
      expect(defaultEditorOptions.fontLigatures).toBe(false);
      expect(defaultEditorOptions.fontFamily).toContain('Cascadia Mono');
      expect(defaultEditorOptions.fontFamily).toContain('monospace');
    });

    it('configures scrolling, layout, and UX animation options', () => {
      expect(defaultEditorOptions.scrollBeyondLastLine).toBe(false);
      expect(defaultEditorOptions.smoothScrolling).toBe(true);
      expect(defaultEditorOptions.automaticLayout).toBe(true);
      expect(defaultEditorOptions.mouseWheelZoom).toBe(true);
      expect(defaultEditorOptions.cursorBlinking).toBe('smooth');
      expect(defaultEditorOptions.cursorSmoothCaretAnimation).toBe('on');
      expect(defaultEditorOptions.links).toBe(true);
      expect(defaultEditorOptions.contextmenu).toBe(true);
      expect(defaultEditorOptions.padding).toEqual({ top: 8 });
    });

    it('configures code folding, guides, and bracket pair colorization', () => {
      expect(defaultEditorOptions.folding).toBe(true);
      expect(defaultEditorOptions.guides).toEqual({
        bracketPairs: true,
        indentation: true,
      });
      expect(defaultEditorOptions.bracketPairColorization).toEqual({ enabled: true });
    });

    it('configures standalone editor initial value and language', () => {
      expect(defaultEditorOptions.value).toBe('');
      expect(defaultEditorOptions.language).toBe('python');
    });
  });

  describe('PYREFLY_SETTINGS_DEFS', () => {
    const expectedKeys = [
      'python.pyrefly.typeCheckingMode',
      'python.pyrefly.disableLanguageServices',
      'python.analysis.autoImportCompletions',
      'python.analysis.inlayHints.variableTypes',
      'python.analysis.inlayHints.callArgumentNames',
      'python.analysis.inlayHints.functionReturnTypes',
      'python.pyrefly.diagnosticMode',
      'python.pyrefly.extraSearchPaths',
    ] as const;

    test.each(expectedKeys)("defines configuration property '%s'", (settingKey) => {
      expect(PYREFLY_SETTINGS_DEFS).toHaveProperty(settingKey);
      expect(PYREFLY_SETTINGS_DEFS[settingKey]).toEqual({});
    });

    it('contains exactly the expected set of Pyrefly configuration keys', () => {
      const actualKeys = Object.keys(PYREFLY_SETTINGS_DEFS);
      expect(actualKeys.length).toBe(expectedKeys.length);
      expect(new Set(actualKeys)).toEqual(new Set(expectedKeys));
    });

    it("prefixes all configuration keys with 'python.'", () => {
      for (const key of Object.keys(PYREFLY_SETTINGS_DEFS)) {
        expect(key.startsWith('python.')).toBe(true);
      }
    });
  });
});

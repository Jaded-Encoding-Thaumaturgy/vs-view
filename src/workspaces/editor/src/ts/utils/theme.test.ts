import { describe, expect, it, test } from "vitest";

import { DEFAULT_THEMES, GITHUB_THEMES, SUPPORTED_THEMES, getThemeDefinition } from "./theme";

describe("Theme utilities", () => {
  describe("ThemeKind mapping and values", () => {
    test.each([
      { kindValue: "dark", expectedUiTheme: "vs-dark" },
      { kindValue: "light", expectedUiTheme: "vs" },
      { kindValue: "hc-black", expectedUiTheme: "hc-black" },
      { kindValue: "hc-light", expectedUiTheme: "hc-light" },
    ] as const)(
      "asserts exact ThemeKind '$kindValue' maps to uiTheme '$expectedUiTheme'",
      ({ kindValue, expectedUiTheme }) => {
        const matchingDefs = Object.values(SUPPORTED_THEMES).filter(
          (theme) => theme.kind.value === kindValue,
        );
        expect(matchingDefs.length).toBeGreaterThan(0);

        for (const def of matchingDefs) {
          expect(def.kind.value).toBe(kindValue);
          expect(def.kind.uiTheme).toBe(expectedUiTheme);
          expect(def.kind.toString()).toBe(kindValue);
        }
      },
    );
  });

  describe("getThemeDefinition with DEFAULT_THEMES", () => {
    test.each(Object.entries(DEFAULT_THEMES))(
      "resolves default theme '%s'",
      (themeName, expectedDef) => {
        const result = getThemeDefinition(themeName);
        expect(result.ok).toBe(true);

        if (result.ok) {
          expect(result.value.id).toBe(expectedDef.id);
          expect(result.value.kind.value).toBe(expectedDef.kind.value);
          expect(result.value.kind.uiTheme).toBe(expectedDef.kind.uiTheme);
        }
      },
    );
  });

  describe("getThemeDefinition with GITHUB_THEMES", () => {
    test.each(GITHUB_THEMES)("resolves GitHub theme '$id'", (themeConfig) => {
      const result = getThemeDefinition(themeConfig.id);
      expect(result.ok).toBe(true);

      if (result.ok) {
        expect(result.value.id).toBe(themeConfig.id);
        expect(result.value.kind.value).toBe(themeConfig.kind.value);
        expect(result.value.kind.uiTheme).toBe(themeConfig.kind.uiTheme);
      }
    });
  });

  describe("getThemeDefinition with invalid or unknown themes", () => {
    test.each([
      "UnsupportedTheme",
      "Monokai",
      "Solarized Dark",
      "One Dark Pro",
      "github-dark",
      "dark",
      "",
      "   ",
    ])("returns Result.err for unknown theme '%s'", (themeName) => {
      const result = getThemeDefinition(themeName);
      expect(result.ok).toBe(false);

      if (!result.ok) {
        expect(result.error).toBeInstanceOf(Error);
        expect(result.error.message).toBe(`Unsupported theme: '${themeName}'`);
      }
    });
  });

  describe("SUPPORTED_THEMES integrity", () => {
    it("contains all entries from DEFAULT_THEMES", () => {
      for (const [key, def] of Object.entries(DEFAULT_THEMES)) {
        expect(SUPPORTED_THEMES).toHaveProperty(key);
        expect(SUPPORTED_THEMES[key]).toEqual(def);
      }
    });

    it("contains all entries from GITHUB_THEMES", () => {
      for (const theme of GITHUB_THEMES) {
        expect(SUPPORTED_THEMES).toHaveProperty(theme.id);
        expect(SUPPORTED_THEMES[theme.id]).toEqual({
          id: theme.id,
          kind: theme.kind,
        });
      }
    });

    it("has no duplicates or missing entries across DEFAULT_THEMES and GITHUB_THEMES", () => {
      const defaultKeys = Object.keys(DEFAULT_THEMES);
      const githubKeys = GITHUB_THEMES.map((theme) => theme.id);
      const supportedKeys = Object.keys(SUPPORTED_THEMES);

      // Verify no overlap between default theme keys and GitHub theme keys
      const intersection = defaultKeys.filter((key) => githubKeys.includes(key));
      expect(intersection).toHaveLength(0);

      // Verify total count matches the exact sum of unique default and GitHub themes
      expect(supportedKeys.length).toBe(defaultKeys.length + githubKeys.length);

      // Verify all supported theme keys match the combined set exactly
      const allExpectedKeys = new Set([...defaultKeys, ...githubKeys]);
      expect(new Set(supportedKeys)).toEqual(allExpectedKeys);
    });
  });
});

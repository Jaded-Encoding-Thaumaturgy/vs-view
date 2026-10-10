import * as path from "path";

import { type Plugin, defineConfig } from "vitest/config";

function fixVitestVscodeDeprecation(): Plugin {
  return {
    name: "fix-vitest-vscode-deprecation",
    configResolved(config) {
      const plugin = config.plugins.find((p) => p.name === "vitest:vscode-extension");
      const vitestApi = plugin?.api?.vitest;
      if (vitestApi?.experimental?.ignoreFsModuleCache !== undefined) {
        vitestApi.ignoreFsModuleCache = vitestApi.experimental.ignoreFsModuleCache;
        delete vitestApi.experimental.ignoreFsModuleCache;
      }
    },
  };
}

export default defineConfig({
  plugins: [fixVitestVscodeDeprecation()],
  test: {
    environment: "happy-dom",
    pool: "vmThreads",
    css: true,
    server: {
      deps: {
        inline: [/@codingame\//, "vscode"],
      },
    },
    fsModuleCache: true,
  },
  // Use relative paths so file:// loading works in QWebEngineView
  base: "./",
  root: path.resolve(import.meta.dirname, "src/ts"),
  resolve: {
    conditions: ["browser", "import", "module", "default"],
    alias: {
      "node:fs/promises": path.resolve(import.meta.dirname, "src/ts/stubs/empty.js"),
      "vscode-languageclient/browser": path.resolve(
        import.meta.dirname,
        "node_modules/vscode-languageclient/lib/browser/main.js",
      ),
    },
  },
  server: { forwardConsole: true },
  worker: { format: "es" },
  build: {
    outDir: path.resolve(import.meta.dirname, "src/python/vsview_editor/web_dist"),
    emptyOutDir: true,
    target: "chrome140", // try to remember to bump this when updating Qt
    modulePreload: false,
    assetsInlineLimit: 10240,
    reportCompressedSize: false,
    rolldownOptions: {
      output: { hashCharacters: "hex" },
    },
    chunkSizeWarningLimit: 10000,
  },
});

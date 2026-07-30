import * as path from "path";

import { defineConfig } from "vite";

import pkg from "./package.json" with { type: "json" };

export default defineConfig({
  // Use relative paths so file:// loading works in QWebEngineView
  base: "./",
  root: path.resolve(import.meta.dirname, "src/ts"),
  resolve: {
    alias: {
      "node:fs/promises": path.resolve(import.meta.dirname, "src/ts/stubs/empty.js"),
    },
  },
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
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

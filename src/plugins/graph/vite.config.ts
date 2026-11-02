import * as path from 'path';

import { defineConfig } from 'vite-plus';

export default defineConfig({
  test: {
    environment: 'happy-dom',
    css: true,
  },
  base: './',
  root: path.resolve(import.meta.dirname, 'src/ts'),
  resolve: {
    conditions: ['browser', 'import', 'module', 'default'],
  },
  build: {
    outDir: path.resolve(import.meta.dirname, 'src/python/vsview_graph/web_dist'),
    emptyOutDir: true,
    target: 'chrome140',
    modulePreload: false,
    assetsInlineLimit: 10240,
    reportCompressedSize: false,
    license: { fileName: 'THIRD_PARTY_LICENSES.md' },
    chunkSizeWarningLimit: 10000,
  },
});

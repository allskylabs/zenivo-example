// Plain object config (no 'import vite'): this dir has no node_modules; vite, astronomy-engine and three are borrowed from sibling checkouts that are not published yet (see README.md).
import { fileURLToPath } from 'node:url';

const AP = fileURLToPath(new URL('../../../astrophoto/', import.meta.url));
const HU = fileURLToPath(new URL('../../../hoan.uk/client/node_modules/', import.meta.url));
const me = (p) => fileURLToPath(new URL(p, import.meta.url));

export default {
  root: me('.'),
  resolve: {
    alias: {
      '@astrophoto/sky-engine': AP + 'packages/sky-engine/src/index.ts',
      '@astrophoto/sky-data': AP + 'packages/sky-data/src/index.ts',
      'astronomy-engine': AP + 'node_modules/astronomy-engine/esm/astronomy.js',
      'satellite.js': me('./stubs/satellite.js'),
      three: HU + 'three/build/three.module.js',
    },
  },
  server: { fs: { allow: [AP, HU, me('../../')] } },
  build: {
    outDir: me('../../src/web/sky/'), emptyOutDir: true, minify: 'esbuild', target: 'es2020', modulePreload: false,
    rollupOptions: { input: me('./src/main.ts'), output: { format: 'es', manualChunks: (id) => (id.endsWith('/src/main.ts') || id.endsWith('/src/dome3d.ts') || id.includes('/three/') ? undefined : 'core'), entryFileNames: 'sky.js', chunkFileNames: '[name]-[hash].js' } },
  },
};

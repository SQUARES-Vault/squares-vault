import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Single source of truth for site URL and base path
export const SITE_URL = 'https://squares-vault.github.io';
export const BASE_PATH = '/squares-vault';

function staticHtmlAliases() {
  return {
    name: 'static-html-aliases',
    hooks: {
      'astro:build:done': async ({ dir }) => {
        const outDir = fileURLToPath(dir);
        const pairs = [
          ['privacy', 'privacy.html'],
          ['terms', 'terms.html'],
        ];
        for (const [folder, targetFile] of pairs) {
          const src = path.join(outDir, folder, 'index.html');
          const dst = path.join(outDir, targetFile);
          if (fs.existsSync(src)) {
            fs.copyFileSync(src, dst);
            console.log(`[static-html-aliases] Copied ${folder}/index.html -> ${targetFile}`);
          }
        }

        // Ensure .nojekyll exists in outDir
        const nojekyllPath = path.join(outDir, '.nojekyll');
        if (!fs.existsSync(nojekyllPath)) {
          fs.writeFileSync(nojekyllPath, '');
          console.log('[static-html-aliases] Created .nojekyll in outDir');
        }
      },
    },
  };
}

export default defineConfig({
  site: SITE_URL,
  base: BASE_PATH,
  outDir: '../docs',
  trailingSlash: 'ignore',
  build: {
    assets: 'assets',
  },
  integrations: [
    sitemap({
      filter: (page) => !page.includes('/404'),
    }),
    staticHtmlAliases(),
  ],
});

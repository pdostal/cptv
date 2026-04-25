#!/usr/bin/env node
// Copy vendored JS/CSS assets from node_modules into cptv/static/ for serving.
// Keep this script small; npm handles the heavy lifting.

import { mkdir, copyFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const out = resolve(root, "cptv", "static", "vendor");

const files = [
  ["node_modules/htmx.org/dist/htmx.min.js", "htmx.min.js"],
  ["node_modules/htmx-ext-sse/dist/sse.min.js", "htmx-ext-sse.min.js"],
  ["node_modules/@picocss/pico/css/pico.min.css", "pico.min.css"],
];

await mkdir(out, { recursive: true });
for (const [src, dest] of files) {
  const from = resolve(root, src);
  const to = resolve(out, dest);
  await copyFile(from, to);
  console.log(`copied ${src} -> cptv/static/vendor/${dest}`);
}

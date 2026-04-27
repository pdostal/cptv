#!/usr/bin/env node
// Copy vendored JS/CSS assets from node_modules into cptv/static/ for serving.
// Keep this script small; npm handles the heavy lifting.

import { mkdir, copyFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const out = resolve(root, "cptv", "static", "vendor");

// Leaflet's CSS references marker-icon.png / marker-shadow.png via a
// relative URL (./images/...), so the marker images must live under
// cptv/static/vendor/images/ — adjacent to leaflet.css — for the default
// marker to render without 404s.
const files = [
  ["node_modules/htmx.org/dist/htmx.min.js", "htmx.min.js"],
  ["node_modules/@picocss/pico/css/pico.min.css", "pico.min.css"],
  ["node_modules/leaflet/dist/leaflet.js", "leaflet.js"],
  ["node_modules/leaflet/dist/leaflet.css", "leaflet.css"],
  ["node_modules/leaflet/dist/images/marker-icon.png", "images/marker-icon.png"],
  ["node_modules/leaflet/dist/images/marker-icon-2x.png", "images/marker-icon-2x.png"],
  ["node_modules/leaflet/dist/images/marker-shadow.png", "images/marker-shadow.png"],
];

await mkdir(out, { recursive: true });
await mkdir(resolve(out, "images"), { recursive: true });
for (const [src, dest] of files) {
  const from = resolve(root, src);
  const to = resolve(out, dest);
  await copyFile(from, to);
  console.log(`copied ${src} -> cptv/static/vendor/${dest}`);
}

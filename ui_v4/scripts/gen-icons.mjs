#!/usr/bin/env node
// ADR-0071 P2 — One-off PNG icon generator from mark-v4.svg.
//
// Usage: from ui_v4 dir:  node scripts/gen-icons.mjs
//
// Re-run policy: тільки коли public/brand/mark-v4.svg змінюється. Outputs
// commited у public/icons/ — production builds НЕ потребують sharp at deploy.
//
// Generated PNGs:
//   public/icons/icon-192.png          — manifest standard (Android home)
//   public/icons/icon-512.png          — manifest standard + Android splash
//   public/icons/icon-maskable-512.png — Android adaptive (60% safe area
//                                       з dark canvas #0D1117 background)
//   public/icons/apple-touch-icon-180.png — iOS home screen
//   public/icons/favicon-32.png        — modern browsers HiDPI
//   public/icons/favicon-16.png        — modern browsers LDPI
//   public/icons/og-1200x630.png       — Open Graph share preview (bonus)
//
// Implementation: sharp (devDep). Pure render — no design changes to V3.

import sharp from 'sharp';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC_SVG = resolve(__dirname, '../public/brand/mark-v4.svg');
const OUT_DIR = resolve(__dirname, '../public/icons');
const DARK_BG = '#0D1117'; // ADR-0066 canonical dark canvas (also manifest theme/bg)

mkdirSync(OUT_DIR, { recursive: true });
const svgBuffer = readFileSync(SRC_SVG);

async function renderPlain(size, outName) {
  await sharp(svgBuffer, { density: 512 })
    .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(resolve(OUT_DIR, outName));
  console.log(`  ✓ ${outName}  (${size}×${size}, transparent bg)`);
}

async function renderMaskable(size, outName) {
  // Android maskable: mark INSET to 60% canvas (20% safe-area each side),
  // composited на solid dark bg. Browsers crop to circle/squircle dynamically.
  const inner = Math.round(size * 0.6);
  const innerPng = await sharp(svgBuffer, { density: 512 })
    .resize(inner, inner, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toBuffer();
  const offset = Math.round((size - inner) / 2);
  await sharp({
    create: { width: size, height: size, channels: 4, background: DARK_BG },
  })
    .composite([{ input: innerPng, top: offset, left: offset }])
    .png()
    .toFile(resolve(OUT_DIR, outName));
  console.log(`  ✓ ${outName}  (${size}×${size}, maskable 60% inset on ${DARK_BG})`);
}

async function renderOg() {
  // OG image: mark centered on dark canvas, 1200×630. Mark size ~300px.
  const markSize = 300;
  const markPng = await sharp(svgBuffer, { density: 512 })
    .resize(markSize, markSize, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toBuffer();
  await sharp({
    create: { width: 1200, height: 630, channels: 4, background: DARK_BG },
  })
    .composite([{ input: markPng, gravity: 'center' }])
    .png()
    .toFile(resolve(OUT_DIR, 'og-1200x630.png'));
  console.log('  ✓ og-1200x630.png  (1200×630, mark centered on dark)');
}

console.log(`Reading source: ${SRC_SVG}`);
console.log(`Output dir:     ${OUT_DIR}\n`);

await Promise.all([
  renderPlain(192, 'icon-192.png'),
  renderPlain(512, 'icon-512.png'),
  renderMaskable(512, 'icon-maskable-512.png'),
  renderPlain(180, 'apple-touch-icon-180.png'),
  renderPlain(32, 'favicon-32.png'),
  renderPlain(16, 'favicon-16.png'),
  renderOg(),
]);

console.log('\nDone. Commit `public/icons/*.png` to repo. Re-run on mark change.');

import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const exportRoots = [join(root, 'out'), join(root, 'dist', 'client')];
const exportRoot = exportRoots.find((candidate) => existsSync(join(candidate, 'index.html')));
if (!exportRoot) {
  console.error('Static export checks failed: no out/index.html or dist/client/index.html');
  process.exit(1);
}
const html = readFileSync(join(exportRoot, 'index.html'), 'utf8');
const checks = [
  ['product title', html.includes('特效感知的可编辑剪辑工作流')],
  ['hero headline', html.includes('让参考风格、真实节拍与叙事')],
  ['case metric', html.includes('0.696807')],
  ['rights control', html.includes('我确认有权使用提供的媒体')],
  ['open graph image', html.includes('og.png')],
  ['reduced motion CSS', readFileSync(join(root, 'app', 'globals.css'), 'utf8').includes('prefers-reduced-motion')],
  ['og dimensions budget', statSync(join(root, 'public', 'og.png')).size < 500_000],
];
const failures = checks.filter(([, ok]) => !ok).map(([name]) => name);
if (failures.length) {
  console.error(`Static export checks failed: ${failures.join(', ')}`);
  process.exit(1);
}
console.log(`Static export checks passed (${checks.length}/${checks.length}).`);

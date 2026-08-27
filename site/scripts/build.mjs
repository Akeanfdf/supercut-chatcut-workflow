import { existsSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const siteRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const outputRoot = join(siteRoot, 'dist');
rmSync(outputRoot, { recursive: true, force: true });

const cli = join(siteRoot, 'node_modules', 'vinext', 'dist', 'cli.js');
const result = spawnSync(process.execPath, [cli, 'build'], { cwd: siteRoot, encoding: 'utf8' });
process.stdout.write(result.stdout ?? '');
process.stderr.write(result.stderr ?? '');

const required = [
  join(outputRoot, 'client', 'index.html'),
  join(outputRoot, 'client', 'og.png'),
  join(outputRoot, 'client', '_next'),
];
const complete = required.every(existsSync);
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
if (result.status && complete && (result.stderr ?? '').includes('UV_HANDLE_CLOSING')) {
  console.warn('vinext completed the static export; ignoring its Windows libuv shutdown assertion.');
  process.exit(0);
}
process.exit(result.status ?? (complete ? 0 : 1));

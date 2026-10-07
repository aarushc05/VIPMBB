import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
const violations = [];
function scan(path) {
  if (!existsSync(path)) return;
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    const child = join(path, entry.name);
    if (entry.isSymbolicLink()) violations.push(child);
    else if (entry.isDirectory()) scan(child);
    else if (/\.(sqlite3?|db)(\.|$)|\.local\.json$|^\.env(?:\.|$)/i.test(entry.name)) violations.push(child);
  }
}
for (const path of process.argv.slice(2)) scan(path);
if (violations.length) {
  console.error('Build blocked: private databases, environment files or symlinks found in publishable assets.');
  console.error(violations.join('\n'));
  console.error('Build only from a clean source checkout without protected data. Never publish your working cache.');
  process.exit(1);
}
console.log('Publishable assets contain no private cache/environment files.');

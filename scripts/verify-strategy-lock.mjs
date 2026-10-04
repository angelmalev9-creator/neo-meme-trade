import fs from 'node:fs';
import crypto from 'node:crypto';
const lock = JSON.parse(fs.readFileSync(new URL('../strategy-lock.json', import.meta.url), 'utf8'));
const file = fs.readFileSync(new URL('../' + lock.strategy_file, import.meta.url));
const actual = crypto.createHash('sha256').update(file).digest('hex');
if (actual !== lock.sha256) {
  console.error(`STRATEGY LOCK FAILED: ${lock.strategy_file} changed.\nExpected ${lock.sha256}\nActual   ${actual}`);
  process.exit(1);
}
console.log(`STRATEGY LOCK OK: ${lock.production_strategy_id} ${actual}`);

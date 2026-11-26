// CI copies this probe into the pinned Nexa checkout's apps/api directory.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { openNxpkg } from './src/infrastructure/nxpkg/reader.js';
import {
  planHistoryFiles,
  readHistoryRecord,
} from './src/modules/platform/legacy-history/application/record-map.js';

const [directory, file] = process.argv.slice(2);
assert(directory && file);
const keyFileText = await readFile(join(directory, 'synthetic.nxkey'), 'utf8');
const pkg = await openNxpkg(join(directory, file), { keyFileText }, {
  workDir: join(directory, 'nexa-reader'),
  maxPayloadBytes: 64 * 1024 * 1024,
  maxFiles: 1000,
  maxFileBytes: 32 * 1024 * 1024,
});
try {
  assert.equal(pkg.manifest.package_schema_version, '1.5.0');
  const plan = planHistoryFiles(pkg.files().map(({ path }) => path));
  const expectedCarry = new Set([
    'records/archive/invoice_carried.jsonl',
    'records/archive/service_other_carried.jsonl',
    'records/archive/reagent_report_carried.jsonl',
    'records/configuration/setting_carried.jsonl',
  ]);
  const keys = new Set<string>();
  for (const entry of plan.files) {
    let line = 0;
    for await (const data of pkg.iterJsonl(entry.path)) {
      const row = readHistoryRecord(entry, ++line, data);
      assert(!keys.has(row.idempotencyKey), 'duplicate history record key');
      keys.add(row.idempotencyKey);
      if (expectedCarry.has(entry.path)) {
        assert.equal(row.payload['schema'], 'm2n.carried_columns.v1');
        assert.equal(row.payload['carry'], 'supplement');
      }
    }
    if (line > 0) expectedCarry.delete(entry.path);
  }
  assert.equal(expectedCarry.size, 0, 'missing supplementary history');
  console.log(`Nexa authenticated and validated ${keys.size} synthetic history records.`);
} finally {
  await pkg.close();
}

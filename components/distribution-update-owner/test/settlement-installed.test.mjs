import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, readFile, copyFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
import {installConsumer} from './npm-consumer.mjs';

const execute = promisify(execFile);
const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const hostArchive = process.env.DISTRIBUTION_SETTLEMENT_HOST_ARCHIVE;

test('installed supervisor and real host settle a slow 20-to-21-owner replacement', {skip: !hostArchive}, async t => {
  const root = await mkdtemp(join(tmpdir(), 'settlement-installed-'));
  t.after(() => rm(root, {recursive:true, force:true}));
  const packed = JSON.parse((await execute('npm', ['pack', '--ignore-scripts', '--json', '--pack-destination', root], {cwd:packageRoot})).stdout)[0];
  const consumer = join(root, 'consumer');
  await mkdir(consumer);
  await writeFile(join(consumer, 'package.json'), JSON.stringify({name:'settlement-fixture', private:true, type:'module'}));
  await installConsumer(consumer, [join(root, packed.filename), resolve(hostArchive)]);
  await copyFile(join(packageRoot, 'test/settlement-worker.mjs'), join(consumer, 'worker.mjs'));
  const result = await execute(process.execPath, ['worker.mjs', root], {cwd:consumer, timeout:90000});
  const receipt = JSON.parse(result.stdout);
  assert.equal(receipt.automaticSettlement, 'settled');
  assert.equal(receipt.reconciliationSettlement, 'settled');
  const evidence = {
    ...receipt,
    ownerArtifactSha256:createHash('sha256').update(await readFile(join(root, packed.filename))).digest('hex'),
    hostArtifactSha256:createHash('sha256').update(await readFile(hostArchive)).digest('hex'),
  };
  if (process.env.DISTRIBUTION_SETTLEMENT_ACCEPTANCE_DIR) {
    const output = resolve(process.env.DISTRIBUTION_SETTLEMENT_ACCEPTANCE_DIR);
    await mkdir(output, {recursive:true});
    await writeFile(join(output, 'settlement-installed.json'), JSON.stringify(evidence,null,2)+'\n');
    await copyFile(join(root, packed.filename), join(output, packed.filename));
  }
});

// Trusted source-side staging tool, not a runtime launcher or publisher.
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {assembleSourceResolutionManifest, verifySourceAssemblyBeforeReady} from '../src/source-assembly.mjs';
const [mode,path,expected,...extra] = process.argv.slice(2);
try {
  if (!['emit','verify'].includes(mode) || !path || !/^[a-f0-9]{64}$/.test(expected ?? '') || extra.length) throw Error('source_assembly_usage_invalid');
  const bytes = await readFile(path);
  if (bytes.length > 32*1024*1024 || createHash('sha256').update(bytes).digest('hex') !== expected) throw Error('source_assembly_plan_digest_mismatch');
  const plan = JSON.parse(bytes);
  if (mode === 'emit') process.stdout.write(JSON.stringify(assembleSourceResolutionManifest(plan).manifest)+'\n');
  else {
    const result = await verifySourceAssemblyBeforeReady(plan);
    process.stdout.write(JSON.stringify({status:'assembly-verified',physicalFiles:result.inventory.length,initialization:result.initialization})+'\n');
  }
} catch (error) {
  const code = /^source_assembly_[a-z_]+$/.test(error.message) ? error.message : 'source_assembly_verification_failed';
  process.stderr.write(JSON.stringify({status:'refused',code})+'\n'); process.exitCode = 1;
}

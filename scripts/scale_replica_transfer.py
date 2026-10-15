#!/usr/bin/env python3
"""Resumable, sequential private capture/extraction; no live-host files created."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time


def run(args):
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = args.output / 'raw'; raw.mkdir(exist_ok=True, mode=0o700)
    source = Path(__file__).with_name('scale_replica_capture.py')
    phases = [('databases',1), ('primary',8), ('artifacts',8), ('ancillary',1), ('events',32)]
    for phase, shards in phases:
        if phase not in args.phases:
            continue
        for shard in range(shards):
            name = phase if shards == 1 else f'{phase}-{shard:02}'
            archive = raw / (name + '.tar.zst')
            verified = args.output / (name + '-verified.json')
            if verified.exists():
                old = json.loads(verified.read_text())
                if not old.get('allHashesMatch') or not archive.is_file():
                    raise ValueError('Invalid resume receipt')
                continue
            # Quiet measurement requests take effect only between complete
            # shards. Never freeze a live Incus websocket or an active source
            # reader halfway through a transfer.
            if args.pause_file:
                marker = args.pause_file.with_name(args.pause_file.name + '.ack')
                while args.pause_file.exists():
                    marker.write_text(json.dumps({'phase': phase, 'nextShard': shard,
                                                  'pausedAtBoundary': True}) + '\n')
                    time.sleep(1)
                marker.unlink(missing_ok=True)
            health = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',args.host,
                'curl -k -sS --connect-timeout 3 --max-time 8 -o /dev/null -w "%{http_code} %{time_total}" https://127.0.0.1:8443/api/health'],
                capture_output=True,text=True,timeout=12,check=True).stdout.split()
            if not health or health[0] != '200' or float(health[1]) > 1:
                raise RuntimeError('Source health guard refused further capture')
            if not archive.exists():
                command = ['ionice','-c3','nice','-n15','python3','-',
                           '--app',args.app,'--native',args.native,'--phase',phase,
                           '--shards',str(shards),'--shard',str(shard),'--rate-mib',str(args.rate_mib)]
                remote = 'set -o pipefail; ' + shlex.join(command) + ' | nice -n15 zstd -1 -T1 -c'
                partial = archive.with_suffix(archive.suffix + '.partial')
                with source.open('rb') as stdin, partial.open('xb') as stdout, (args.output/(name+'-capture.log')).open('ab') as stderr:
                    subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',args.host,remote],stdin=stdin,stdout=stdout,stderr=stderr,check=True)
                partial.rename(archive)
            with (args.output/(name+'-extract.log')).open('ab') as errors:
                unpack = subprocess.Popen(['zstd','-dc',str(archive)],stdout=subprocess.PIPE,stderr=errors)
                result = subprocess.run(['incus','exec',args.dtu,'--','nice','-n15','python3',
                    '/opt/unified-scale-replica-20261015/tools/scale_replica_extract.py','--root',args.seed],
                    stdin=unpack.stdout,stdout=subprocess.PIPE,stderr=errors,text=True)
                unpack.stdout.close()
                code = unpack.wait()
                if code or result.returncode:
                    raise RuntimeError('Snapshot extraction failed; preserved archive and log')
            receipt = json.loads(result.stdout)
            digest = hashlib.sha256()
            with archive.open('rb') as data:
                while block := data.read(1024*1024): digest.update(block)
            receipt.update(archiveBytes=archive.stat().st_size, archiveSha256=digest.hexdigest(),
                           completedAt=time.time(), sourceHealthSeconds=float(health[1]))
            verified.write_text(json.dumps(receipt,indent=2)+'\n'); archive.chmod(0o400)
            print(json.dumps({'completed':name,**receipt}),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--host',required=True)
    parser.add_argument('--app',required=True)
    parser.add_argument('--native',required=True)
    parser.add_argument('--dtu',required=True)
    parser.add_argument('--seed',required=True)
    parser.add_argument('--rate-mib',type=float,default=80)
    parser.add_argument('--phases',nargs='+',default=['databases','primary','artifacts','events'])
    parser.add_argument('--pause-file',type=Path)
    run(parser.parse_args())

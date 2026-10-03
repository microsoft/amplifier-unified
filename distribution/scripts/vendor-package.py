#!/usr/bin/env python3
"""Record one public npm artifact by content identity for an isolated composition."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('archive',type=Path)
parser.add_argument('--repository',required=True)
parser.add_argument('--revision',required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parent.parent
raw=args.archive.read_bytes();digest=hashlib.sha256(raw).hexdigest()
with tarfile.open(args.archive,'r:gz') as archive:
    entry=archive.getmember('package/package.json')
    if not entry.isfile() or entry.size>128*1024:raise ValueError('Bounded package manifest required')
    package=json.load(archive.extractfile(entry))
name=package['name']
if not name.startswith('@amplifier/') or '/' in name.split('/',1)[1]:raise ValueError('An Amplifier public package is required')
if not args.repository.startswith('microsoft/') or len(args.revision)!=40 or any(c not in '0123456789abcdef' for c in args.revision):raise ValueError('Exact repository and commit receipt required')
target=Path('vendor')/(name.split('/')[1]+'-'+digest[:12]+'.tgz')
(root/target).write_bytes(raw)
manifest=root/'package.json';data=json.loads(manifest.read_text());data['dependencies'][name]='file:'+str(target);data['bundleDependencies']=list(data['dependencies']);manifest.write_text(json.dumps(data,indent=2)+'\n')
receipt=root/'components.json';state=json.loads(receipt.read_text()) if receipt.exists() else {'formatVersion':1,'status':'integration-candidate','components':{}}
state['components'][name]={**state['components'].get(name,{}),'repository':args.repository,'revision':args.revision,'version':package['version'],'artifact':str(target),'sha256':digest}
receipt.write_text(json.dumps(state,indent=2,sort_keys=True)+'\n')
print(name+' '+package['version']+' '+digest[:12])

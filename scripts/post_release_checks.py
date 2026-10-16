"""Verify published release identity/bytes and track asynchronous check failures.

Only the reporting job receives issue-write permission. This script never
publishes, replaces assets, changes tags, or installs anything on a shared host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

CHECKS = ('resolve', 'installed-package', 'readiness', 'python', 'browser', 'integrations')


def gh(*args, payload=None):
    command = ['gh', *args]
    if payload is not None:
        command += ['--input', '-']
    result = subprocess.check_output(command, input=None if payload is None else json.dumps(payload), text=True)
    return json.loads(result) if result.strip() else None


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Expected a stable release version')
    return value


def resolve_candidate(candidate, release, tag_revision):
    version = stable_version(candidate.get('version'))
    revision = candidate.get('revision', '')
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Expected an immutable release revision')
    if (release.get('tagName') != 'v' + version or release.get('isDraft') is not False
            or release.get('isPrerelease') is not False or not release.get('publishedAt')):
        raise ValueError('Candidate has no matching published stable release')
    if tag_revision != revision:
        raise ValueError('Published tag differs from the qualified candidate')
    return {'version': version, 'tag': 'v' + version, 'revision': revision}


def published_revision(repository, tag):
    obj = gh('api', f'repos/{repository}/git/ref/tags/{tag}')['object']
    for _ in range(4):
        if obj['type'] == 'commit':
            return obj['sha']
        if obj['type'] != 'tag':
            break
        obj = gh('api', f'repos/{repository}/git/tags/{obj["sha"]}')['object']
    raise ValueError('Release tag does not resolve to a commit')


def verify_downloads(destination, version):
    version = stable_version(version)
    expected = {f'amplifier_unified-{version}-py3-none-any.whl', f'amplifier_unified-{version}.tar.gz'}
    entries = {}
    for line in (destination / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split()
        if name not in expected or name in entries or not re.fullmatch(r'[0-9a-f]{64}', checksum):
            raise ValueError('Unexpected or duplicate release checksum entry')
        entries[name] = checksum
    if set(entries) != expected:
        raise ValueError('Published checksums do not cover both distributions')
    for name, checksum in entries.items():
        with (destination / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                raise ValueError(f'Published asset checksum mismatch: {name}')
    report = {'version': version, 'assets': entries, 'verified': True}
    (destination / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def build_report(results, repository, run_id, publication_id):
    outputs = results.get('resolve', {}).get('outputs', {})
    tag = outputs.get('tag') or f'publication-run-{publication_id}'
    revision = outputs.get('revision') or 'Unresolved; see identity verification failure'
    failed = [name for name in CHECKS if results.get(name, {}).get('result') != 'success']
    marker = f'<!-- amplifier-post-release:{tag} -->'
    title = f'Post-release verification: {tag}'
    body = '\n'.join([
        marker, f'Post-release verification **{"failed" if failed else "passed"}** for `{tag}`.',
        f'Published revision: `{revision}`.', '',
        f'[Verification run](https://github.com/{repository}/actions/runs/{run_id}) · '
        f'[Publication run](https://github.com/{repository}/actions/runs/{publication_id})', '',
        *[f'- {name}: {results.get(name, {}).get("result", "missing")}' for name in CHECKS], '',
        'These checks run after publication. This report does not withdraw the release, '
        'move a tag, replace assets, or change installed applications.',
    ])
    return {'failed': failed, 'marker': marker, 'title': title, 'body': body}


def track_report(repository, report):
    # Read all pages so reruns update the existing per-release issue, including
    # a previously closed issue. No issue is created for an all-green release.
    pages = gh('api', '--paginate', '--slurp', f'repos/{repository}/issues?state=all&per_page=100')
    issue = next((row for page in pages for row in page
                  if 'pull_request' not in row and report['marker'] in (row.get('body') or '')), None)
    if issue:
        gh('api', '--method', 'PATCH', f'repos/{repository}/issues/{issue["number"]}', payload={
            'body': report['body'], 'state': 'open' if report['failed'] else 'closed',
            'state_reason': 'reopened' if report['failed'] else 'completed',
        })
    elif report['failed']:
        gh('api', '--method', 'POST', f'repos/{repository}/issues', payload={
            'title': report['title'], 'body': report['body'],
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve = commands.add_parser('resolve')
    resolve.add_argument('--candidate', type=Path, required=True)
    resolve.add_argument('--output', type=Path, required=True)
    download = commands.add_parser('download')
    download.add_argument('--version', required=True)
    download.add_argument('--destination', type=Path, required=True)
    commands.add_parser('report')
    args = parser.parse_args()
    repository = os.environ['GITHUB_REPOSITORY']
    if args.command == 'resolve':
        candidate = json.loads(args.candidate.read_text())
        tag = 'v' + stable_version(candidate.get('version'))
        release = gh('release', 'view', tag, '--repo', repository,
                     '--json', 'tagName,isDraft,isPrerelease,publishedAt')
        result = resolve_candidate(candidate, release, published_revision(repository, tag))
        with args.output.open('a') as stream:
            stream.write(''.join(f'{key}={value}\n' for key, value in result.items()))
    elif args.command == 'download':
        version = stable_version(args.version)
        args.destination.mkdir(parents=True, exist_ok=False)
        subprocess.run(['gh', 'release', 'download', 'v' + version, '--repo', repository,
                        '--pattern', f'amplifier_unified-{version}-py3-none-any.whl',
                        '--pattern', f'amplifier_unified-{version}.tar.gz', '--pattern', 'SHA256SUMS',
                        '--dir', str(args.destination)], check=True)
        verify_downloads(args.destination, version)
    else:
        report = build_report(json.loads(os.environ['RELEASE_RESULTS']), repository,
                              os.environ['GITHUB_RUN_ID'], os.environ['RELEASE_RUN_ID'])
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
            stream.write(report['body'] + '\n')
        track_report(repository, report)
        if report['failed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()

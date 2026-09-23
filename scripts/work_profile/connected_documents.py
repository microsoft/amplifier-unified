"""Print an optional native bundle overlay; never apply settings or sign in."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def profile(m365_source, *, base_bundle, state_dir=None, auth_config=None,
            tenant=None, account_id=None, cloud=True, live_url=None,
            live_token_file=None, live_ca_file=None):
    source = Path(m365_source).expanduser().resolve()
    if not (source / 'behaviors/m365-documents.yaml').is_file():
        raise ValueError('Choose a checked-out microsoft/amplifier-m365 contribution')
    if not base_bundle:
        raise ValueError('Choose the existing base bundle to compose')
    result = {'bundle': {'name': 'connected-documents-profile'},
              'includes': [{'bundle': str(base_bundle)}], 'tools': []}
    if cloud:
        if not state_dir:
            raise ValueError('Choose a private cloud receipt/export directory')
        result['includes'].append({'bundle': str(source / 'behaviors/m365-documents.yaml')})
        config = {'connected_documents': True, 'auth_mode': 'device_code',
                  'state_dir': str(Path(state_dir).expanduser().resolve())}
        if tenant: config['tenant'] = tenant
        if account_id: config['account_id'] = account_id
        if auth_config:
            result['tools'].append({'module': 'tool-m365-auth',
                'config': {'config_path': str(Path(auth_config).expanduser().resolve())}})
        result['tools'].append({'module': 'tool-m365-documents', 'config': config})
    if any((live_url, live_token_file, live_ca_file)):
        if not all((live_url, live_token_file, live_ca_file)):
            raise ValueError('Live Excel requires its loopback URL, host token file and CA file')
        if not (source / 'behaviors/m365-office-bridge.yaml').is_file():
            raise ValueError('The selected upstream checkout lacks the Office bridge contribution')
        result['includes'].append({'bundle': str(source / 'behaviors/m365-office-bridge.yaml')})
        result['tools'].append({'module': 'tool-m365-office-bridge', 'config': {
            'enabled': True, 'url': live_url,
            'token_file': str(Path(live_token_file).expanduser().resolve()),
            'ca_file': str(Path(live_ca_file).expanduser().resolve())}})
    elif not cloud:
        raise ValueError('Select cloud documents or configure live Excel')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('m365_source', help='Local checkout of the existing upstream contribution')
    parser.add_argument('--base-bundle', required=True)
    parser.add_argument('--state-dir')
    parser.add_argument('--auth-config')
    parser.add_argument('--tenant')
    parser.add_argument('--account-id')
    parser.add_argument('--live-only', action='store_true')
    parser.add_argument('--live-url')
    parser.add_argument('--live-token-file')
    parser.add_argument('--live-ca-file')
    args = parser.parse_args()
    values = vars(args)
    values['cloud'] = not values.pop('live_only')
    print(json.dumps(profile(**values), indent=2))


if __name__ == '__main__':
    main()

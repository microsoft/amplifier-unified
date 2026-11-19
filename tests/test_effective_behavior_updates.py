"""Effective app behavior inventory: read-only selection and exact checked batches."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from amplifier_web import builtin_behaviors, update_sequence, updates
from amplifier_web.host.config import HostConfig

URL = 'https://example.invalid/behavior'
URI = 'git+' + URL + '@main#subdirectory=behavior.yaml'


def settings(app=None, **extra):
    return {'bundle': {'app': app or []},
            'web_bundles': {'excluded': list(builtin_behaviors.DEFAULT_BEHAVIORS)}, **extra}


def config(tmp_path, value):
    return HostConfig(tmp_path, tmp_path, value, tmp_path/'foundation', tmp_path/'shared', True)


def selection(tmp_path, value):
    return update_sequence.app_selection(tmp_path, config=config(tmp_path, value))


@pytest.mark.parametrize('ref', ['main', 'stable', 'v1.2.3', 'a'*40])
def test_effective_selection_preserves_authored_ref_and_override(tmp_path, ref):
    chosen = 'git+' + URL + '@' + ref + '#subdirectory=behavior.yaml'
    value = settings([URI], sources={'bundles': {URI: chosen}})
    before = copy.deepcopy(value)
    result = selection(tmp_path, value)
    assert result['sources'] == [{'url': URL, 'ref': ref}]
    assert value == before
    assert not list(tmp_path.iterdir()), 'Selection must not create a registry/cache'


@pytest.mark.parametrize('kind', ['path', 'managed-cache', 'disabled'])
def test_local_or_disabled_does_not_restore_default_remote(tmp_path, kind):
    uri = builtin_behaviors.ROUTING_BEHAVIOR_URI
    if kind == 'disabled':
        value = settings()
    else:
        local = tmp_path/('foundation/cache/local.yaml' if kind == 'managed-cache' else 'local.yaml')
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text('bundle: {name: local}\n')
        value = settings([uri], sources={'bundles': {uri: str(local)}})
    assert selection(tmp_path, value)['sources'] == []


def test_alias_source_override_matches_configured_bundle_precedence(tmp_path):
    value = settings(['Mixed'], sources={'bundles': {'mixed': URI, URI: 'git+'+URL+'@stable'}})
    # Host selection applies an override once, then BundleRegistry.load performs
    # its single exact registration lookup (including a URI registration key).
    assert selection(tmp_path, value)['sources'] == [{'url': URL, 'ref': 'stable'}]
    registered = settings(['Mixed'], sources={'bundles': {URI: 'git+'+URL+'@stable'}})
    registered['bundle']['added'] = {'mixed': URI}
    assert selection(tmp_path, registered)['sources'] == [{'url': URL, 'ref': 'stable'}]
    value['sources']['bundles']['MIXED'] = 'git+'+URL+'@other'
    with pytest.raises(ValueError, match='Ambiguous'):
        selection(tmp_path, value)


@pytest.mark.parametrize('via_alias', [False, True])
async def test_inventory_reference_matches_actual_registry_resolution(tmp_path, monkeypatch, via_alias):
    from amplifier_web.host.session import session_registry, load_configured_bundle
    from amplifier_foundation.paths.resolution import ResolvedSource
    target=tmp_path/'observed.yaml'
    target.write_text('bundle: {name: observed, version: "1"}\n')
    value=settings(['mixed' if via_alias else URI],
                   sources={'bundles':{URI:'git+'+URL+'@stable'}})
    if via_alias:
        value['sources']['bundles']['mixed']=URI
    cfg=config(tmp_path,value);registry=session_registry(cfg);registry.register(cfg.registrations)
    observed=[]
    async def resolve(uri):
        observed.append(uri)
        return ResolvedSource(active_path=target,source_root=tmp_path)
    monkeypatch.setattr(registry._source_resolver,'resolve',resolve)
    await load_configured_bundle(registry,cfg,value['bundle']['app'][0])
    assert observed==['git+'+URL+'@stable']
    assert selection(tmp_path,value)['sources']==[update_sequence.git_source(observed[0])]


def test_unbound_and_circular_aliases_do_not_authorize_cached_sources(tmp_path):
    with pytest.raises(ValueError, match='unresolved'):
        selection(tmp_path, settings(['not-registered']))
    with pytest.raises(ValueError, match='unresolved'):
        selection(tmp_path, settings(['one'], sources={'bundles': {'one':'two', 'two':'one'}}))
    # The runtime registry does not recursively follow aliases, even if the
    # last alias in an invented chain would identify a remote source.
    value = settings(['one'])
    value['bundle']['added'] = {'one':'two', 'two':URI}
    with pytest.raises(ValueError, match='unresolved'):
        selection(tmp_path, value)


def test_disabled_shipped_shell_does_not_select_its_skills_dependency(tmp_path):
    result = selection(tmp_path, settings())
    assert result['sources'] == []
    result = selection(tmp_path, settings([builtin_behaviors.SHELL_BEHAVIOR_URI]))
    assert result['sources'] == [{'url':'https://github.com/microsoft/amplifier-bundle-skills', 'ref':'main'}]


def test_shipped_shell_is_read_from_an_app_folder_whose_address_is_quoted(tmp_path, monkeypatch):
    # The shipped behavior has a file address. A space in the app's folder is "%20" there.
    import shutil
    app = tmp_path/'an app'
    shutil.copytree(builtin_behaviors.resource_root()/'behaviors', app/'behaviors')
    monkeypatch.setattr(builtin_behaviors, 'resource_root', lambda: app)
    result = selection(tmp_path, settings([builtin_behaviors.SHELL_BEHAVIOR_URI]))
    assert result['sources'] == [{'url':'https://github.com/microsoft/amplifier-bundle-skills', 'ref':'main'}]


def test_only_the_address_that_the_app_makes_is_read_as_the_shipped_file(tmp_path):
    skills = [{'url':'https://github.com/microsoft/amplifier-bundle-skills', 'ref':'main'}]
    address = builtin_behaviors.resource_root().as_uri()
    file = '#subdirectory=behaviors/unified-shell.yaml'
    assert address.startswith('file:///')
    assert selection(tmp_path, settings([address + file]))['sources'] == skills
    # The same address with "//localhost" before the folder. A general conversion of a file
    # address takes that part away on every system; the selection must not. Linux and macOS
    # only: on Windows that text is the path of a network folder, and the selection's look for
    # a local bundle (host.bundle_paths.local_bundle_path) asks the system whether it exists.
    if os.name != 'nt':
        other = address.replace('file:///', 'file:////localhost/', 1)
        assert selection(tmp_path, settings([other + file]))['sources'] == []
    # The same address with a server name. On Windows the app's own address has none, and only
    # an address with no server name loses the slash before its drive; a general conversion
    # does not look at the server name. Linux and macOS do not read the server name of a file
    # address.
    if os.name == 'nt':
        other = address.replace('file:///', 'file://another-computer/', 1)
        assert selection(tmp_path, settings([other + file]))['sources'] == []


@pytest.mark.parametrize('reference', ['file:///C:/a:b/behaviors/behavior.yaml', 'file::b/behaviors/behavior.yaml'])
def test_a_file_address_that_is_no_path_of_this_system_selects_nothing(tmp_path, reference):
    # Neither address is a path on Windows. It is not the shipped file, the check goes on, and the
    # reference stays in the selection as it is.
    assert selection(tmp_path, settings([reference]))['sources'] == []
    assert selection(tmp_path, settings([reference]))['witness'] != selection(tmp_path, settings())['witness']


def test_classification_keeps_exact_ref_and_both_source_and_package_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(update_sequence, 'included_sources', lambda: (set(), []))
    chosen = selection(tmp_path, settings([URI]))
    def row(id, ref='main', **kw):
        return {'id':id,'url':URL,'ref':ref,'current':'a'*40,'kind':'bundle / module',
                'eligible':True,'status':'not_checked',**kw}
    rows = [row('source'), row('package',kind='runtime dependency',package='fixture'),
            row('sibling','other'), row('pinned','v1.2.3',eligible=False,status='pinned'),
            row('unselected',url=URL+'-unused')]
    result = update_sequence.classify(tmp_path, rows, selection=chosen)
    assert [r['id'] for r in result if r['updateTier']=='included'] == ['source','package']
    assert result[3]['status']=='pinned' and not result[3]['eligible']
    assert all(r['appSelection']==chosen['witness'] for r in result)
    missing = update_sequence.classify(tmp_path,[row('package',kind='runtime dependency',package='fixture')],selection=chosen)
    assert len(missing)==2 and missing[1]['kind']=='included source'
    assert missing[1]['ref']=='main'


def test_missing_pin_cannot_become_moving_install_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(update_sequence, 'included_sources', lambda: (set(), []))
    chosen = selection(tmp_path, settings(['git+'+URL+'@'+'b'*40]))
    row, = update_sequence.classify(tmp_path, [], selection=chosen)
    assert row['status']=='pinned' and not row['eligible']


def save_settings(home, value):
    home.mkdir(parents=True, exist_ok=True)
    (home/'settings.yaml').write_text(yaml.safe_dump(value))


def test_current_and_copied_selection_witness_and_legacy_unknown(tmp_path, monkeypatch):
    home=tmp_path/'app';home.mkdir();shared=Path(os.environ['AMPLIFIER_HOME'])
    value=settings([URI]);save_settings(shared,value)
    # Staging rewrites generation-owned sources, not external local references
    # to another existing worker project under the app home.
    value['sources']={'modules':{'local-tool':str(home/'runtime/qualified/tool')}}
    save_settings(shared,value)
    manager=SimpleNamespace(home=home)
    rows=[{'kind':'bundle / module','appSelection':update_sequence.app_selection(home)['witness']}]
    update_sequence.verify_app_selection(manager,rows)
    stage=home/'updates/releases'/('a'*32);stage.mkdir(parents=True)
    save_settings(stage/'shared-config',value)
    update_sequence.verify_app_selection(manager,rows,stage=stage)
    copied = copy.deepcopy(value)
    copied['sources']['modules']['local-tool'] = str(stage/'runtime/qualified/tool')
    save_settings(stage/'shared-config',copied)
    with pytest.raises(ValueError,match='Staged'):
        update_sequence.verify_app_selection(manager,rows,stage=stage)
    save_settings(stage/'shared-config',settings(['git+'+URL+'@stable']))
    with pytest.raises(ValueError,match='Staged'):
        update_sequence.verify_app_selection(manager,rows,stage=stage)
    save_settings(shared,settings())
    with pytest.raises(ValueError,match='changed'):
        update_sequence.verify_app_selection(manager,rows)
    with pytest.raises(ValueError,match='not checked'):
        update_sequence.verify_app_selection(manager,[{'kind':'bundle / module'}])


def test_only_existing_source_copy_relocation_is_witness_equivalent(tmp_path, monkeypatch):
    home=tmp_path/'app';home.mkdir();shared=Path(os.environ['AMPLIFIER_HOME'])
    value=settings([URI],sources={'modules':{'local-tool':str(home/'foundation/cache/tool')}})
    save_settings(shared,value)
    rows=[{'kind':'bundle / module','appSelection':update_sequence.app_selection(home)['witness']}]
    stage=home/'updates/releases'/('b'*32);stage.mkdir(parents=True)
    copied=copy.deepcopy(value)
    copied['sources']['modules']['local-tool']=str(stage/'foundation/cache/tool')
    save_settings(stage/'shared-config',copied)
    update_sequence.verify_app_selection(SimpleNamespace(home=home),rows,stage=stage)


def below(root):
    return settings([URI], sources={'modules':{'local-tool':str(root/'foundation/cache/tool')}},
                    overrides={'tool-x':{'source':str(root/'foundation/cache/other')}})


# The second name has a letter that JSON text writes in another form, on every system.
@pytest.mark.parametrize('name', ['app', 'caf\u00e9 app'])
def test_an_installation_and_its_stage_have_one_configuration_key_and_one_witness(tmp_path, name):
    # The settings name sources below the data folder. The stage's copy names them below the stage.
    from amplifier_web.runtime_profiles import configuration_key
    home = tmp_path.resolve()/name
    stage = home/'updates/releases'/('b'*32)
    assert configuration_key(below(home), home, stage) == configuration_key(below(stage), home, stage)
    live = HostConfig(home, home, below(home), home/'foundation', home/'shared', True)
    staged = HostConfig(stage, stage, below(stage), stage/'foundation', stage/'shared-config', True)
    assert (update_sequence.app_selection(home, config=live)['witness']
            == update_sequence.app_selection(stage, config=staged, original_home=home)['witness'])
    # A source outside the named folders keeps its own path.
    other = tmp_path.resolve()/'another folder'
    assert configuration_key(below(home), home, stage) != configuration_key(below(other), home, stage)

    # An enabled app behavior that is a file below the Foundation folder: the
    # selection itself names the path then, not only the settings.
    def behavior(root):
        path = root/'foundation/cache/behavior/behavior.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('bundle: {name: fixture}\n')
        return settings([str(path)])
    live = HostConfig(home, home, behavior(home), home/'foundation', home/'shared', True)
    staged = HostConfig(stage, stage, behavior(stage), stage/'foundation', stage/'shared-config', True)
    assert (update_sequence.app_selection(home, config=live)['witness']
            == update_sequence.app_selection(stage, config=staged, original_home=home)['witness'])
    elsewhere = HostConfig(home, home, behavior(other), home/'foundation', home/'shared', True)
    assert (update_sequence.app_selection(home, config=elsewhere)['witness']
            != update_sequence.app_selection(home, config=live)['witness'])


def named(source):
    return settings([URI], sources={'modules': {'local-tool': str(source)}})


def witness_of(root, source, original=None):
    config = HostConfig(root, root, named(source), root/'foundation',
                        root/('shared' if original is None else 'shared-config'), True)
    return update_sequence.app_selection(root, config=config, original_home=original)['witness']


@pytest.mark.parametrize('name', ['app', 'caf\u00e9 app'])
def test_a_folder_beside_a_named_folder_keeps_its_own_path(tmp_path, name):
    # "<data folder>-extra" and "foundation-extra" are not below the data folder or the
    # Foundation folder. So an installation and its stage must not give one value for them.
    # One exception stays as it was: a folder path that JSON text writes as it is (no
    # backslash, only ASCII). The replacement for such a folder never looked at where the
    # folder's name ends, and installations have saved values that it made (the last case
    # of this group holds some).
    from amplifier_web.runtime_profiles import configuration_key
    home = tmp_path.resolve()/name
    stage = home/'updates/releases'/('b'*32)
    as_before = json.dumps(str(home))[1:-1] == str(home)
    assert (configuration_key(named(Path(str(home) + '-extra')/'tool'), home, stage)
            == configuration_key(named(Path(str(stage) + '-extra')/'tool'), home, stage)) == as_before
    assert (witness_of(home, home/'foundation-extra/tool')
            == witness_of(stage, stage/'foundation-extra/tool', home)) == as_before
    # A text that only has the folder's path in it is no path below the folder.
    assert (witness_of(home, '--from=' + str(home/'foundation/cache/tool'))
            == witness_of(stage, '--from=' + str(stage/'foundation/cache/tool'), home)) == as_before
    # "file://" and the path is a form of a local bundle (host.bundle_paths.local_bundle_path).
    assert (witness_of(home, 'file://' + str(home/'foundation/cache/tool'))
            == witness_of(stage, 'file://' + str(stage/'foundation/cache/tool'), home))

    # The same for an enabled app behavior that is a file beside the Foundation folder: the
    # selection itself names that path, not only the settings. "#" can be in a folder's name.
    def beside(root, folder):
        path = root/folder/'behavior.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('bundle: {name: fixture}\n')
        return settings([str(path)])
    for folder in ('foundation-extra', 'foundation#extra'):
        live = HostConfig(home, home, beside(home, folder), home/'foundation', home/'shared', True)
        staged = HostConfig(stage, stage, beside(stage, folder), stage/'foundation', stage/'shared-config', True)
        assert (update_sequence.app_selection(home, config=live)['witness']
                == update_sequence.app_selection(stage, config=staged, original_home=home)['witness']) == as_before, folder


@pytest.mark.parametrize('name', ['app', 'caf\u00e9 app'])
def test_only_a_value_that_is_a_named_folder_or_a_path_below_it_loses_the_folder(tmp_path, name):
    # The rule for a folder path that JSON text does not write as it is: every Windows path,
    # and the second name on every system. A folder path that JSON writes as it is keeps the
    # replacement of before, wherever its text stands (`as_before`).
    from amplifier_web.runtime_profiles import configuration_key
    home = tmp_path.resolve()/name
    stage = home/'updates/releases'/('b'*32)
    as_before = json.dumps(str(home))[1:-1] == str(home)

    def one_key(value):
        # Do an installation and its stage have one key for the same value, each with its own folder?
        return configuration_key(value(str(home)), home, stage) == configuration_key(value(str(stage)), home, stage)

    # The folder itself, and a path below it with each path separator of this system.
    assert one_key(named)
    assert one_key(lambda folder: named(folder + '/tool'))
    assert one_key(lambda folder: named(folder + os.sep + 'tool'))
    assert one_key(lambda folder: named('file://' + folder + os.sep + 'tool'))
    # The name of a folder beside it can go on with "#", with a letter that JSON writes in
    # another form, or with a quote.
    for more in ('#extra', '#extra' + os.sep + 'tool', '\u00e9', '\u00e9' + os.sep + 'tool', '"x'):
        assert one_key(lambda folder: named(folder + more)) == as_before, more
    # A backslash is a part of a name on Linux and macOS, and a path separator on Windows.
    assert one_key(lambda folder: named(folder + '\\tool')) == (as_before or os.sep == '\\')
    # ".." after the folder can lead out of it again, so a value with it keeps its own path.
    # That holds for a part, and also for a name or a fragment with two dots, which do not
    # lead out: the rule does not read the path. In an address, "%" can hide the dots.
    for more in ('..', '..' + os.sep + 'outside', 'cache' + os.sep + '..' + os.sep + '..' + os.sep + 'outside',
                 '..tool', 'cache' + os.sep + 'tool..v2', 'bundle#subdirectory=a/../b'):
        assert one_key(lambda folder: named(folder + os.sep + more)) == as_before, more
        assert one_key(lambda folder: named(folder + '/' + more)) == as_before, more
        assert one_key(lambda folder: named('file://' + folder + os.sep + more)) == as_before, more
    for more in ('%2e%2e/outside', 'a%20b'):
        assert one_key(lambda folder: named('file://' + folder + '/' + more)) == as_before, more
    # One dot is the folder itself or a part of a name. "%" in a path that is no address is a letter.
    for more in ('.', '.' + os.sep + 'tool', '.hidden', 'tool.v2', '100%', '%2e%2e' + os.sep + 'tool'):
        assert one_key(lambda folder: named(folder + os.sep + more)), more
    # The name of an entry is not a source.
    assert one_key(lambda folder: settings([URI], sources={'modules': {folder: URI}})) == as_before
    assert one_key(lambda folder: settings([URI], sources={'modules': {folder + os.sep + 'tool': URI}})) == as_before
    # The same in the witness, where the named folders are the Foundation folders.
    assert witness_of(home, home/'foundation') == witness_of(stage, stage/'foundation', home)
    assert (witness_of(home, str(home/'foundation') + '#extra')
            == witness_of(stage, str(stage/'foundation') + '#extra', home)) == as_before
    assert (witness_of(home, str(home/'foundation'/'..'/'outside'))
            == witness_of(stage, str(stage/'foundation'/'..'/'outside'), home)) == as_before


@pytest.mark.parametrize('name', ['app', 'caf\u00e9 app'])
def test_settings_that_name_no_folder_keep_their_configuration_key_and_witness(tmp_path, name):
    # Most installations: the settings name Git addresses only. The two values
    # hold no path then, so they are the same on every system and for every
    # data folder, also one with a name that JSON text writes in another form.
    # They were read from the code before it handled a Windows path; a change
    # of that code must not move them (see the next case).
    from amplifier_web.runtime_profiles import configuration_key
    home = tmp_path.resolve()/name
    stage = home/'updates/releases'/('b'*32)
    value = settings([URI], sources={'modules': {'tool-x': 'git+' + URL + '@main#subdirectory=modules/tool-x'}})
    assert configuration_key(value, home, stage) == 'bd8517dcc00253948dc827b31b5f172f83c281d4372df6732d287cc21a2f44c4'
    config = HostConfig(home, home, value, home/'foundation', home/'shared', True)
    assert update_sequence.app_selection(home, config=config)['witness'] == 'd053b29f08226ee87e125cb19a251ee6e1bbb8b9164f6062fa6a41b0af86815c'


@pytest.mark.skipif(os.name == 'nt', reason='the values of a Linux installation')
def test_a_linux_installation_keeps_its_selection_witness_and_configuration_key():
    # An installation saves these values in its checked rows and in its
    # qualification records and compares them later. They were read from the
    # code before it handled a Windows path. A change of that code must not
    # move them: every saved row and record of a Linux installation would
    # then count as changed.
    from amplifier_web.runtime_profiles import configuration_key
    home=Path('/srv/unified');stage=home/'updates/releases'/('b'*32)
    live=HostConfig(home,home,below(home),home/'foundation',home/'shared',True)
    staged=HostConfig(stage,stage,below(stage),stage/'foundation',stage/'shared-config',True)
    witness='f3e506d1da2d497b7f9c74017650d5456fc9e454b7571f24cb0b4363408c19c3'
    assert update_sequence.app_selection(home,config=live)['witness']==witness
    assert update_sequence.app_selection(stage,config=staged,original_home=home)['witness']==witness
    key='d3ecd70527a165b03ef15d1b8428bf9797e525644617a723ede05866cdcbe9e1'
    assert configuration_key(below(home),home,stage)==key
    assert configuration_key(below(stage),home,stage)==key
    # The same for a folder beside a named folder, for a text that only has the path in it,
    # and for "file://" and the path. These paths are written as JSON writes them, so the
    # replacement for them is the one of before, also where it takes more than the folder.
    beside='e45ebae9ff7e3aeb4d926ae7aa1583e0a1df64228804b57477f611c8ced62cd3'
    assert configuration_key(named('/srv/unified-extra/tool'),home,stage)==beside
    assert configuration_key(named(str(stage)+'-extra/tool'),home,stage)==beside
    inside='0381d296f9338d81fe5a1179966f2fcf70ce3ea7fc140361015ca36898f84cea'
    assert configuration_key(named('--from=/srv/unified/foundation/cache/tool'),home,stage)==inside
    assert configuration_key(named('--from='+str(stage/'foundation/cache/tool')),home,stage)==inside
    beside='0eb48737eca44dc5bd4a2d980ef14745e5fec33ba15ccfc464bd73e95b7d1d97'
    assert witness_of(home,'/srv/unified/foundation-extra/tool')==beside
    assert witness_of(stage,str(stage/'foundation-extra/tool'),home)==beside
    address='07793ce887e6bbe5d82004e6fc43db875f06bb5e2070a86cb8be77647b620bcd'
    assert witness_of(home,'file:///srv/unified/foundation/cache/tool')==address
    assert witness_of(stage,'file://'+str(stage/'foundation/cache/tool'),home)==address
    # One call with a folder of each kind: each folder keeps its own rule.
    other=Path('/srv/caf\u00e9')
    def two(first,second): return settings([URI],sources={'modules':{'one':first,'two':second}})
    assert (configuration_key(two('/srv/unified/x','/srv/caf\u00e9/y'),home,other)
            ==configuration_key(two('/srv/caf\u00e9/x','/srv/unified/y'),home,other))
    assert (configuration_key(two('/srv/unified-extra/x',URI),home,other)
            !=configuration_key(two('/srv/caf\u00e9-extra/x',URI),home,other))
    assert configuration_key(two('/srv/unified-extra/x',URI),home,other)==configuration_key(two('/srv/unified-extra/x',URI),home)


async def test_index_invalidates_on_effective_selection_not_history_size(tmp_path,monkeypatch):
    from amplifier_web import update_inventory_index as index
    home=tmp_path/'app';home.mkdir();directory=home/'updates';directory.mkdir()
    # The product names its data folder as the app home before it builds the service (cli.py).
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(home))
    shared=Path(os.environ['AMPLIFIER_HOME']);save_settings(shared,settings([URI]))
    manager=SimpleNamespace(home=home,directory=directory,service=SimpleNamespace(state={'settings':{},'sessions':[]}))
    calls=[]
    async def scan():calls.append(1);return [{'id':'observed'}]
    await index.inventory(manager,scan);await index.inventory(manager,scan)
    assert len(calls)==1
    # A same-size atomic replacement is not merely a length/mtime witness.
    value=settings(['git+'+URL+'@test#subdirectory=behavior.yaml']);save_settings(shared,value)
    await index.inventory(manager,scan);assert len(calls)==2
    manager.service.state['sessions']=[{'privateHistory':'not read'}]*10000
    await index.inventory(manager,scan);assert len(calls)==2


async def test_real_check_includes_selected_source_before_other_tier(tmp_path,monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web import app_updates,runtime_environment
    from amplifier_web.host.config import amplifier_home
    provider_config = {'providers': [{'module': 'provider-anthropic',
                                      'config': {'default_model': 'fixture-model'}}]}
    shared=amplifier_home();save_settings(shared,settings([URI], config=provider_config))
    home=tmp_path/'app';cache=home/'foundation/cache/behavior';cache.mkdir(parents=True)
    def git(*args):return subprocess.check_output(['git',*args],cwd=cache,text=True).strip()
    git('init','-q','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (cache/'behavior.yaml').write_text('bundle: {name: fixture}\n');git('add','.');git('commit','-qm','fixture')
    current=git('rev-parse','HEAD')
    (cache/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':URL,'ref':'main','commit':current}))
    class Runtime:
        async def close(self):pass
    # The product names its data folder as the app home before it builds the service (cli.py).
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(home))
    app=AppService(home,Runtime(),workspace=tmp_path);manager=updates.UpdateManager(app);app.update_manager=manager
    async def application():return {'id':'application','status':'current'}
    async def runtime_rows(_):return []
    monkeypatch.setattr(app_updates,'check',application)
    monkeypatch.setattr(runtime_environment,'update_inventory',runtime_rows)
    monkeypatch.setattr(update_sequence,'included_sources',lambda:(set(),[]))
    process=updates.process;calls=[]
    async def remote(*args,**kwargs):
        if 'ls-remote' in args:
            calls.append(args);assert URL in args
            return 'b'*40+'\trefs/heads/main'
        return await process(*args,**kwargs)
    monkeypatch.setattr(updates,'process',remote)
    try:
        await manager.check(fresh=True)
        assert app.state['updates']['phase']=='available'
        assert app.state['updates']['sequence']['stage']=='included'
        row, = manager.inventory
        assert row['url']==URL and row['latest']=='b'*40 and row['appSelection']
        assert calls and not app.state['updates'].get('pendingRelease')
        save_settings(shared,settings(config=provider_config))
        with pytest.raises(ValueError,match='selection changed'):
            await manager.install()
        assert not list((home/'updates/releases').glob('*'))
    finally:
        await app.close()


async def test_selection_changed_during_remote_check_never_becomes_checked_batch(tmp_path,monkeypatch):
    from amplifier_web.service import AppService
    from amplifier_web import app_updates,runtime_environment
    shared=Path(os.environ['AMPLIFIER_HOME']);save_settings(shared,settings([URI]))
    home=tmp_path/'app'
    class Runtime:
        async def close(self):pass
    app=AppService(home,Runtime(),workspace=tmp_path)
    manager=updates.UpdateManager(app);app.update_manager=manager
    async def application():return {'id':'application','status':'current'}
    async def runtime_rows(_):return []
    monkeypatch.setattr(app_updates,'check',application)
    monkeypatch.setattr(runtime_environment,'update_inventory',runtime_rows)
    monkeypatch.setattr(update_sequence,'included_sources',lambda:(set(),[]))
    process=updates.process
    async def remote(*args,**kwargs):
        if 'ls-remote' in args:
            save_settings(shared,settings(['git+'+URL+'@stable']))
            return 'b'*40+'\trefs/heads/main'
        return await process(*args,**kwargs)
    monkeypatch.setattr(updates,'process',remote)
    try:
        await manager.check(fresh=True)
        assert app.state['updates']['phase']=='error'
        assert manager.inventory==[]
        assert not app.state['updates'].get('pendingRelease')
        assert not list((home/'updates/releases').glob('*'))
    finally:
        await app.close()


def test_discovery_is_bounded_by_declarations_not_retained_caches(tmp_path,monkeypatch):
    import time
    references=['git+https://example.invalid/behavior'+str(n)+'@main' for n in range(100)]
    cfg=config(tmp_path,settings(references))
    reads=[]
    original=Path.read_text
    def read(path,*args,**kwargs):
        reads.append(str(path))
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    started=time.perf_counter()
    result=update_sequence.app_selection(tmp_path,config=cfg)
    elapsed=time.perf_counter()-started
    assert len(result['sources'])==100
    assert reads==[], 'Remote app choices must not read cache bodies or history'
    assert elapsed<1, f'100 declarations exceeded the one-second fixture budget: {elapsed}'


@pytest.mark.parametrize('ref', ['main', 'a'*40])
def test_checked_reachable_dependency_uses_canonical_provenance(ref):
    from amplifier_web.selected_runtime_dependencies import selected
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    from amplifier_web.runtime_environment import ProtectedRuntimeSource
    class Distribution:
        metadata={'Name':'dependency'}
        requires=[]
        version='1.0'
        def read_text(self,name):
            return json.dumps({'url':URL,'vcs_info':{'vcs':'git','commit_id':'b'*40}})
    manifest=('[project]\nname="root"\ndependencies=["dependency @ git+'+URL+'@'+ref+'"]').encode()
    packages=selected({'root':manifest},distributions=[Distribution()])
    proof={'batchPaths':{'root':'/selected/root'},'packages':packages}
    checked={'dependency':{'url':URL,'ref':'main','revision':'b'*40}}
    if ref!='main':
        assert packages['dependency']['status']=='selected-dependency-unresolved'
        with pytest.raises(ProtectedRuntimeSource):
            verify_checked_selection(checked,proof,exact=True)
    else:
        verify_checked_selection(checked,proof,exact=True)
        checked['dependency']['revision']='c'*40
        verify_checked_selection(checked,proof)
        with pytest.raises(ProtectedRuntimeSource) as mismatch:
            verify_checked_selection(checked,proof,exact=True)
        assert mismatch.value.diagnostic_facts['package'] == 'dependency'
        assert mismatch.value.diagnostic_facts['reason'] == 'runtime-resolution-mismatch'
        checked['dependency']['revision']='b'*40
        checked['dependency']['subdirectory']='other'
        with pytest.raises(ProtectedRuntimeSource):
            verify_checked_selection(checked,proof,exact=True)


def test_unrelated_installed_extra_never_becomes_selected_guard_input():
    from amplifier_web.selected_runtime_dependencies import selected
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    class Unrelated:
        metadata={'Name':'unused'}
        requires=['dependency @ git+'+URL+'@'+'a'*40]
        version='1.0'
        def read_text(self,name):return '{}'
    packages=selected({'root':b'[project]\nname="root"\ndependencies=[]'},
                      distributions=[Unrelated()])
    assert packages=={}
    verify_checked_selection({'unused':{'url':URL,'ref':'main','revision':'b'*40}},
                             {'batchPaths':{'root':'/selected/root'},'packages':packages},exact=True)

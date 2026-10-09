from amplifier_web.runtime_profiles import module_source_references


def test_only_declared_sources_including_nested_agent_modules_are_qualified():
    plan = {
        'session': {'orchestrator': {'module': 'loop-live', 'source': './loop', 'config': {'source': '/data'}},
                    'context': {'module': 'context-simple', 'source': './context'}},
        'providers': [{'module': 'provider-local', 'source': './provider', 'config': {'credential_file': '/data'}}],
        'tools': [{'module': 'tool-local', 'source': './tool', 'config': {'module': 'data', 'source': '/data'}}],
        'hooks': [{'module': 'hook-local', 'source': './hook', 'config': {'include_paths': ['/data']}}],
        'context': {'include': ['/data']}, 'instruction': '/data',
        'agents': {'child': {'tools': [{'module': 'tool-child', 'source': './child'}],
                             'agents': {'nested': {'hooks': [{'module': 'hook-deep', 'source': './deep'}]}}}}
    }
    assert list(module_source_references(plan)) == ['./loop', './context', './provider', './tool', './hook', './child', './deep']


def test_integration_keeps_spawn_and_bundle_sources_without_scanning_config():
    plan = {
        "tools": None,
        "spawn": {"tools": [{"module": "tool-lazy", "source": "./lazy",
                            "config": {"source": "/data", "includes": ["/data"]}}]},
        "includes": [{"bundle": "./bundle#subdirectory=bundle.yaml"}],
        "agents": {"child": {
            "spawn": {"tools": [{"module": "tool-child", "source": "./child"}]},
            "includes": ["./child-bundle"],
        }},
        "context": {"include": ["/data"]},
    }
    assert list(module_source_references(plan)) == [
        "./lazy", "./bundle#subdirectory=bundle.yaml", "./child", "./child-bundle",
    ]

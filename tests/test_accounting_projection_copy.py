import copy

from amplifier_web.session_projection import accounting_projection


class MutableString(str):
    pass


class CustomUsage(dict):
    def __deepcopy__(self, memo):
        return {'inputTokens': self['inputTokens'] + 1, 'outputTokens': 2}


class CopiedName(str):
    copies = 0

    def __deepcopy__(self, memo):
        result = type(self)(self)
        result.copies = self.copies + 1
        return result


class UsageSubclass(dict):
    pass


def test_accounting_projection_detaches_retained_nested_values_and_scalar_subclasses():
    model = MutableString('fixture')
    model.notes = ['original']
    receipt = {'id': 'call', 'kind': 'llm', 'sessionId': 'root', 'producerId': 'producer',
               'model': model, 'lifecycle': {'nested': ['original']}, 'revision': 1,
               'usage': {'inputTokens': 3, 'costUsd': .25}}
    result = accounting_projection({'retiredUsageNodes': [receipt]})[0]
    receipt['lifecycle']['nested'].append('later')
    model.notes.append('later')
    receipt['usage']['inputTokens'] = 8
    assert result['lifecycle'] == {'nested': ['original']}
    assert result['model'].notes == ['original']
    assert result['usage'] == {'inputTokens': 3, 'costUsd': .25}
    result['usage']['costUsd'] = 1
    assert receipt['usage']['costUsd'] == .25
    name = MutableString('inputTokens')
    name.notes = ['original']
    receipt['usage'] = {name: 8}
    copied_name = next(iter(accounting_projection({'retiredUsageNodes': [receipt]})[0]['usage']))
    name.notes.append('later')
    assert copied_name.notes == ['original']


def test_usage_filter_preserves_exact_types_custom_mapping_and_field_order():
    values = {'inputTokens': True, 'outputTokens': MutableString('2'),
              'totalTokens': 3, 'costUsd': .25, 'costType': 'reported',
              'cacheReadTokens': {'nested': [1]}, 'unknown': {'private': ['body']}}
    receipt = {'id': 'call', 'usage': values, 'kind': 'llm', 'sessionId': 'root',
               'revision': 1, 'requestInfo': {'private': 'excluded'}}
    result = accounting_projection({'retiredUsageNodes': [receipt]})[0]
    assert result['usage'] == {'totalTokens': 3, 'costUsd': .25, 'costType': 'reported'}
    assert list(result) == ['id', 'usage', 'kind', 'sessionId', 'revision']
    receipt['usage'] = CustomUsage(inputTokens=4)
    assert accounting_projection({'retiredUsageNodes': [receipt]})[0]['usage'] == {
        'inputTokens': 5, 'outputTokens': 2}
    receipt['usage'] = ['invalid']
    assert 'usage' not in accounting_projection({'retiredUsageNodes': [receipt]})[0]
    # Generic mapping copies already detach custom keys; do not copy them twice.
    receipt['usage'] = UsageSubclass({CopiedName('inputTokens'): 4})
    key = next(iter(accounting_projection({'retiredUsageNodes': [receipt]})[0]['usage']))
    assert key.copies == 1


def test_fresh_projection_observes_mutated_receipts_without_borrowing_child_identity():
    root = {'id': 'same', 'kind': 'llm', 'sessionId': 'root', 'rootSessionId': 'root',
            'producerId': 'producer', 'revision': 1, 'phase': 'running',
            'usage': {'inputTokens': 2}}
    child = {**copy.deepcopy(root), 'sessionId': 'child'}
    tree = {'retiredUsageNodes': [root, child]}
    initial = accounting_projection(tree)
    root.update(revision=2, phase='completed', endedAt=10)
    root['usage']['outputTokens'] = 4
    latest = accounting_projection(tree)
    assert initial[0]['revision'] == 1 and initial[0]['usage'] == {'inputTokens': 2}
    assert latest[0]['revision'] == 2 and latest[0]['usage']['outputTokens'] == 4
    assert latest[1]['sessionId'] == 'child' and latest[1]['revision'] == 1

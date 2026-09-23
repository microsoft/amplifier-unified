"""Attachment prompt decoration must not become a second user turn."""
import copy

import pytest

from amplifier_web.automatic_history import display_message, merge_web_history


def fixture(identity='input-one', caption='Inspect this image.'):
    visible = {'id': 'web-' + identity, 'role': 'user', 'text': caption,
               'inputId': identity, 'createdAt': 42, 'via': 'chat',
               'attachments': [{'id': 'file-' + identity, 'name': 'image.png'}],
               'delivery': {'status': 'delivered'}}
    native = {'role': 'user', 'content': [
        {'type': 'text', 'text': caption},
        {'type': 'text', 'text': 'User attachment: image.png\nLocal file: /fixture/content'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'fixture'}},
    ], 'metadata': {'amplifier_input': {'version': 1, 'kind': 'user', 'id': identity, 'source': 'chat'}}}
    return visible, native


@pytest.mark.parametrize('caption', ['Inspect this image.', ''])
def test_attachment_prompt_has_one_original_bubble_after_repeated_refresh(caption):
    visible, native = fixture(caption=caption)
    original = copy.deepcopy(native)
    session = {'id': 'session', 'messages': [visible]}
    incoming = [display_message(native, 0, session)]
    for _ in range(3):
        merge_web_history(session, incoming)
        assert len(session['messages']) == 1
        assert {k: session['messages'][0][k] for k in visible} == visible
        assert session['messages'][0]['nativeIndex'] == 0
    assert native == original


def test_repairs_exact_cached_native_copy_with_saved_boundary():
    visible, native = fixture()
    session = {'id': 'session', 'messages': [visible]}
    incoming = [display_message(native, 0, session)]
    legacy = {k: v for k, v in incoming[0].items() if k != 'nativeInputId'}
    session.update(messages=[visible, legacy], nativeBoundary=0, nativeBoundaryId=legacy['id'])
    merge_web_history(session, incoming)
    assert [row['id'] for row in session['messages']] == [visible['id']]
    assert session['nativeBoundaryId'] == incoming[0]['id']


def test_repeated_caption_with_different_inputs_keeps_each_attachment():
    first, one = fixture('first')
    second, two = fixture('second')
    session = {'id': 'session', 'messages': [first, second]}
    incoming = [display_message(row, n, session) for n, row in enumerate([one, two])]
    merge_web_history(session, incoming)
    assert [row['id'] for row in session['messages']] == [first['id'], second['id']]
    assert [row['nativeIndex'] for row in session['messages']] == [0, 1]
    assert [row['attachments'] for row in session['messages']] == [first['attachments'], second['attachments']]


def test_first_alignment_keeps_native_only_reply_between_attachment_inputs():
    first, one = fixture('first')
    second, two = fixture('second')
    session = {'id': 'session', 'messages': [first, second]}
    rows = [one, {'role': 'assistant', 'content': 'Saved reply'}, two]
    incoming = [display_message(row, n, session) for n, row in enumerate(rows)]
    merge_web_history(session, incoming)
    assert [row['role'] for row in session['messages']] == ['user', 'assistant', 'user']
    assert session['messages'][1]['text'] == 'Saved reply'


@pytest.mark.parametrize('provenance', [None, {'version': 99, 'kind': 'user', 'id': 'input-one'},
                                     {'version': 1, 'kind': 'service', 'id': 'input-one', 'source': 'service'},
                                     {'version': 1, 'kind': 'user', 'id': 'different-input'}])
def test_attachment_like_text_or_wrong_identity_cannot_hide_a_native_message(provenance):
    visible, native = fixture()
    native['metadata'] = {'amplifier_input': provenance} if provenance else {}
    session = {'id': 'session', 'messages': [visible]}
    merge_web_history(session, [display_message(native, 0, session)])
    assert len(session['messages']) == 2


def test_ambiguous_input_identity_is_not_coalesced():
    visible, native = fixture()
    session = {'id': 'session', 'messages': [visible]}
    incoming = [display_message(native, n, session) for n in range(2)]
    merge_web_history(session, incoming)
    assert len(session['messages']) == 3


def test_rewrite_does_not_silently_rebind_an_attachment_anchor():
    visible, native = fixture()
    session = {'id': 'session', 'messages': [visible]}
    merge_web_history(session, [display_message(native, 0, session)])
    before = copy.deepcopy(session)
    native['content'][1]['text'] = 'Changed attachment instructions'
    with pytest.raises(ValueError, match='rewritten'):
        merge_web_history(session, [display_message(native, 0, session)])
    assert session == before


def test_unverified_cached_copy_is_not_removed():
    visible, native = fixture()
    session = {'id': 'session', 'messages': [visible]}
    incoming = [display_message(native, 0, session)]
    altered = {**incoming[0], 'id': 'unverified-copy', 'nativeIndex': 100}
    session['messages'].append(altered)
    with pytest.raises(ValueError, match='rewritten'):
        merge_web_history(session, incoming)
    assert session['messages'] == [visible, altered]

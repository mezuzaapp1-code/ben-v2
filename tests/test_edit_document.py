"""Pure B01 contract tests: no database, provider or application startup."""
import copy
import json
from pathlib import Path

import pytest

from services.media.edit_document import validate_edit_document, parse_edit_document

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/editor_document_v1.json').read_text(encoding='utf-8'))


def draft():
    return copy.deepcopy(FIXTURE['document'])


def test_roundtrip_complete_draft_immutable_and_detached():
    value = draft()
    doc = validate_edit_document(value)
    assert parse_edit_document(doc.model_dump_json()) == doc
    assert doc.body.cues[0].text == 'יוסטון, טקסס\n03.10.2026'
    assert doc.body.cues[1].start == 16.05
    assert doc.body.style.backgroundMode == 'none'
    assert doc.body.textLayers[0].style.shadow == 'depth'
    assert doc.body.textLayers[1].text == ''
    value['body']['cues'][0]['text'] = 'changed'
    assert doc.body.cues[0].text != 'changed'
    with pytest.raises(ValueError):
        doc.body.style.x = 15


def test_optional_old_fields_receive_explicit_defaults():
    value = draft()
    del value['body']['textLayers'], value['body']['style']['alignment'], value['source']['timebase']
    doc = validate_edit_document(value)
    assert doc.body.textLayers == ()
    assert doc.body.style.alignment == 'auto'
    assert doc.source.timebase == 'seconds'


@pytest.mark.parametrize('patch', FIXTURE['rejectedPatches'], ids=lambda item: item['name'])
def test_reject_shared_invalid_cases(patch):
    value = draft()
    node = value
    for part in patch['path'][:-1]:
        node = node[part]
    node[patch['path'][-1]] = patch['value']
    with pytest.raises(ValueError):
        validate_edit_document(value)


@pytest.mark.parametrize('key,count', [('cues', 301), ('textLayers', 21)])
def test_track_limits(key, count):
    value = draft()
    value['body'][key] = [dict(copy.deepcopy(value['body'][key][0]), id=f'n-{i}') for i in range(count)]
    with pytest.raises(ValueError):
        validate_edit_document(value)


def test_unicode_codepoint_and_byte_budgets():
    value = draft()
    value['body']['cues'][0]['text'] = '🚀' * 1000
    assert validate_edit_document(value).body.cues[0].text == '🚀' * 1000
    value['body']['cues'][0]['text'] += '🚀'
    with pytest.raises(ValueError):
        validate_edit_document(value)
    value = draft()
    value['body']['cues'] = [dict(id=f'x-{i}', text='🚀' * 1000, start=0, end=1) for i in range(300)]
    with pytest.raises(ValueError, match='too large'):
        validate_edit_document(value)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), None])
def test_invalid_numbers(value):
    doc = draft()
    doc['source']['duration_seconds'] = value
    with pytest.raises(ValueError):
        validate_edit_document(doc)


def test_json_parser_and_schema_boundary():
    with pytest.raises(ValueError, match='Duplicate JSON field'):
        parse_edit_document('{"kind":"video","kind":"image"}')
    with pytest.raises(ValueError):
        parse_edit_document('{')
    with pytest.raises(ValueError, match='too large'):
        parse_edit_document(' ' * 1000001)
    value = draft()
    value['body']['cues'][0]['text'] = '\ud800'
    with pytest.raises(ValueError):
        validate_edit_document(value)
    value = draft()
    value['body']['cues'] = tuple(value['body']['cues'])
    with pytest.raises(ValueError, match='JSON'):
        validate_edit_document(value)


def test_document_never_authorizes_source_or_redefines_product_duration_limits():
    # Parsing knows syntax only; caller must resolve this ID, ownership and actual duration.
    value = draft()
    value['source']['duration_seconds'] = 60
    assert validate_edit_document(value).source.duration_seconds == 60
    assert 'owner_id' not in validate_edit_document(value).model_dump()

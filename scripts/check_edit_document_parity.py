"""Compare frontend-normalized fixture against the independent Python validator.

Run from repository root after test-edit-document.mjs with EDIT_DOCUMENT_RESULT.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.media.edit_document import validate_edit_document  # noqa: E402


def main():
    fixture = json.loads(Path('tests/fixtures/editor_document_v1.json').read_text(encoding='utf-8'))
    frontend = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    backend = validate_edit_document(fixture['document']).model_dump(mode='json')
    assert frontend == backend, 'Frontend/backend normalized documents differ'
    assert validate_edit_document(frontend).model_dump(mode='json') == backend
    print('PASS: frontend output accepted unchanged by Python, including every style and timestamp')


if __name__ == '__main__':
    main()

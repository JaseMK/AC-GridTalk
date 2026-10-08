"""Validate the public schema and packet examples; development dependency only."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/schema-deps'))
from jsonschema import Draft202012Validator


def packet_validator():
    schema = json.loads((ROOT / 'protocol/gridtalk-v2.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def main():
    validator = packet_validator()
    for path in sorted((ROOT / 'protocol/examples').glob('*.json')):
        validator.validate(json.loads(path.read_text(encoding='utf-8')))
    state = json.loads((ROOT / 'protocol/examples/state.json').read_text(encoding='utf-8'))
    invalid = []
    for key, value in [('snapshot', True), ('pages', 0), ('pages', 33), ('page', -1), ('connected', 'true'),
                       ('source', ''), ('v', 3), ('clients', state['clients'] * 5)]:
        packet = copy.deepcopy(state)
        packet[key] = value
        invalid.append(packet)
    packet = copy.deepcopy(state)
    packet['clients'][0]['client_id'] = 9007199254740992
    invalid.append(packet)
    for value in ('W' * 513, '\x00', '\ud800'):
        packet = copy.deepcopy(state)
        packet['clients'][0]['name'] = value
        invalid.append(packet)
    packet = copy.deepcopy(state)
    packet['connected'] = False  # disconnected packets cannot carry members
    invalid.append(packet)
    talk = json.loads((ROOT / 'protocol/examples/talk-start.json').read_text(encoding='utf-8'))
    talk['talking'] = None
    invalid.append(talk)
    assert all(not validator.is_valid(packet) for packet in invalid)
    print('JSON Schema valid; all example packets pass; malformed packet cases rejected.')


if __name__ == '__main__':
    main()

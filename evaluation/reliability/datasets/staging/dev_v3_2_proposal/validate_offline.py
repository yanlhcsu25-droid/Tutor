"""Read-only validator for Teacher Agent Dev proposal. No model / DB imports or API calls."""
from pathlib import Path
from collections import Counter
import json
import yaml

BASE = Path(__file__).resolve().parent

def load(name):
    payload = yaml.safe_load((BASE/name).read_text(encoding='utf-8'))
    assert isinstance(payload,dict)
    cases=payload.get('cases')
    assert isinstance(cases,list)
    for x in cases:
        assert isinstance(x.get('id'),str) and x.get('turns') and isinstance(x.get('expected'),dict)
        assert x.get('review',{}).get('status') in {'pending', 'approved'} or name == 'excluded_duplicates_78.yaml'
        assert all(g.get('type') in {'state','acceptance','workflow_safety','constraint_preservation','score','teaching_quality','paper_analysis'} for g in x.get('graders',[]))
    return cases

def key(x):
    return json.dumps({k:x.get(k) for k in ('turns','setup','expected','graders')},ensure_ascii=False,sort_keys=True)

original=load('dev_122_revised_candidate.yaml')
new=load('new_capability_20_candidate.yaml')
archive=load('excluded_duplicates_78.yaml')
frozen_input=load('dev_v3_2_reviewed_142.yaml')
assert (len(original),len(new),len(archive),len(frozen_input))==(122,20,78,142)
ids=[x['id'] for x in original+new+archive]
assert len(set(ids)) == len(ids), 'archived duplicates still have distinct IDs'
assert len({x['id'] for x in original+new})==142
assert [x['id'] for x in frozen_input] == [x['id'] for x in original+new]
assert all(key(x) == key(y) for x, y in zip(frozen_input, original+new))
assert all(x['id'].startswith('DEVX-') for x in new)
assert 'DEVX-015' not in {x['id'] for x in new}
assert 'DEVX-021' in {x['id'] for x in new}
assert sum(x['review']['status'] == 'approved' for x in original + new) == 129
assert len({key(x) for x in original})==122
for x in original:
    if x['category']=='generation' and x['subtype']=='preview_generation':
        assert x['expected']['pending_generation']['scope_names'] == ['第一章 函数与极限']
for x in new:
    assert not set((g['type'] for g in x.get('graders',[]))) - {'state','acceptance','workflow_safety','constraint_preservation','score','teaching_quality','paper_analysis'}
print('PASS: 122 unique original definitions, 78 archived duplicates, 20 new candidates')
print('NEW_READINESS:',dict(Counter(x['review'].get('readiness') for x in new)))
print('No API calls, no DB access, no historical data mutation.')

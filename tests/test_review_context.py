"""Only bounded declared task/policy data may enter independent review."""
from dataclasses import replace

import pytest

from nightshift.workers.review_context import ApprovedTask, ReviewPolicy, payload, validate

TASK = ApprovedTask('issue-1', 'Fix parsing', 'Preserve valid input.', ('Reject malformed input.',))
POLICY = ReviewPolicy('Check correctness and scope.', ('Inspect failure handling.',))


@pytest.mark.parametrize('field,value', [
    ('task_id', 1), ('task_id', ''), ('title', 'x' * 513), ('body', b'bytes'),
    ('body', 'x' * 32769), ('body', '\ud800'), ('body', 'nul\0'),
    ('body', 'é' * 16385), ('acceptance_criteria', ['mutable']),
    ('acceptance_criteria', (object(),)), ('acceptance_criteria', ('x',) * 33),
    ('acceptance_criteria', ('x' * 2049,)),
    ('acceptance_criteria', ('x' * 2048,) * 32),
])
def test_invalid_task_data_rejected(field, value):
    with pytest.raises(ValueError):
        validate(replace(TASK, **{field: value}), POLICY)


@pytest.mark.parametrize('field,value', [
    ('instructions', None), ('instructions', ' '), ('instructions', 'x' * 8193),
    ('required_checks', {'execute': 'code'}), ('required_checks', (True,)),
    ('required_checks', ('x' * 2048,) * 9),
])
def test_invalid_policy_data_rejected(field, value):
    with pytest.raises(ValueError):
        validate(TASK, replace(POLICY, **{field: value}))


def test_duck_types_and_subclasses_are_not_pure_contract():
    class ExtendedTask(ApprovedTask):
        pass
    with pytest.raises(ValueError):
        validate(ExtendedTask('a', 'b', 'c'), POLICY)
    with pytest.raises(ValueError):
        validate({'task_id': 'a', 'title': 'b', 'body': 'c'}, POLICY)


def test_only_declared_fields_are_serialized():
    task = replace(TASK)
    object.__setattr__(task, 'implementation_transcript', 'PRIVATE_SENTINEL')
    data = payload(task, POLICY)
    assert set(data) == {'approved_task', 'review_policy'}
    assert set(data['approved_task']) == {'task_id', 'title', 'body', 'acceptance_criteria'}
    assert 'PRIVATE_SENTINEL' not in str(data)

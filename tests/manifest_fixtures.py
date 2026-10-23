"""Valid repository templates for tests with injected CLI boundaries."""

import json


def manifest_text(source):
    return f'''[run]
repo = {json.dumps(str(source))}
[roles]
implement = "test"
review = "test"
[profiles.test]
adapter = "codex"
model = "test"
[paths]
allowed = ["src/**"]
[[verify]]
id = "tests"
run = ["true"]
'''

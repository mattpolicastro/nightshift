"""Generated ChatGPT-managed policy with synthetic-only admission tests.

Production construction is blocked until keyring account binding is qualified.
A fresh HOME is not isolation from macOS's shared keyring, and this module never
copies profiles, reads credentials directly, starts login, or purchases credits.
"""
import json
import os
import sys
from pathlib import Path

from .chatgpt_admission import ChatGPTAdmission
from .container_session import SessionError
from .provider_home import FixtureProvider, FEATURES, _same


class ChatGPTProvider(FixtureProvider):
    def __init__(self, *args, **kwargs):
        raise SessionError('ChatGPT-managed provider activation is blocked: keyring account binding is unqualified')

    @classmethod
    def _for_synthetic_test(cls, binary: Path, model: str):
        """Private schema/transport fixture constructor; never use with live auth."""
        if sys.platform != 'darwin':
            raise SessionError('ChatGPT keyring policy is supported only on macOS')
        instance = cls.__new__(cls)
        FixtureProvider.__init__(instance, binary, model, 'http://127.0.0.1:1/v1')
        return instance

    def _configuration(self):
        return ('model_provider="openai"\nmodel=' + json.dumps(self.model) + '\n'
            'forced_login_method="chatgpt"\ncli_auth_credentials_store="keyring"\n'
            'web_search="disabled"\n[tools]\nexperimental_request_user_input={enabled=false}\n'
            '[features]\n' + ''.join(k + '=' + str(v).lower() + '\n' for k, v in FEATURES.items()) +
            '[model_providers]\n[mcp_servers]\n[plugins]\n')

    def _environment(self):
        return {'PATH': os.defpath, 'HOME': str(self.home), 'CODEX_HOME': str(self.home)}

    def _validate_model_provider(self, effective):
        if (not _same(effective.get('model_providers'), {})
                or effective.get('forced_login_method') != 'chatgpt'
                or effective.get('cli_auth_credentials_store') != 'keyring'
                or effective.get('openai_base_url') is not None
                or effective.get('chatgpt_base_url') != 'https://chatgpt.com/backend-api/'):
            raise ValueError('ChatGPT-managed provider, endpoint, or keyring policy differs')

    def _admission(self):
        return ChatGPTAdmission()

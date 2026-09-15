"""Generated ChatGPT-managed policy with synthetic-only admission tests.

Production construction is blocked until the stable keyring-home lifecycle is qualified.
A fresh HOME is not isolation from macOS's shared keyring, and this module never
copies profiles, reads credentials directly, starts login, or purchases credits.
"""
import json
import os
import pwd
import sys
from pathlib import Path

from .chatgpt_admission import ChatGPTAdmission, ChatGPTIdentity
from .container_session import SessionError
from .provider_home import FixtureProvider, FEATURES, _same


class ChatGPTProvider(FixtureProvider):
    def __init__(self, *args, **kwargs):
        raise SessionError('ChatGPT-managed provider activation is blocked: stable keyring-home lifecycle is unqualified')

    @classmethod
    def _for_synthetic_test(cls, binary: Path, model: str, *, expected_identity: ChatGPTIdentity):
        """Private schema/transport fixture constructor; never use with live auth."""
        return cls._private_policy(binary, model, expected_identity)

    @classmethod
    def _for_fresh_home_metadata_probe(cls, binary: Path, model: str, *,
                                    expected_identity: ChatGPTIdentity):
        """Generate a temporary CODEX_HOME policy for a metadata-only probe.

        Does not reuse a stable enrolled home or its keyring scope. This object
        cannot run model turns; use the separate managed metadata RPC seam.
        """
        instance = cls._private_policy(binary, model, expected_identity)
        instance._metadata_only = True
        return instance

    @classmethod
    def _private_policy(cls, binary, model, expected_identity):
        if sys.platform != 'darwin':
            raise SessionError('ChatGPT keyring policy is supported only on macOS')
        if not isinstance(expected_identity, ChatGPTIdentity):
            raise ValueError('An operator-supplied private ChatGPT identity is required')
        instance = cls.__new__(cls)
        instance._expected_identity = expected_identity
        instance._metadata_only = False
        FixtureProvider.__init__(instance, binary, model, 'http://127.0.0.1:1/v1')
        return instance

    async def run(self, request):
        if self._metadata_only:
            raise SessionError('Fresh-home metadata policy cannot start a model thread or turn')
        return await super().run(request)

    def _configuration(self):
        return ('model_provider="openai"\nmodel=' + json.dumps(self.model) + '\n'
            'forced_login_method="chatgpt"\ncli_auth_credentials_store="keyring"\n'
            'forced_chatgpt_workspace_id=' + json.dumps(self._expected_identity.account_id) + '\n'
            'web_search="disabled"\n[tools]\nexperimental_request_user_input={enabled=false}\n'
            '[features]\n' + ''.join(k + '=' + str(v).lower() + '\n' for k, v in FEATURES.items()) +
            '[model_providers]\n[mcp_servers]\n[plugins]\n')

    def _environment(self):
        # Keychain access needs the operating system user's HOME. CODEX_HOME
        # and provider cwd remain owned temporary paths; ambient HOME is ignored.
        os_home = pwd.getpwuid(os.getuid()).pw_dir
        if not isinstance(os_home, str) or not Path(os_home).is_absolute() or '\0' in os_home:
            raise SessionError('Operating system account home could not be determined')
        return {'PATH': os.defpath, 'HOME': os_home, 'CODEX_HOME': str(self.home)}

    def _validate_model_provider(self, effective):
        if (not _same(effective.get('model_providers'), {})
                or effective.get('forced_login_method') != 'chatgpt'
                or effective.get('cli_auth_credentials_store') != 'keyring'
                or effective.get('forced_chatgpt_workspace_id') != self._expected_identity.account_id
                or effective.get('openai_base_url') is not None
                or effective.get('chatgpt_base_url') != 'https://chatgpt.com/backend-api/'):
            raise ValueError('ChatGPT-managed provider, endpoint, or keyring policy differs')

    def _admission(self):
        return ChatGPTAdmission(self._expected_identity)

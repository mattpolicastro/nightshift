"""Synthetic-tested ChatGPT-managed usage admission; not daemon activation.

Allows included usage or already-available ChatGPT credits. Never buys credits,
redeems reset credits, changes spend limits, or admits API-key/custom-provider auth.
Telemetry is a point-in-time eligibility signal, not a reservation or cost quote.
"""
from decimal import Decimal, InvalidOperation

PLANS = {'free', 'go', 'plus', 'pro', 'prolite', 'team', 'self_serve_business_prolite',
    'self_serve_business_usage_based', 'business', 'ent26', 'enterprise_cbp_automation',
    'enterprise_cbp_usage_based', 'enterprise', 'edu', 'edu_plus', 'edu_pro'}
REACHED = {'rate_limit_reached', 'workspace_owner_credits_depleted', 'workspace_member_credits_depleted',
           'workspace_owner_usage_limit_reached', 'workspace_member_usage_limit_reached'}


def _integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def _decimal(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError('Malformed managed-credit amount')
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError('Malformed managed-credit amount') from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError('Malformed managed-credit amount')
    return parsed


def _label(value, limit=200):
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(ord(character) >= 32 for character in value))


class ChatGPTAdmission:
    def __init__(self):
        self.plan = None
        self.account_confirmed = False
        self.usage_available = False
        self.model_confirmed = False
        self.invalid = False

    def _fail(self, reason):
        self.invalid = True
        raise ValueError(reason)

    def account(self, response):
        if not isinstance(response, dict) or response.get('requiresOpenaiAuth') is not True:
            self._fail('Managed OpenAI authentication was not confirmed')
        account = response.get('account')
        if (not isinstance(account, dict) or account.get('type') != 'chatgpt'
                or account.get('planType') not in PLANS or 'email' not in account
                or account['email'] is not None and not isinstance(account['email'], str)):
            self._fail('A recognized ChatGPT-managed account is required')
        if self.plan is not None and self.plan != account['planType']:
            self._fail('ChatGPT account plan changed during admission')
        self.plan = account['planType']
        self.account_confirmed = True

    def _snapshot(self, value):
        if not isinstance(value, dict):
            self._fail('Managed usage snapshot is unavailable')
        if value.get('limitId') not in (None, 'codex'):
            self._fail('An unsupported usage bucket was reported')
        plan = value.get('planType')
        if plan is not None and (plan not in PLANS or self.plan is not None and plan != self.plan):
            self._fail('Managed usage plan does not match admission')
        reached = value.get('rateLimitReachedType')
        if reached is not None and reached not in REACHED:
            self._fail('Unknown managed usage limit state')
        spend = value.get('spendControlReached')
        if spend is not None and type(spend) is not bool:
            self._fail('Malformed managed spend-control state')
        if spend is True or reached not in (None, 'rate_limit_reached'):
            self._fail('ChatGPT workspace spend controls prevent execution')
        individual = value.get('individualLimit')
        if individual is not None:
            if (not isinstance(individual, dict) or not _integer(individual.get('remainingPercent'), 0, 100)
                    or not _integer(individual.get('resetsAt'))):
                self._fail('Malformed individual managed usage limit')
            _decimal(individual.get('limit'))
            _decimal(individual.get('used'))
            if individual['remainingPercent'] == 0:
                self._fail('Individual managed usage allowance is exhausted')
        windows = []
        for name in ('primary', 'secondary'):
            window = value.get(name)
            if window is None:
                continue
            if not isinstance(window, dict) or not _integer(window.get('usedPercent'), 0, 100):
                self._fail('Malformed managed usage window')
            if window.get('windowDurationMins') is not None and not _integer(window['windowDurationMins'], 1):
                self._fail('Malformed managed usage window duration')
            if window.get('resetsAt') is not None and not _integer(window['resetsAt']):
                self._fail('Malformed managed usage reset time')
            windows.append(window['usedPercent'])
        credits = value.get('credits')
        credit_available = False
        if credits is not None:
            if (not isinstance(credits, dict) or type(credits.get('hasCredits')) is not bool
                    or type(credits.get('unlimited')) is not bool):
                self._fail('Malformed ChatGPT credit availability')
            balance = credits.get('balance')
            parsed = _decimal(balance) if balance is not None else None
            credit_available = credits['unlimited'] or credits['hasCredits']
            if credits['hasCredits'] and not credits['unlimited'] and parsed is not None and parsed == 0:
                self._fail('Contradictory ChatGPT credit availability')
        if not ((windows and all(percent < 100 for percent in windows) and reached is None) or credit_available):
            self._fail('No confirmed included usage or existing ChatGPT credits')

    def rate_limits(self, response):
        if not isinstance(response, dict) or 'rateLimits' not in response:
            self._fail('Managed usage telemetry is missing')
        self._snapshot(response['rateLimits'])
        buckets = response.get('rateLimitsByLimitId')
        if buckets is not None:
            if not isinstance(buckets, dict) or set(buckets) != {'codex'}:
                self._fail('Usage bucket attribution is unsupported')
            self._snapshot(buckets['codex'])
        # Reset-credit details and upsell banners are not spendable credit evidence.
        self.usage_available = True

    async def preflight(self, rpc, model, reasoning_effort=None):
        self.account(await rpc('account/read', {'refreshToken': False}))
        self.rate_limits(await rpc('account/rateLimits/read', {}))
        cursor = None
        seen = set()
        model_ids, model_names = set(), set()
        requested_efforts = None
        for _ in range(5):
            params = {'limit': 100, 'includeHidden': False}
            if cursor is not None:
                params['cursor'] = cursor
            page = await rpc('model/list', params)
            if not isinstance(page, dict) or not isinstance(page.get('data'), list) or len(page['data']) > 100:
                self._fail('Malformed bounded model catalog')
            for item in page['data']:
                if (not isinstance(item, dict)
                        or not all(isinstance(item.get(key), str) for key in ('id', 'model', 'description', 'displayName'))
                        or not item['id'] or not item['model']
                        or type(item.get('hidden')) is not bool or type(item.get('isDefault')) is not bool
                        or not _label(item.get('defaultReasoningEffort'))
                        or not isinstance(item.get('supportedReasoningEfforts'), list)
                        or len(item['supportedReasoningEfforts']) > 16
                        or any(not isinstance(e, dict) or not _label(e.get('reasoningEffort'))
                               or not isinstance(e.get('description'), str) for e in item['supportedReasoningEfforts'])):
                    self._fail('Malformed model catalog entry')
                efforts = [entry['reasoningEffort'] for entry in item['supportedReasoningEfforts']]
                if len(efforts) != len(set(efforts)):
                    self._fail('Duplicate model reasoning effort')
                if item['id'] in model_ids or item['model'] in model_names:
                    self._fail('Duplicate or conflicting model catalog identity')
                model_ids.add(item['id'])
                model_names.add(item['model'])
                if item['model'] == model and item['hidden'] is False:
                    self.model_confirmed = True
                    requested_efforts = set(efforts)
            cursor = page.get('nextCursor')
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or len(cursor) > 1024 or cursor in seen:
                self._fail('Malformed model catalog pagination')
            seen.add(cursor)
        else:
            self._fail('Model catalog exceeded its page budget')
        if reasoning_effort is not None and (not _label(reasoning_effort)
                                               or reasoning_effort not in (requested_efforts or set())):
            self._fail('Requested reasoning effort is unavailable for the model')
        self.check_ready()

    def notification(self, method, params):
        if method == 'account/updated':
            # The notification carries no stable account identity. Even an
            # unchanged plan cannot prove that the account is still the same.
            if self.account_confirmed:
                self._fail('Managed account changed after identity admission')
            if (not isinstance(params, dict) or params.get('authMode') != 'chatgpt'
                    or params.get('planType') not in PLANS
                    or self.plan is not None and params['planType'] != self.plan):
                self._fail('ChatGPT authentication changed or became unavailable')
            self.plan = params['planType']
        elif method == 'account/rateLimits/updated':
            self.rate_limits(params)
        elif method == 'model/rerouted':
            self._fail('Requested model was rerouted')

    def check_ready(self):
        if self.invalid or not (self.account_confirmed and self.usage_available and self.model_confirmed):
            self._fail('ChatGPT admission is incomplete or invalidated')

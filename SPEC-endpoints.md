# Endpoint routing

Declare endpoints in config.toml with their protocol, base URL, billing kind,
authentication environment-variable name, models and context limits. Never put
credentials directly into configuration. A bare model name uses the default
endpoint; endpoint:model selects a declared endpoint. Per-repository models
can override the global selection.

Claude Code speaks the Anthropic wire protocol. An OpenAI-only endpoint needs
an explicitly configured translating proxy. Subscription credentials must not
be forwarded to non-default endpoints. Run preflight before enabling workers.
See config.example.toml for examples.

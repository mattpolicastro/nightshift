# Known limitations

- This is a personal automation system, not a hardened multi-tenant service.
- Only enroll trusted repositories and explicitly approve queued issues.
- Model routing is configured manually; automatic task-based routing is absent.
- Quota telemetry is recorded, but within-window pacing and automatic handling
  of subscription overage are incomplete. Review your provider settings.
- Tool permissions and independent review reduce risk; they do not prove that
  an agent cannot make an unsafe change. Human merge review remains required.
- The Paseo plugin assumes a local checkout at ~/Projects/nightshift and uv at
  /opt/homebrew/bin/uv. Other layouts require adapting its server configuration.
- Large attention queues need multiple refreshes; deferred results remain stale.

## Context

The repository has no `nightshift/forge/` package, Gitea test file, or existing
Gitea capability. Python is 3.12+, runtime dependencies are empty, and build
verification denies network. Tests already register `pytest.mark.scenario`.
`scripts/check_types.py` checks new mypy errors against the committed baseline.
README guidance and the spec-drafting and agent-test-rules requirements inform
this plan; no `AGENTS.md` was present in the checkout. All existing modules and
capability requirements remain unchanged.

## Goals / Non-Goals

Provide a small, independently testable urllib transport with fixed credential
destinations, no redirects, JSON helpers, bounded read retries and optional
pause checks. Preserve headers so later endpoint callers can decide pagination.
Do not introduce a forge Protocol, GiteaForge, callers, config/daemon wiring,
endpoint contracts, automatic pagination, Retry-After scheduling, third-party
dependencies, or a retry framework. The new package initializer is a docstring only.

## Decisions

1. **Public surface and validation.** `nightshift/forge/http.py` exposes
   `GiteaTransport(base_url, token, *, opener=None, guard=None, timeout=30)` and
   `GiteaHTTPError(status, message, transient)`. The exception exposes those three
   attributes; status is an integer when known and None otherwise. Invalid
   constructor/path arguments raise fixed-message ValueError before dispatch.
   Reject nonpositive timeout and empty or CR/LF-containing tokens without
   echoing supplied values.

2. **Origin and path boundary.** Parse bases with `urllib.parse.urlsplit` and
   require a hostname and valid port. Reject userinfo, query, fragment, whitespace,
   controls and backslashes. Permit HTTPS; permit HTTP only for case-insensitive
   localhost or literal 127.0.0.1 and ::1 (bracketed in URLs). Do not resolve DNS
   or broaden the exception to 127/8. Retain an optional base path prefix after
   trimming trailing slashes: `https://forge.example/gitea/` plus
   `/api/v1/version` becomes `https://forge.example/gitea/api/v1/version`.
   API paths must start with exactly one slash and have no scheme, authority,
   query, fragment, whitespace, controls or backslashes. Reject `..` segments in
   raw and once-percent-decoded paths and prefixes, including encoded separators;
   reject decoded backslashes, whitespace and controls too. Preserve accepted
   encoding on the wire. Concatenate validated prefix/path rather than using
   unrestricted urljoin. Compare the constructed URL's scheme, normalized
   hostname and effective port against the base before adding authorization.
   Encode query separately with `urlencode(query, doseq=True)`.

3. **Block redirects before dispatch.** Build a private opener with a custom
   HTTPRedirectHandler subclass. Its http_response/https_response processing
   rejects every status 300–399, including 304 and absent Location, before
   HTTPErrorProcessor can dispatch to redirect handlers (handler order below
   1000). Raise HTTPError for transport normalization; never open Location.
   Also reject directly returned 3xx in request for simple fakes. Checking a
   final response URL alone is too late to protect credentials. Do not modify
   the process-global opener.

   An injected opener is a trusted urllib-compatible object supporting
   `add_handler(handler)` and `open(request, timeout=...)`; install the same
   refusal handler on it. It must honor response handlers and not independently
   redirect. Tests use local scripted openers. Redirect tests use a real
   OpenerDirector pipeline with fake HTTP/HTTPS handlers, including an ordinary
   redirect handler, so ordering is exercised with no sockets. Substitute the
   default builder with one returning such an in-process opener to verify the
   default construction path too. A malicious injected dependency is outside
   this boundary; no new transport interface framework is needed.

4. **Requests and helpers.** `request(method, path, *, query=None,
   json_body=None)` uppercases the method, sends `Authorization: token <token>`
   and `Accept: application/json`, and serializes a non-None body as UTF-8 JSON
   with `Content-Type: application/json`. None means no body. Pass timeout on
   every open. For 2xx, return `(status, headers, parsed_json_or_None)`, preserving
   the case-insensitive response headers. Empty bodies, including 204, produce
   None. Decode other bodies as UTF-8 JSON; invalid encoding/JSON is a
   nontransient GiteaHTTPError with the received status and no body in diagnostics.
   Close responses and HTTPError streams on success, failure and before retry.

   `get_json(path, query=None)` returns only the parsed GET result.
   `get_page(path, query, page, limit)` copies a query mapping, overrides page
   and limit with positive integer arguments, performs one logical GET, and
   returns `(items, headers)`. Reject non-array results as nontransient errors.
   Do not mutate caller data, follow Link, fetch another page, or infer
   completeness from counts/headers. Retries may repeat the same page.

5. **Error classification, retries and guard.** Normalize HTTPError and directly
   returned non-2xx statuses identically. HTTP 429 and 500–599 are transient;
   other HTTP failures, redirects and invalid JSON are not. Connection failures
   (URLError and connection OSError) and timeouts during open/read are transient
   with status None. Catch errors only around transport work, not guard calls
   or programmer argument validation. Only GET retries transient failures, with
   three total attempts and sleeps of 0.1 then 0.2 seconds between attempts.
   Never sleep after the final failure; preserve its status/transient flag.
   Every other method, explicitly POST/PATCH/PUT/DELETE, gets one attempt.
   Patch local sleep in tests; add no public scheduling hook or jitter.

   Call guard() immediately before every opener dispatch, including retries
   after backoff and helper calls. A raising guard propagates unchanged and
   stops dispatch and further retries, even if its exception would otherwise
   be transient. Import no daemon code and read no pause file.

6. **Token secrecy.** Use fixed transport diagnostics and numeric statuses,
   never remote bodies, reason strings, URLs, headers or underlying exception
   text. Suppress displayed exception chaining when normalizing errors. Repr
   omits credentials and injected objects; exception str/repr/args contain only
   safe diagnostics. Emit no transport logs. Test tokens echoed in error bodies,
   Location, exception reasons and invalid JSON, checking formatted tracebacks
   and captured logs as well as str/repr. Guard and opener are trusted callbacks;
   do not pass the token to the guard or format callbacks in diagnostics.

7. **Verification and task boundaries.** Three sessions in one evidence section
   cover the origin/redirect boundary, JSON/helpers, then retry/guard orchestration.
   Each includes tests in `tests/test_gitea_http.py` and targets at most about
   300 changed lines. Every test injects an in-process fake opener, with socket
   calls blocked; fixtures stay in that file. Mark scenarios and retain all
   pre-existing assertions. Run focused tests during each task, then
   `uv run pytest` and `uv run python scripts/check_types.py` for acceptance.
   Do not modify existing modules, test helpers, dependencies or the type baseline.

## Risks / Trade-offs

- All redirects, even same-origin redirects and 304, fail. Users must configure
  a canonical URL. This is deliberate to avoid credential forwarding.
- Origin is scheme/host/effective port, not pinned DNS. urllib's normal TLS
  certificate verification remains enabled; proxy/DNS policy is not expanded.
- Fixed diagnostics sacrifice server details for simple token secrecy. Successful
  JSON and response headers remain caller data, not transport diagnostics.
- Timeout is per attempt, not an overall deadline. Lost write responses remain
  ambiguous and are surfaced without replay; endpoint recovery belongs later.
- CI can prove transport rules in process without a host application, sockets,
  or real waits. It cannot prove real Gitea behavior. Human checklist for later
  integration: confirm canonical URL/prefix, TLS/authentication, and actual
  endpoint page/header behavior against a disposable Gitea instance. Record
  these checks as pending; a later contract suite owns them. They are not tasks
  or completion claims for this change.

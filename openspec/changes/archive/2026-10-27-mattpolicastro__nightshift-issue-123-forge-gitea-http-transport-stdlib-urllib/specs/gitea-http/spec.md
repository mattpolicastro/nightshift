## Purpose

Provide a standalone stdlib urllib transport for a future Gitea forge adapter,
constraining credential destinations and exposing JSON responses without
endpoint-specific policy or daemon integration.

## ADDED Requirements

### Requirement: Configured origin

`GiteaTransport(base_url, token, *, opener=None, guard=None, timeout=30)` SHALL
accept HTTPS bases and accept HTTP only for localhost, 127.0.0.1 or ::1. It SHALL
reject missing hosts, invalid ports, userinfo, query/fragment components and
unsafe prefixes at construction. Invalid arguments SHALL raise ValueError
without echoing their values or accessing the network.

#### Scenario: HTTPS and loopback bases
- **WHEN** a transport is constructed with HTTPS or HTTP using localhost, 127.0.0.1 or bracketed ::1, including explicit valid ports
- **THEN** construction succeeds without opening a connection

#### Scenario: Unsafe bases refused
- **WHEN** a base uses non-loopback HTTP, another scheme, a missing host, invalid port, userinfo, query, fragment, whitespace, controls, backslashes or traversal in its prefix
- **THEN** construction raises ValueError before any opener dispatch

### Requirement: API path confinement

Requests SHALL accept paths beginning with exactly one slash and SHALL reject
absolute URLs, scheme-relative paths, embedded query/fragment components,
backslashes, whitespace, controls and raw or once-percent-decoded `..` segments.
Accepted paths SHALL retain the configured base prefix and origin. Query
parameters SHALL be encoded separately, and authorization SHALL be added only
to a URL matching the configured scheme, normalized host and effective port.

#### Scenario: Unsafe paths refused
- **WHEN** a request receives an absolute URL, //host path, relative path, traversal (including encoded dots or separators), query/fragment or raw/decoded backslash, whitespace or controls
- **THEN** it raises ValueError without dispatch, even if an absolute URL names the configured origin

#### Scenario: Authorization stays on origin
- **WHEN** a valid API path and query containing URL-like values are requested from a base with an optional path prefix and explicit port
- **THEN** the opener receives only the configured origin and prefix with the API path, separately encoded query and Authorization token header
- **THEN** no query value changes the destination

### Requirement: Redirect refusal

The transport SHALL install a private urllib redirect-refusal handler on its
default or compatible injected opener. Every 300–399 response SHALL become a
nontransient GiteaHTTPError before any redirect dispatch, regardless of Location.
It SHALL also reject directly returned 3xx and SHALL never open a redirect target.

#### Scenario: Redirect is never followed
- **WHEN** a fake urllib pipeline supplies any 3xx with same-origin, foreign-origin, scheme-relative or missing Location, including 301, 302, 303, 304, 307 and 308
- **THEN** the transport raises a nontransient error with that status after exactly one dispatch, and the redirect target receives no request or credentials
- **THEN** the rule holds for injected openers and the default opener factory with fake HTTP/HTTPS handlers

### Requirement: JSON requests

request(method, path, *, query=None, json_body=None) SHALL send authenticated
JSON requests with encoded queries and the configured timeout. For 2xx it SHALL
return (status, headers, parsed_json_or_None), preserve case-insensitive headers,
and close response streams. Invalid UTF-8 or JSON SHALL raise nontransient
GiteaHTTPError with the received status and no response content in diagnostics.

#### Scenario: JSON request and response
- **WHEN** a request supplies a lowercase method, repeated query values and a JSON body, and receives 2xx JSON with pagination headers
- **THEN** the fake opener observes the uppercase method, doseq-encoded query, Authorization token and JSON Accept headers, and configured timeout (30 seconds by default)
- **THEN** non-None json_body is UTF-8 JSON with Content-Type application/json; None sends no body
- **THEN** the result contains the HTTP status, case-insensitive headers and parsed JSON, and the response closes

#### Scenario: Empty and invalid bodies
- **WHEN** a 2xx response has an empty body, including status 204, or has nonempty invalid UTF-8 or JSON
- **THEN** an empty body returns None and invalid content raises a nontransient error with the received status
- **THEN** the response closes in either case

### Requirement: JSON and page helpers

`get_json(path, query=None)` SHALL return the parsed GET result.
`get_page(path, query, page, limit)` SHALL copy the query mapping, override page
and limit with positive integers, request that page, and return its JSON array
and headers. It SHALL neither mutate caller query data nor judge completeness
or fetch other pages. Invalid page arguments SHALL fail before dispatch and
non-array results SHALL raise a nontransient GiteaHTTPError.

#### Scenario: Get JSON result
- **WHEN** get_json receives a path and optional query
- **THEN** it performs GET and returns only the parsed JSON or None, preserving request errors

#### Scenario: Single page without completeness policy
- **WHEN** get_page is called with an existing query and valid page/limit and the response contains an empty, short or full JSON array, with or without pagination headers
- **THEN** it returns that array and headers, overrides pagination in a copy of the query, and never follows Link or declares completeness

#### Scenario: Invalid page inputs and results
- **WHEN** page or limit is not a positive integer, or a successful page response is not a JSON array
- **THEN** invalid arguments raise ValueError without dispatch and invalid response shape raises nontransient GiteaHTTPError with the received status

### Requirement: Classified failures and bounded retries

Failures SHALL raise GiteaHTTPError(status, message, transient), with status None
when unavailable. Connection failures, timeouts, 429 and 5xx SHALL be transient;
other HTTP failures and invalid JSON SHALL not. Only GET SHALL retry transient
failures, at most three attempts with 0.1 then 0.2 seconds of backoff. HTTPError
and returned statuses SHALL be classified identically. Streams SHALL close
before retry. Other methods SHALL never retry.

#### Scenario: GET transient recovery and exhaustion
- **WHEN** GET encounters connection failures, timeouts during open/read, 429 or 5xx and then succeeds or continues failing
- **THEN** it makes at most three attempts with the specified intervening backoffs and returns the success or raises the last classified error
- **THEN** it closes available failed streams before retry and never sleeps after the final failure

#### Scenario: Permanent errors stop immediately
- **WHEN** GET receives a nontransient failure such as 401, 403, 404, a redirect or invalid JSON
- **THEN** it raises after one dispatch without backoff, retaining the status and transient false

#### Scenario: Writes never retry
- **WHEN** POST, PATCH, PUT, DELETE or any non-GET method receives a connection failure, timeout, 429 or 5xx, including a lost response after a write
- **THEN** it raises the classified error after one attempt without backoff or replay

### Requirement: Per attempt guard

When supplied, guard() SHALL run immediately before every opener dispatch,
including retries after backoff and requests through helpers. A raising guard
SHALL stop the operation and propagate unchanged without dispatch or retry.

#### Scenario: Guard precedes each attempt
- **WHEN** direct requests or helpers perform one attempt or retry transient GET failures with a supplied guard
- **THEN** every dispatch is immediately preceded by a guard call, including the retry attempts after backoff

#### Scenario: Raising guard stops dispatch
- **WHEN** the guard raises on the initial attempt or before a retry, including an exception otherwise classified as transient
- **THEN** that exception propagates unchanged, no request is dispatched for that attempt, and no further attempts occur

### Requirement: Token secrecy

Transport-generated exceptions, their str/repr and displayed tracebacks, and
transport repr SHALL not disclose the configured token. Diagnostics SHALL use
fixed messages and numeric statuses, excluding raw remote or underlying error
text. The transport SHALL emit no logs and SHALL suppress displayed exception
chains containing unsafe diagnostics.

#### Scenario: Echoed token remains secret
- **WHEN** a fake opener returns or raises failures echoing the token in a body, malformed JSON, Location, HTTP reason or connection exception, or validation rejects a value containing it
- **THEN** exception text, args, repr, formatted tracebacks, transport repr and captured logs do not contain the token

# Managed web research

Hames provides web research as two read-only model tools: `web_search` finds
sources through a private local SearXNG instance, and `web_fetch` retrieves one
selected public page as bounded readable text. Search results and page content
are untrusted evidence, never instructions, and retain their source URLs for
citation.

## First run and service ownership

`hames setup` records one global consent decision under
`~/.hames/services/search/`. Interactive clients offer the same setup before
their first gateway start. Noninteractive gateway startup never prompts or
installs host packages. Hames prefers an available rootless Podman runtime and
otherwise uses Docker; if neither works, the gateway remains usable and reports
search as degraded.

The managed SearXNG image is pinned by immutable digest. Hames binds it only to
loopback, generates a private secret, enables JSON output, persists its
configuration below the search service directory, and uses an ownership-labelled
container volume for the cache. `hames search`
provides status, start, stop, restart, and release-pinned update operations.
Stopping the gateway stops the container; closing a client does not because the
gateway remains alive.

## MCP boundary

The gateway is an MCP host distinct from Hames's private plugin protocol. It
launches the bundled `hames-search-mcp` process over stdio, probes
`server/discover`, and requires protocol `2026-07-28`. Each tool call owns its
child lifecycle and retries once with a fresh child after an unexpected protocol
or process failure. No MCP network port or arbitrary third-party MCP server is
enabled by this slice.

The MCP server talks to SearXNG's loopback JSON API. `web_fetch` performs direct
public HTTP(S) retrieval without browser execution. It validates and pins DNS
destinations on every redirect, rejects credentials and non-public addresses,
allows only ports 80 and 443, and bounds redirects, time, bytes, MIME types, and
returned text. Search and fetch are allowed in Manual, Auto, and Plan modes after
the one-time consent because they do not mutate local state; every call remains
durable, visible under Explore, and cancellable.

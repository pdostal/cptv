# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

**Mid-implementation.** Most read-only diagnostic endpoints are wired up; traceroute + Redis is the remaining chunky piece. `PLAN.md` remains the authoritative specification.

What currently exists:

- `cptv/` package: `main.py`, `config.py`, `negotiation.py`, `middleware.py`.
- Services: `ip`, `geoip` (GeoLite2 City), `asn` (GeoLite2 ASN), `dns` (known-resolver classifier), `clock`.
- Routes: `/`, `/api/v1/`, `/ip`, `/ipv4` + aliases, `/ipv6` + aliases, `/geoip`, `/asn`, `/isp`, `/dns`, `/help`, `/details` + `/more`, `/health`, plus all `api/v1/` variants.
- Templates: `base.html` (Pico CSS + HTMX), rich `index.html` with IP / GeoIP / ASN / DNS / timing / quick-links sections, `details.html`, `help.html`, `section_stub.html`.
- Static assets: `cptv/static/app.js` (progressive enhancements: clock-skew, dual-stack probe, DNSSEC probe via `rhybar.cz`, browser geolocation), `app.css`.
- `pyproject.toml` with ruff + bandit configured, `uv.lock`, `geoip2` dep.
- `package.json` with HTMX + Pico CSS + ESLint (+ `eslint-plugin-security`), flat `eslint.config.js`.
- `Containerfile` (multi-stage: npm asset build → uv sync → runtime with `mtr-tiny` + `cap_net_raw`).
- `scripts/download-geolite2.sh`, `scripts/build-assets.mjs`.
- `.github/workflows/`: `lint.yml`, `test.yml` (pytest + Lighthouse CI), `security.yml` (pip-audit, npm audit, bandit, eslint-security, trivy), `scorecard.yml`, `release.yml`, `geolite2-refresh.yml`.
- `.github/dependabot.yml` for pip / npm / GitHub Actions / docker.
- 74 tests passing: unit tests per service, integration tests for every endpoint / content-negotiation combination.

What's still pending (see `PLAN.md`):

- **Traceroute** — `/traceroute`, `/traceroute.json`, `/traceroute.txt`; `mtr` wrapper, Redis cache (1h TTL keyed by IPv4 / IPv6 `/64`), rate limiting, per-hop rDNS + ASN enrichment, HTMX streaming.
- **Redis integration** — required for traceroute; nothing else uses it yet.
- **Real DNS-side resolver detection** — current `/dns` only classifies a resolver IP when passed as `?resolver=`; true server-side detection needs a DNS-probe host (unique subdomain → authoritative logs). Out of scope for the web app alone.
- **`/health` deep checks** — currently returns `ok` + empty `checks`; should verify GeoLite2 DB, Redis, `mtr-packet`, and `cap_net_raw`.
- **Session history** (§4.9) — `localStorage` schema not yet wired up in the UI.

## Working commands

These all work now — run from the repository root:

- `uv run pytest` — full test suite (74 tests at time of writing)
- `uv run pytest tests/unit/test_ip_service.py::test_name` — single test
- `uv run ruff check .` / `uv run ruff format --check .` — Python lint / format check
- `uv run bandit -c pyproject.toml -r cptv/ -ll` — Python security scan (HIGH blocks CI)
- `uv export --frozen --no-dev --no-emit-project | uvx pip-audit --requirement /dev/stdin --strict` — Python dep CVE scan
- `npm install && npm run build` — install JS/CSS deps and copy vendored assets to `cptv/static/vendor/`
- `npm audit --audit-level=high` — JS dep CVE scan
- `npx eslint .` — JS lint (uses `eslint.config.js` flat config)
- `uv run uvicorn cptv.main:app --reload` — dev server on `127.0.0.1:8000` (GeoIP/ASN gracefully degrade when MMDBs absent)
- `scripts/download-geolite2.sh` — pull GeoLite2 City + ASN MMDBs (needs `MAXMIND_LICENSE_KEY`)
- `podman build -f Containerfile -t cptv:dev .` — build the container image (needs `vendor/geolite2/*.mmdb` present)

## Publishing

Container images are published to **`ghcr.io/pdostal/cptv`** only — no PyPI, no standalone binaries.

- `release.yml` fires on `v*` tag push (or `workflow_dispatch` with a tag input). It downloads fresh GeoLite2 DBs via `MAXMIND_LICENSE_KEY`, builds for `linux/amd64` + `linux/arm64`, pushes `latest`, `<version>`, and `<version>-<yyyymmdd>` tags, and attaches provenance + SBOM.
- `geolite2-refresh.yml` fires weekly (Monday) and can be `workflow_dispatch`ed or `workflow_call`ed. It rebuilds `latest` and a `geolite2-<yyyymmdd>` tag with fresh MaxMind data.
- Both workflows require the repository secret `MAXMIND_LICENSE_KEY` and use `GITHUB_TOKEN` scoped to `packages: write`.

## Architecture invariants

These are decisions from `PLAN.md` that are easy to accidentally break. Read the linked sections before touching related code.

- **Domain-agnostic** (§1, §14): no domain name appears in application code. The base domain is read at runtime from the `X-Base-Domain` nginx header. Only the prefixes `ipv4.`, `ipv6.`, `secure.` are hardcoded — and only as prefixes.
- **HTTP is intentional** (§2): the apex domain is HTTP-only so captive portals can intercept. `https://<domain>` deliberately redirects _down_ to `http://<domain>`. Only `secure.<domain>` enforces TLS. Don't "fix" this.
- **Content negotiation on every endpoint** (§5): each endpoint supports HTML (browser), JSON (`Accept: application/json` or `?format=json`), and plain text (`User-Agent: curl/*` or `?format=text`). Plain-text output is meant to be shell-scriptable (bare values, minimal decoration on `ipv4.`/`ipv6.` subdomain endpoints). Implemented in `cptv/negotiation.py`.
- **Routes vs. services split** (§10): `cptv/routes/` contains thin FastAPI handlers per endpoint group; `cptv/services/` contains the actual logic. Keep handlers thin.
- **Stateless app, Redis holds ephemera** (§4.6, §11): traceroute cache and rate-limit counters live only in Redis. IPv4 keys are full addresses; IPv6 keys are `/64` prefixes. TTL 1 hour. Every traceroute response sets `X-Traceroute-Cached` / `X-Traceroute-Cache-Age` / `X-Traceroute-Refreshes-In`.
- **Server-side vs. client-side split** (§4): DNSSEC validation, dual-stack detection, clock-skew comparison, and browser geolocation are deliberately client-side (the question is about the _client's_ resolver / dual-stack / clock). IP / GeoIP / ASN / DNS resolver are server-side. Don't move features across this boundary.
- **HTMX with no-JS fallback always** (§6): every dynamically rendered piece of UI must have a plain-HTML fallback, tested separately.
- **Privacy** (§13): server logs only the client IP, nothing else. Traceroute cache is operational, not tracking. Don't add user-agent logging, cookies, session IDs, or fingerprinting.
- **mtr capability** (§4.6): `setcap cap_net_raw+ep /usr/bin/mtr-packet` in the `Containerfile`. The Quadlet unit does _not_ need `AddCapability=CAP_NET_RAW`. UDP mode does not remove this requirement.
- **Config via env only** (§6): all behaviour configurable via documented env vars (notably `CPTV_QUICK_LINKS` as JSON array). No magic constants in code.
- **CI least privilege** (§8): every workflow declares `permissions: {}` at the top and opts in per-job. Security scan jobs have zero secrets access. Don't loosen this when adding workflows.

## Known divergences from `PLAN.md`

Minor implementation details that deviate from the plan doc but were chosen deliberately:

- ESLint uses flat config (`eslint.config.js`) instead of `.eslintrc.json`. Flat config is the default in ESLint 9+ and `.eslintrc*` is deprecated.
- Vendored JS/CSS assets live under `cptv/static/vendor/` (not `cptv/static/` directly) so app-authored static files and vendored ones don't collide.
- GeoLite2 MMDBs are staged at `vendor/geolite2/` at build time and baked into the image at `/app/vendor/geolite2/`. Env vars `CPTV_GEOIP_CITY_DB` / `CPTV_GEOIP_ASN_DB` point to them — don't hardcode paths.

## Non-goals

Spelled out in `PLAN.md` §14 — worth repeating because they come up:

- No PyPI package — container distribution only
- No user accounts, auth, or persistent server-side visitor data
- No heavy JS frameworks (React/Vue) — HTMX + Pico CSS only
- No hardcoded domain names

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

**Mid-implementation.** Read-only diagnostic endpoints and traceroute with Valkey caching are wired up. `PLAN.md` remains the authoritative specification.

What currently exists:

- `cptv/` package: `main.py`, `config.py`, `negotiation.py`, `middleware.py`.
- Services: `ip`, `geoip` (GeoLite2 City), `asn` (GeoLite2 ASN), `dns` (known-resolver classifier), `clock`, `traceroute` (mtr wrapper + Valkey cache/rate-limit), `valkey` (connection manager).
- Routes: `/`, `/api/v1/`, `/ip`, `/ipv4` + aliases, `/ipv6` + aliases, `/geoip`, `/asn`, `/isp`, `/dns`, `/traceroute` + `.json`/`.txt`, `/help`, `/details` + `/more`, `/health`, plus all `api/v1/` variants.
- Templates: `base.html` (Pico CSS + HTMX), rich `index.html` with IP / GeoIP / ASN / DNS / timing / quick-links sections, `details.html`, `help.html`, `traceroute.html`, `section_stub.html`.
- Static assets: `cptv/static/app.js` (progressive enhancements: clock-skew, dual-stack probe, DNSSEC probe via `rhybar.cz`, browser geolocation), `app.css`.
- `pyproject.toml` with ruff + bandit configured, `uv.lock`, `geoip2` + `redis[hiredis]` deps.
- `package.json` with HTMX + Pico CSS + ESLint (+ `eslint-plugin-security`), flat `eslint.config.js`.
- `Containerfile` (multi-stage: npm asset build -> uv sync -> runtime with `mtr-tiny` + `cap_net_raw`).
- `scripts/download-geolite2.sh`, `scripts/build-assets.mjs`.
- `.github/workflows/`: `lint.yml`, `test.yml` (pytest with Valkey service container + Lighthouse CI), `security.yml` (pip-audit, npm audit, bandit, eslint-security, trivy), `scorecard.yml`, `release.yml`.
- `.github/dependabot.yml` for pip / npm / GitHub Actions / docker.
- 106 tests passing: unit tests per service, integration tests for every endpoint / content-negotiation combination, traceroute cache/rate-limit tests.

What's still pending (see `PLAN.md`):

- **HTMX streaming** — live traceroute progress via polling or SSE (traceroute runs and caches but does not stream hop-by-hop yet).
- **Real DNS-side resolver detection** — current `/dns` only classifies a resolver IP when passed as `?resolver=`; true server-side detection needs a DNS-probe host (unique subdomain -> authoritative logs). Out of scope for the web app alone.
- **Session history** (§4.9) — `localStorage` schema not yet wired up in the UI.

## Pre-commit checks

Run these from the repository root before committing:

```sh
uv run ruff check .                    # Python lint
uv run ruff format --check .           # Python format check
uv run bandit -c pyproject.toml -r cptv/ -ll  # Python security scan (HIGH blocks CI)
uv run pytest -q                       # Full test suite
npx eslint .                           # JS lint
npm audit --audit-level=high           # JS dep CVE scan
uv export --frozen --no-dev --no-emit-project | uvx pip-audit --requirement /dev/stdin --strict  # Python dep CVE scan
npx markdownlint-cli2 '**/*.md' '#node_modules' '#.venv'  # Markdown lint
```

## Setup / build commands

```sh
uv sync --dev --frozen                 # Install Python deps
npm install && npm run build           # Install JS/CSS deps and build vendor assets
uv run uvicorn cptv.main:app --reload  # Dev server on 127.0.0.1:8000
```

### Running Valkey locally for development

```sh
podman run --rm -d --name cptv-valkey -p 6379:6379 docker.io/valkey/valkey:8-alpine
```

The app defaults to `CPTV_VALKEY_HOST=localhost` and `CPTV_VALKEY_PORT=6379`. Without Valkey, traceroute still works but skips caching.

### Building the container image

```sh
podman build -f Containerfile -t cptv:dev .
```

GeoLite2 databases are not included in the image. Mount them at runtime — see `README.md` for the systemd timer setup.

## Releasing

Container images are published to **`ghcr.io/pdostal/cptv`** only — no PyPI, no standalone binaries. Images are built with `podman`.

- **`release.yml`** — fires on `v*` tag push (or `workflow_dispatch`). Builds for `linux/amd64` + `linux/arm64`, pushes `latest` and `<version>` tags, with provenance + SBOM. Then creates a GitHub Release with auto-generated notes.
- GeoLite2 databases are **not** baked into the image (MaxMind EULA prohibits redistribution). They are bind-mounted at runtime from the host.
- The repository secret `MAXMIND_LICENSE_KEY` is no longer needed by CI.

To cut a release:

```sh
git tag v1.0.0
git push origin v1.0.0
```

## Architecture invariants

These are decisions from `PLAN.md` that are easy to accidentally break. Read the linked sections before touching related code.

- **Domain-agnostic** (§1, §14): no domain name appears in application code. The base domain is read at runtime from the `X-Base-Domain` nginx header. Only the prefixes `ipv4.`, `ipv6.`, `secure.` are hardcoded — and only as prefixes.
- **HTTP is intentional** (§2): the apex domain is HTTP-only so captive portals can intercept. `https://<domain>` deliberately redirects _down_ to `http://<domain>`. Only `secure.<domain>` enforces TLS. Don't "fix" this.
- **Content negotiation on every endpoint** (§5): each endpoint supports HTML (browser), JSON (`Accept: application/json` or `?format=json`), and plain text (`User-Agent: curl/*` or `?format=text`). Plain-text output is meant to be shell-scriptable (bare values, minimal decoration on `ipv4.`/`ipv6.` subdomain endpoints). Implemented in `cptv/negotiation.py`.
- **Routes vs. services split** (§10): `cptv/routes/` contains thin FastAPI handlers per endpoint group; `cptv/services/` contains the actual logic. Keep handlers thin.
- **Stateless app, Valkey holds ephemera** (§4.6, §11): traceroute cache and rate-limit counters live only in Valkey. IPv4 keys are full addresses; IPv6 keys are `/64` prefixes. TTL 1 hour. Every traceroute response sets `X-Traceroute-Cached` / `X-Traceroute-Cache-Age` / `X-Traceroute-Refreshes-In`.
- **Server-side vs. client-side split** (§4): DNSSEC validation, dual-stack detection, clock-skew comparison, and browser geolocation are deliberately client-side (the question is about the _client's_ resolver / dual-stack / clock). IP / GeoIP / ASN / DNS resolver are server-side. Don't move features across this boundary.
- **HTMX with no-JS fallback always** (§6): every dynamically rendered piece of UI must have a plain-HTML fallback, tested separately.
- **Privacy** (§13): server logs only the client IP, nothing else. Traceroute cache is operational, not tracking. Don't add user-agent logging, cookies, session IDs, or fingerprinting.
- **mtr capability** (§4.6): `setcap cap_net_raw+ep /usr/bin/mtr-packet` in the `Containerfile`. The Quadlet unit does _not_ need `AddCapability=CAP_NET_RAW`. UDP mode does not remove this requirement.
- **Config via env only** (§6): all behaviour configurable via documented env vars (notably `CPTV_QUICK_LINKS` as JSON array, `CPTV_VALKEY_HOST`/`CPTV_VALKEY_PORT` for Valkey). No magic constants in code.
- **CI least privilege** (§8): every workflow declares `permissions: {}` at the top and opts in per-job. Security scan jobs have zero secrets access. Don't loosen this when adding workflows.
- **Podman, not Docker** — container images are built with `podman build`. The build definition is `Containerfile` (not `Dockerfile`). Production runs as Podman Quadlet units with `AutoUpdate=registry`.

## CI workflows

| Workflow               | Trigger              | What it does                                                                   |
| ---------------------- | -------------------- | ------------------------------------------------------------------------------ |
| `lint.yml`             | PR                   | `ruff check .`, `ruff format --check .`                                        |
| `test.yml`             | push + PR            | Valkey service container + `pytest -q`; Lighthouse CI (accessibility >= 0.90)  |
| `security.yml`         | push + PR            | `pip-audit`, `npm audit`, `bandit`, `eslint`, `trivy`                          |
| `scorecard.yml`        | weekly + push master | OSSF Scorecard posture report                                                  |
| `release.yml`          | `v*` tag push        | Multi-arch build, push to GHCR, create GitHub Release                          |

## Known divergences from `PLAN.md`

Minor implementation details that deviate from the plan doc but were chosen deliberately:

- ESLint uses flat config (`eslint.config.js`) instead of `.eslintrc.json`. Flat config is the default in ESLint 9+ and `.eslintrc*` is deprecated.
- Vendored JS/CSS assets live under `cptv/static/vendor/` (not `cptv/static/` directly) so app-authored static files and vendored ones don't collide.
- GeoLite2 MMDBs are **not** in the container image. They are bind-mounted from the host at `/app/vendor/geolite2/`. Env vars `CPTV_GEOIP_CITY_DB` / `CPTV_GEOIP_ASN_DB` point to them — don't hardcode paths.
- The `redis` Python package is used as the Valkey protocol client (Valkey is wire-compatible). The service module is `cptv/services/valkey.py`.

## Non-goals

Spelled out in `PLAN.md` §14 — worth repeating because they come up:

- No PyPI package — container distribution only
- No user accounts, auth, or persistent server-side visitor data
- No heavy JS frameworks (React/Vue) — HTMX + Pico CSS only
- No hardcoded domain names

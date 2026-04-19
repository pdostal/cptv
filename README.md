# cptv — CaPTiVe

Self-hosted, nerdy network diagnostics. Shows visitors detailed real-time info about their connection — IP, geolocation, ASN, DNS, traceroute, clock skew, DNSSEC validation.

Served over plain HTTP on purpose so captive portals (hotel Wi-Fi, airport networks) can intercept and redirect it. The `secure.<domain>` prefix has TLS enforced; `ipv4.<domain>` and `ipv6.<domain>` are protocol-forcing endpoints.

The application is **domain-agnostic** — nothing about `cptv.cz` is hardcoded. The base domain is read from the `X-Base-Domain` nginx header at request time. See `PLAN.md` for the full specification.

> **Status:** early implementation. Scaffolding and CI are in place; many features from `PLAN.md` §4 are still pending. See `CLAUDE.md` for what works today vs. what's still to build.

## Running the container

Images are published to `ghcr.io/pdostal/cptv` — container distribution only (no PyPI).

```sh
podman run --rm -p 8000:8000 \
  -e CPTV_QUICK_LINKS='[{"label":"Looking Glass","url":"https://lg.example.net","icon":"🔭"}]' \
  ghcr.io/pdostal/cptv:latest
```

The container expects Redis to be reachable for traceroute caching and rate limiting (see §11 of `PLAN.md`). A Podman-native deployment uses two Quadlet `.container` units (app + Redis) on a shared internal network, fronted by nginx with Certbot handling TLS for `secure.<domain>`.

### Required at deploy time

- An nginx reverse proxy that injects `X-Base-Domain`, `X-Forwarded-For`, and `X-Forwarded-Proto`.
- A Redis instance reachable from the app container.
- GeoLite2 data — baked into the image at build time, no runtime download needed.

### Configuration (environment variables)

| Variable             | Purpose                                                                                                                    | Default                                   |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `CPTV_QUICK_LINKS`   | JSON array of `{label,url,icon?,description?}` objects rendered as a "Quick Links" section. Empty/unset hides the section. | unset                                     |
| `CPTV_GEOIP_CITY_DB` | Path to `GeoLite2-City.mmdb`.                                                                                              | `/app/vendor/geolite2/GeoLite2-City.mmdb` |
| `CPTV_GEOIP_ASN_DB`  | Path to `GeoLite2-ASN.mmdb`.                                                                                               | `/app/vendor/geolite2/GeoLite2-ASN.mmdb`  |

All behaviour must be configurable via env vars — no hardcoded constants in code.

## Building locally

Requires Python 3.12+, `uv`, Node 22+, and `podman` (or Docker).

```sh
# Python deps + test
uv sync --dev --frozen
uv run pytest

# JS/CSS assets
npm install
npm run build      # copies htmx + pico into cptv/static/vendor/

# Run dev server (no Redis, no GeoIP — feature-dependent endpoints will degrade)
uv run uvicorn cptv.main:app --reload
```

To build the container image locally you first need the GeoLite2 databases on disk:

```sh
export MAXMIND_LICENSE_KEY=...     # from https://www.maxmind.com/en/geolite2/signup
scripts/download-geolite2.sh       # writes vendor/geolite2/*.mmdb

podman build -f Containerfile -t cptv:dev .
```

## MaxMind setup (one-time)

1. Sign up at <https://www.maxmind.com/en/geolite2/signup>.
2. Generate a license key in the account portal.
3. For local builds: `export MAXMIND_LICENSE_KEY=...` and run `scripts/download-geolite2.sh`.
4. For CI: add the key as the GitHub Actions repository secret `MAXMIND_LICENSE_KEY`. The `release.yml` and `geolite2-refresh.yml` workflows use it to fetch fresh DBs at image build time.

MaxMind terms require GeoLite2 databases to be refreshed at least every 30 days. The `geolite2-refresh.yml` workflow runs weekly to keep the published image current.

## Publishing

- **`release.yml`** — fires on `v*` tag push (or manual `workflow_dispatch` with a tag). Downloads fresh GeoLite2 DBs, builds `linux/amd64` + `linux/arm64` image, pushes `latest` / `<version>` / `<version>-<yyyymmdd>` tags to GHCR, with provenance attestations and SBOM.
- **`geolite2-refresh.yml`** — fires weekly on Monday. Rebuilds `latest` and a dated `geolite2-<yyyymmdd>` tag. Also callable from other workflows.

Both require the `MAXMIND_LICENSE_KEY` repository secret. `GITHUB_TOKEN` is scoped to `packages: write` only.

## Security + quality scanning

All run on every push and pull request (except OSSF Scorecard which runs weekly):

| Tool                                | Workflow        | Blocks on                              |
| ----------------------------------- | --------------- | -------------------------------------- |
| `ruff` (lint + format)              | `lint.yml`      | any finding                            |
| `pytest`                            | `test.yml`      | any failure                            |
| Lighthouse CI                       | `test.yml`      | accessibility or best-practices < 0.90 |
| `pip-audit`                         | `security.yml`  | any HIGH/CRITICAL CVE                  |
| `npm audit --audit-level=high`      | `security.yml`  | any HIGH+ CVE                          |
| `bandit -ll`                        | `security.yml`  | any HIGH-severity finding              |
| `ESLint` + `eslint-plugin-security` | `security.yml`  | any violation                          |
| `trivy` (container image)           | `security.yml`  | any CRITICAL OS/library CVE            |
| OSSF Scorecard                      | `scorecard.yml` | — (reports posture, uploads SARIF)     |

Scan jobs have **no repository secrets** and `contents: read` only — the principle of least privilege is enforced in the workflow permission blocks.

Dependabot watches `pip`, `npm`, `github-actions`, and `docker` ecosystems weekly.

## Privacy

The server logs **only the client IP** — no user-agent, no cookies, no session IDs, no fingerprinting. Traceroute results are cached in Redis for up to 1 hour keyed by IP (IPv4) or `/64` prefix (IPv6), then expire. Nothing is sold, shared, or sent to third parties. Full statement in `PLAN.md` §13.

## Repository

<https://github.com/pdostal/cptv>

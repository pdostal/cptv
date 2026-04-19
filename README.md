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

The container expects [Valkey](https://valkey.io/) to be reachable for traceroute caching and rate limiting. A Podman-native deployment uses two Quadlet `.container` units (app + Valkey) on a shared internal network, fronted by nginx with Certbot handling TLS for `secure.<domain>`.

### Required at deploy time

- An nginx reverse proxy that injects `X-Base-Domain`, `X-Forwarded-For`, and `X-Forwarded-Proto`.
- A Valkey instance reachable from the app container.
- GeoLite2 MMDB files on the host, bind-mounted into the container (see [GeoLite2 setup](#geolite2-setup) below).

### Configuration (environment variables)

| Variable                     | Purpose                                                                                                                    | Default                                   |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `CPTV_QUICK_LINKS`           | JSON array of `{label,url,icon?,description?}` objects rendered as a "Quick Links" section. Empty/unset hides the section. | unset                                     |
| `CPTV_GEOIP_CITY_DB`         | Path to `GeoLite2-City.mmdb`.                                                                                              | `/app/vendor/geolite2/GeoLite2-City.mmdb` |
| `CPTV_GEOIP_ASN_DB`          | Path to `GeoLite2-ASN.mmdb`.                                                                                               | `/app/vendor/geolite2/GeoLite2-ASN.mmdb`  |
| `CPTV_VALKEY_HOST`           | Valkey server hostname.                                                                                                    | `localhost`                               |
| `CPTV_VALKEY_PORT`           | Valkey server port.                                                                                                        | `6379`                                    |
| `CPTV_TRACEROUTE_CACHE_TTL`  | Traceroute result cache TTL in seconds.                                                                                    | `3600`                                    |
| `CPTV_MTR_PATH`              | Path to the `mtr` binary.                                                                                                  | `mtr`                                     |
| `CPTV_MTR_COUNT`             | Number of ICMP probes per hop.                                                                                             | `5`                                       |

All behaviour is configurable via env vars — no hardcoded constants in code.

---

## Production deployment with Podman Quadlet

The recommended production setup uses **systemd Quadlet** units to run the app and Valkey as rootless Podman containers, fronted by nginx with Certbot for TLS on `secure.<domain>`.

### 1. Create a Podman network

```sh
podman network create cptv
```

### 2. Quadlet unit files

Place these files in `~/.config/containers/systemd/` (rootless) or `/etc/containers/systemd/` (rootful).

#### `cptv-valkey.container`

```ini
[Unit]
Description=Valkey cache for cptv
After=network-online.target

[Container]
Image=docker.io/valkey/valkey:8-alpine
ContainerName=cptv-valkey
Network=cptv
AutoUpdate=registry

[Install]
WantedBy=default.target
```

#### `cptv.container`

```ini
[Unit]
Description=cptv network diagnostics
After=cptv-valkey.service
Requires=cptv-valkey.service

[Container]
Image=ghcr.io/pdostal/cptv:latest
ContainerName=cptv
Network=cptv
PublishPort=127.0.0.1:8000:8000
Volume=%h/.local/share/cptv/geolite2:/app/vendor/geolite2:ro,z
Environment=CPTV_VALKEY_HOST=cptv-valkey
Environment=CPTV_VALKEY_PORT=6379
AutoUpdate=registry

# Uncomment and customise as needed:
# Environment=CPTV_QUICK_LINKS=[{"label":"Status","url":"https://status.example.net","icon":"🟢"}]

[Install]
WantedBy=default.target
```

### 3. Load and start the units

```sh
systemctl --user daemon-reload
systemctl --user start cptv-valkey.service cptv.service
systemctl --user enable cptv-valkey.service cptv.service
```

### 4. Enable Podman auto-update

Podman auto-update pulls newer images from the registry and restarts containers that have `AutoUpdate=registry` set.

```sh
# Enable the systemd timer (rootless)
systemctl --user enable --now podman-auto-update.timer

# Or run a one-off update check
podman auto-update
```

The timer runs daily by default. To customise the schedule, override the timer:

```sh
systemctl --user edit podman-auto-update.timer
```

```ini
[Timer]
OnCalendar=*-*-* 04:00:00
```

---

## nginx reverse proxy

nginx handles TLS termination, subdomain routing, and header injection. The base domain is passed to the app via the `X-Base-Domain` header — this is how the app stays domain-agnostic.

### `/etc/nginx/sites-available/cptv.conf`

Replace `cptv.example.com` with your actual domain throughout.

```nginx
# Extract the base domain (strip www. prefix if present)
map $host $host_base_domain {
    default         cptv.example.com;
    ~^www\.(.+)$    $1;
}

# ---- Plain HTTP: apex + www + ipv4 + ipv6 ----
server {
    listen 80;
    listen [::]:80;
    server_name cptv.example.com www.cptv.example.com
                ipv4.cptv.example.com ipv6.cptv.example.com;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Base-Domain     $host_base_domain;
        proxy_set_header   X-Forwarded-For   $remote_addr;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}

# ---- HTTPS on apex → redirect down to HTTP (captive-portal-friendly) ----
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name cptv.example.com www.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;

    return 301 http://$host$request_uri;
}

# ---- secure.<domain>: HTTP → HTTPS redirect ----
server {
    listen 80;
    listen [::]:80;
    server_name secure.cptv.example.com;

    return 301 https://$host$request_uri;
}

# ---- secure.<domain>: HTTPS (TLS enforced) ----
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name secure.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Base-Domain     $host_base_domain;
        proxy_set_header   X-Forwarded-For   $remote_addr;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Enable the site:

```sh
ln -s /etc/nginx/sites-available/cptv.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

---

## Certbot (nginx plugin)

Obtain and auto-renew TLS certificates for `secure.<domain>` using Certbot with the nginx plugin. The certificates are also used by the apex HTTPS-to-HTTP redirect block.

```sh
# Install certbot + nginx plugin
apt install certbot python3-certbot-nginx    # Debian/Ubuntu
# dnf install certbot python3-certbot-nginx  # Fedora

# Obtain certificate (covers all subdomains in one cert)
certbot --nginx \
  -d cptv.example.com \
  -d www.cptv.example.com \
  -d secure.cptv.example.com \
  -d ipv4.cptv.example.com \
  -d ipv6.cptv.example.com

# Verify auto-renewal timer is active
systemctl status certbot.timer
```

Certbot's nginx plugin will automatically update the `ssl_certificate` / `ssl_certificate_key` paths in your nginx config and set up a systemd timer for renewal.

---

## Building locally

Requires Python 3.12+, `uv`, Node 22+, and `podman` (or Docker).

```sh
# Python deps + test
uv sync --dev --frozen
uv run pytest

# JS/CSS assets
npm install
npm run build      # copies htmx + pico into cptv/static/vendor/

# Run dev server (no Valkey, no GeoIP — feature-dependent endpoints will degrade)
uv run uvicorn cptv.main:app --reload
```

### Running Valkey locally for development

```sh
podman run --rm -d --name cptv-valkey -p 6379:6379 docker.io/valkey/valkey:8-alpine
```

The app defaults to `localhost:6379` and will connect automatically. Stop it with `podman stop cptv-valkey`.

### Building the container image

```sh
podman build -f Containerfile -t cptv:dev .
```

GeoLite2 databases are **not** included in the image — mount them at runtime (see below).

## GeoLite2 setup

The MaxMind EULA prohibits redistributing GeoLite2 databases, so the container image ships without them. You download the databases to the host and bind-mount them into the container. A systemd timer keeps them fresh (MaxMind requires updates at least every 30 days).

### 1. Sign up and get a license key

1. Sign up at <https://www.maxmind.com/en/geolite2/signup>.
2. Generate a license key in the account portal.

### 2. Initial download

```sh
export MAXMIND_LICENSE_KEY=...     # your key
scripts/download-geolite2.sh       # writes ~/.local/share/cptv/geolite2/*.mmdb
```

Or download manually and place `GeoLite2-City.mmdb` and `GeoLite2-ASN.mmdb` in `~/.local/share/cptv/geolite2/`.

### 3. Systemd timer for weekly refresh

Create `~/.config/systemd/user/cptv-geolite2-refresh.service`:

```ini
[Unit]
Description=Refresh GeoLite2 databases for cptv

[Service]
Type=oneshot
Environment=MAXMIND_LICENSE_KEY=<your-key-here>
ExecStart=%h/.local/bin/cptv-download-geolite2.sh
```

Create `~/.config/systemd/user/cptv-geolite2-refresh.timer`:

```ini
[Unit]
Description=Weekly GeoLite2 refresh for cptv

[Timer]
OnCalendar=Mon *-*-* 05:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Copy the download script and enable the timer:

```sh
mkdir -p ~/.local/bin
cp scripts/download-geolite2.sh ~/.local/bin/cptv-download-geolite2.sh
chmod +x ~/.local/bin/cptv-download-geolite2.sh

systemctl --user daemon-reload
systemctl --user enable --now cptv-geolite2-refresh.timer
```

After a GeoLite2 refresh, restart the app container to pick up the new databases:

```sh
systemctl --user restart cptv.service
```

## Publishing

- **`release.yml`** — fires on `v*` tag push (or manual `workflow_dispatch`). Builds `linux/amd64` + `linux/arm64` image, pushes `latest` / `<version>` tags to GHCR with provenance attestations and SBOM, then creates a GitHub Release with auto-generated notes.

`GITHUB_TOKEN` is scoped to `contents: write` + `packages: write` only. No `MAXMIND_LICENSE_KEY` is needed — GeoLite2 databases are not in the image.

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

The server logs **only the client IP** — no user-agent, no cookies, no session IDs, no fingerprinting. Traceroute results are cached in Valkey for up to 1 hour keyed by IP (IPv4) or `/64` prefix (IPv6), then expire. Nothing is sold, shared, or sent to third parties. Full statement in `PLAN.md` §13.

## Repository

<https://github.com/pdostal/cptv>

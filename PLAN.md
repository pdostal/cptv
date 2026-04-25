# cptv.cz — Project Specification

> **CaPTiVe** — A self-hosted, nerdy network diagnostics tool.  
> Repository: **https://github.com/pdostal/cptv**  
> Distributed via GitHub Container Registry only.

---

## 1. Concept

`cptv.cz` is a lightweight Python web application that shows visitors detailed, real-time information about their network connection — IP addresses, geolocation, DNS, ASN, traceroute, timing, and more. It is intentionally served over plain HTTP so that captive portals (hotel Wi-Fi, airport networks, etc.) can intercept and redirect it, making it a useful diagnostic tool.

The name is a double entendre: **CaPTiVe** (captive portal) and **CPTV** as an abbreviation.

### Domain-agnostic design

The application runs under **any domain name** — `cptv.cz`, `cptv.com`, `captive.local`, or anything else. Only the three special subdomain prefixes (`ipv4.`, `ipv6.`, `secure.`) are hardcoded. The base domain is read at runtime from the `X-Base-Domain` nginx proxy header; the app uses it to construct all subdomain links dynamically. No domain name appears anywhere in application code.

---

## 2. Domain Structure

| Subdomain prefix  | Protocol       | Purpose                                                                       |
| ----------------- | -------------- | ----------------------------------------------------------------------------- |
| `ipv4.<domain>`   | HTTP + HTTPS   | DNS A-record only → forces IPv4. Both protocols answer.                       |
| `ipv6.<domain>`   | HTTP + HTTPS   | DNS AAAA-record only → forces IPv6. Both protocols answer.                    |
| `secure.<domain>` | **HTTPS only** | Encrypted endpoint; HTTP → HTTPS redirect in place                            |

The main entry point `<domain>` / `www.<domain>` is **HTTP only** — intentionally unencrypted so captive portals can intercept it.

`ipv4.<domain>` and `ipv6.<domain>` accept both protocols on purpose: the home page's JavaScript dual-stack probe runs on whichever scheme the page itself was loaded with, and a same-scheme cross-origin fetch is the only way to avoid mixed-content blocking on `secure.<domain>`. The "force IPv4 / IPv6" semantics come from DNS (A-only / AAAA-only records), not from the URL scheme.

### Redirect rules

| From                     | To                        | Reason                                          |
| ------------------------ | ------------------------- | ----------------------------------------------- |
| `https://<domain>`       | `http://<domain>`         | Intentional downgrade — captive-portal-friendly |
| `http://secure.<domain>` | `https://secure.<domain>` | Intentional upgrade — enforces TLS              |

### nginx → app headers

```nginx
proxy_set_header X-Base-Domain     $host_base_domain;
proxy_set_header X-Forwarded-For   $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
```

---

## 3. Tech Stack

| Layer                        | Choice                             | Rationale                                                                                          |
| ---------------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------- |
| Language                     | Python 3.12+                       |                                                                                                    |
| Web framework                | **FastAPI**                        | Async, typed, automatic OpenAPI docs at `/docs` and `/redoc`                                       |
| Templating                   | **Jinja2**                         | Bundled with FastAPI                                                                               |
| Dynamic UI                   | **HTMX**                           | Polling/SSE for live results; full JS-free fallback always present                                 |
| CSS framework                | **Pico CSS**                       | Semantic HTML, zero class names, auto dark/light via `prefers-color-scheme`, responsive by default |
| JS/CSS asset management      | **npm**                            | `package.json` + `package-lock.json`; build output gitignored, built fresh in CI                   |
| Python dependency management | **`uv`**                           | Lockfile, virtualenv, `uv run pytest`                                                              |
| GeoIP                        | **MaxMind GeoLite2**               | `.mmdb` baked into the container image at build time                                               |
| Traceroute                   | **`mtr`**                          | `cap_net_raw+ep` set on `mtr-packet` binary inside image                                           |
| DNSSEC detection             | **`rhybar.cz` + `<img>` tag**      | Client-side JS — see section 4.5                                                                   |
| Rate limiting + cache        | **Valkey** (Redis-compatible)      | TTL-based caching and atomic rate limit counters; spoken to via the `redis-py` client              |
| Containerisation             | **Podman Quadlet**                 | Systemd-native; one `.container` unit for app, one for Valkey                                      |
| Reverse proxy                | **nginx + Certbot** (nginx plugin) | TLS termination, redirects, header injection                                                       |
| CI/CD                        | **GitHub Actions**                 | All workflows with tightened permissions — see section 8                                           |
| Security updates             | **Dependabot**                     | Python deps, npm deps, and Actions versions                                                        |

---

## 4. Features

### Where each feature runs

| Feature                                                    | Server-side        | Client-side                              |
| ---------------------------------------------------------- | ------------------ | ---------------------------------------- |
| Client IP, ASN, GeoIP, HTTP version, referrer, resolver IP | ✓                  |                                          |
| BGP looking glass                                          | generates URL only | user clicks link                         |
| Dual-stack detection                                       |                    | ✓ JS fetch to `ipv4.`/`ipv6.` subdomains |
| DNSSEC validation                                          |                    | ✓ JS `<img>` tag → `rhybar.cz`           |
| Clock skew                                                 | provides timestamp | ✓ JS compares to `Date.now()`            |
| Browser geolocation                                        |                    | ✓ JS, explicit opt-in                    |
| Anycast PoP detection                                      |                    | ✓ JS fetch to Cloudflare /cdn-cgi/trace  |
| Resolver whoami probe                                      |                    | ✓ JS DoH query to o-o.myaddr.l.google.com|
| Captive portal redirect origin                             | header inspection  | banner shown when heuristic fires        |

### 4.1 IP Address Information 🌐

- Detect and display the client's **current connection IP** (IPv4 or IPv6)
- On page load, two silent background JS fetches to `ipv4.<domain>/json` and `ipv6.<domain>/json` reveal whether both stacks are available; results injected via HTMX
- Without JS: only the current connection address is shown, with a brief explanatory note
- Clearly indicate which protocol is active and which is preferred
- Link to the forcing subdomains so users can test each protocol explicitly
- The `ipv4.<domain>` and `ipv6.<domain>` subdomains also behave as dedicated single-purpose endpoints — when curled they return just the raw IP address in plain text, useful for scripting
- The IP echo endpoints (`/ip`, `/ipv4`, `/ip4`, `/4`, `/ipv6`, `/ip6`, `/6` and their `/api/v1/` variants) emit `Access-Control-Allow-Origin: *` and `Cache-Control: no-store` so the home page's cross-origin dual-stack probe can read the body. CORS is **not** enabled on other endpoints.

### 4.2 Geolocation 🌍

- Country, region, city — queried server-side from the **MaxMind GeoLite2 City** database baked into the image
- Approximate coordinates shown as a map when JS is available, plain text otherwise
- Small opt-in **"Show my real location"** button triggers the browser Geolocation API (client-side JS); if granted, shown alongside the GeoIP result for comparison
- Browsers block `navigator.geolocation` on insecure origins, and the apex
  `<domain>` is intentionally HTTP. When the JS detects
  `!window.isSecureContext`, the button becomes a deep link that
  navigates to `https://secure.<domain>/?ask-location=1`. The secure
  page recognises that query parameter, auto-triggers the geolocation
  prompt at load, and renders the coordinates on the secure page itself.
  Visiting `secure.<domain>` directly without the query parameter never
  prompts — the API is only invoked on an explicit user gesture.

### 4.3 ASN / Network Information 🔌

All resolved server-side from the client IP:

- Autonomous System Number and name
- ISP / organisation name
- Prefix / CIDR block
- **BGP looking glass link** — server generates a URL to an external looking glass (e.g. HE.net, RIPE RIS) for the client's ASN; user clicks it, their browser does the rest

### 4.4 DNS & Resolver 🔎

- Client's resolver IP address — detected server-side
- Detection of well-known public resolvers (1.1.1.1, 8.8.8.8, 9.9.9.9, etc.)

### 4.5 DNSSEC Validation 🔐

Detected **client-side** via JS `<img>` tag — because the question is whether _the client's resolver_ validates DNSSEC, not the server's.

The browser simultaneously loads:

1. A pixel from **`http://www.rhybar.cz/`** — intentionally signed with an **invalid DNSSEC signature**, operated by **CZ.NIC** (Czech internet registry) specifically as a test domain
2. A pixel from a known validly-signed domain — as a connectivity control

Outcomes:

- Control loads + `rhybar.cz` fails → 🟢 **validating**
- Both load → 🔴 **not validating**
- Control fails → ⚪ **inconclusive**
- JS disabled → ⚪ _"Unable to determine — JavaScript required"_

References:

- CZ.NIC: https://www.nic.cz/
- Internet Society DNSSEC test sites: https://www.internetsociety.org/resources/deploy360/2013/dnssec-test-sites/
- Knot Resolver bogus log docs: https://www.knot-resolver.cz/documentation/

### 4.6 Traceroute / MTR 🛰️

Two execution paths share the same enrichment + caching layer:

- **Blocking** at `/traceroute` (and `/traceroute.json`, `/traceroute.txt`) runs `mtr --json --report --no-dns --mpls -c 5 <client-ip>`, parses the final JSON, enriches each hop with rDNS + ASN + MPLS labels, and returns the full result. JSON / text / no-JS clients consume this.
- **Live streaming** at `/traceroute/stream` runs `mtr --raw --no-dns --mpls -c 5 <client-ip>` and parses its split-format line protocol (`h <pos> <ip>`, `x <pos> <seq>`, `p <pos> <usec> <seq>`, `m <pos> <label> <tc> <s> <ttl>`) hop-by-hop. Each new measurement triggers a Server-Sent Event with an updated `<tr>` for that hop, which the browser swaps into the table by id. The HTML home page consumes this with a small `EventSource` handler in `app.js`.

Both paths use a process-wide `asyncio.Semaphore` (cap configurable via `CPTV_TRACEROUTE_MAX_CONCURRENCY`, default 4; wait window via `CPTV_TRACEROUTE_CONCURRENCY_WAIT_SECONDS`, default 2.0s) so a sudden surge from many distinct client IPs cannot saturate the host.

The `--no-dns` flag is important: DNS resolution of hop names is performed **by the application after each hop reply** — not by mtr itself. This gives full control over per-hop enrichment (rDNS, ASN, MPLS labels) and lets us cache enriched results.

#### Per-hop data displayed

| Field                  | Source                                                |
| ---------------------- | ----------------------------------------------------- |
| Hop number             | mtr                                                   |
| IP address             | mtr                                                   |
| Reverse DNS hostname   | app (post-resolve)                                    |
| ASN number + name      | app (GeoLite2 ASN DB)                                 |
| Loss %                 | mtr                                                   |
| Avg / best / worst RTT | mtr                                                   |
| MPLS labels            | mtr `--mpls` (shown when present, hidden when absent) |
| `* * *`                | mtr (non-responding hops shown as-is)                 |

Example plain-text hop:

```
  3.  203.0.113.1  ae-1.router.example.net  AS1234 Example ISP  3.2ms  MPLS:12345/0/1
```

#### Rate limiting and caching (Valkey)

Cache/rate limit key:

- **IPv4:** full client IP
- **IPv6:** client's **/64 prefix**

Rules:

- 1 fresh MTR run per key per hour; cached in Valkey with 1-hour TTL
- Cached result returned immediately if available
- In-progress request from same key → **rejected**, return partial cache or HTTP 429
- Every response includes `X-Traceroute-Cached`, `X-Traceroute-Cache-Age`, and `X-Traceroute-Refreshes-In` headers
- UI shows: **"⚡ Live result"** or **"🕐 Cached result, age 60s · refreshes in 3540s"**
- Cache hits over the SSE stream replay stored hops with a small inter-hop delay so the live and cached UX are consistent

#### NAT / CGNAT detection

RFC1918 and CGNAT (`100.64.0.0/10`) ranges show a contextual warning ⚠️. Trace still runs.

#### Capability note

```dockerfile
RUN setcap cap_net_raw+ep /usr/bin/mtr-packet
```

In **rootful** Podman / Docker the file capability is honoured and the
container needs no extra flags.

In **rootless** Podman the file capability is dropped when the binary
runs inside an unprivileged user namespace, so `mtr-packet` fails to
open raw sockets and the parent reports
`mtr: Failure to start mtr-packet: Invalid argument`. The Quadlet must
add the capability explicitly:

```ini
[Container]
AddCapability=CAP_NET_RAW
```

UDP mode does not eliminate this requirement either; `mtr-packet` still
needs raw sockets to receive ICMP time-exceeded replies.

### 4.7 Timing & Clock ⏱️

- **Server-side handling time** measured by `RequestTimingMiddleware` and exposed three ways:
  - `X-Response-Time-Ms` response header on every request
  - `timing.rtt_ms` field in the JSON aggregated response
  - Inline display in the HTML timing card
- Server embeds current ISO-8601 timestamp in HTML response
- Client-side JS compares `Date.now()` to server timestamp: deviation > **±5 seconds** → visible warning ⚠️

### 4.8 Referrer / Redirect Origin 🔀

- Captive portal redirect origin displayed when detected
- `cptv.services.redirect_origin` inspects `Referer`, `X-Original-URL`,
  `X-Original-Host`, `X-Forwarded-Host`, `X-Original-URI`, and `X-Rewrite-URL`
- Self-referrers (Referer host matches the base domain or any subdomain of
  it) are ignored so internal navigation does not trigger the warning
- When triggered, the home page renders a yellow ⚠️ "Captive portal detected"
  card with the referrer host and the original URL the visitor was trying to
  reach

### 4.9 Session History 📋

- Every IP observed (current address plus dual-stack probe results) stored in **browser `localStorage`** under the key `cptv:history:v1`
- Each entry records `{ ip, protocol, first_seen, last_seen, count }`
- Rendered most-recent-first on the home page; a "Clear history" button wipes the entry
- Server stores nothing — the privacy footer reiterates this

### 4.10 HTTP Protocol Version

- HTTP/1.1, HTTP/2, or HTTP/3 — detected server-side, shown as a badge
- Passed through nginx via request scope headers

### 4.11 Dark / Light Theme 🌙

- Automatic via Pico CSS `prefers-color-scheme` — no JS required
- 3-state manual toggle (auto → light → dark) in the navigation bar; choice
  persists in `localStorage` under the key `cptv:theme:v1` and is applied
  before the first paint to avoid a flash of incorrect theme
- Page declares `color-scheme: light dark` on `:root` and ships a
  `<meta name="darkreader-lock">` so the Dark Reader extension and
  browser-native "force dark mode" features stand down — cptv handles
  both schemes natively and a second-pass tint just makes it muddy

### 4.12 Quick Links 🔗

Operators can expose a configurable section of useful links — internal dashboards, related tools, looking glasses, status pages, or anything else relevant to their deployment.

#### Configuration

Set a single environment variable containing a JSON array:

```bash
CPTV_QUICK_LINKS='[
  {"label": "My Looking Glass", "url": "https://lg.example.net", "icon": "🔭", "description": "BGP looking glass"},
  {"label": "Network Status",   "url": "https://status.example.net", "icon": "🟢"},
  {"label": "Internal Wiki",    "url": "https://wiki.example.net"}
]'
```

#### Per-link fields

| Field         | Required | Description                                  |
| ------------- | -------- | -------------------------------------------- |
| `label`       | ✓        | Display name of the link                     |
| `url`         | ✓        | Destination URL                              |
| `icon`        | —        | Emoji or short string shown before the label |
| `description` | —        | Optional subtitle shown below the label      |

#### Behaviour

- If `CPTV_QUICK_LINKS` is unset or an empty array, the **entire section is hidden** — no empty box, no placeholder
- If the JSON is malformed, the app logs a warning at startup and hides the section rather than crashing
- Links open in a new tab (`target="_blank"` with `rel="noopener noreferrer"`)
- Section title defaults to **"Quick Links"** and is configurable via
  `CPTV_QUICK_LINKS_TITLE` (also surfaced in the JSON aggregated response
  under `quick_links_title`)
- Quick links are included in the `/api/v1/` JSON response under a `quick_links` key, and omitted entirely when the list is empty
- Quick links are **not** shown in plain-text curl output (not relevant for scripting)

### 4.13 Animations

- Subtle CSS-only animations:
  - 0.3s opacity fade-in with a 40-ms stagger on home page cards
  - Slow opacity pulse on the traceroute status banner while a live trace
    is in progress (toggled via the `is-running` class)
  - Short row-flash on each hop the moment its measurements update
- All animations honour `prefers-reduced-motion: reduce` and switch off

### 4.14 Mobile layout

- Long IPv6 addresses and other code spans wrap mid-string via
  `overflow-wrap: anywhere`
- The traceroute table reflows below 720px viewport width: `<thead>` is
  visually hidden, each `<tr>` becomes a stacked card, and each `<td>`
  renders its `data-label` attribute as a small uppercase legend so the
  full per-hop data is readable on a phone without horizontal scrolling
- Wider intermediate viewports get an `overflow-x: auto` figure as a
  fallback so the table can still scroll horizontally if it doesn't fit

---

## 5. API Reference

**Design principle:** every piece of server-side data has a dedicated API endpoint. All endpoints support content negotiation and are available under both convenience paths and the canonical `/api/v1/` prefix. FastAPI generates interactive OpenAPI docs automatically at `/docs` and `/redoc`.

### Content negotiation

| Signal                                       | Format returned |
| -------------------------------------------- | --------------- |
| `Accept: text/html` (browser default)        | Full HTML page  |
| `Accept: application/json` or `?format=json` | JSON            |
| `User-Agent: curl/*` or `?format=text`       | Plain text      |

### 5.1 `GET /` · `GET /api/v1/`

Full aggregated info — all fields combined.

**Plain text response:**

```
🌐 IP:        2001:db8::1  (IPv6, preferred)
    IPv4:      203.0.113.42

🌍 Country:   CZ  Czech Republic
    City:      Prague
    Coords:    50.0880, 14.4208

🔌 ASN:       AS1234  Example ISP
    Prefix:    203.0.113.0/24

🔎 Resolver:  1.1.1.1  (Cloudflare)
🔐 DNSSEC:    unable to determine (requires browser)

⏱️  RTT:       12ms
    HTTP:      HTTP/2
    Server:    cptv.cz  (https://github.com/pdostal/cptv)
```

**JSON response:**

```json
{
  "ip": {
    "current": "2001:db8::1",
    "protocol": "IPv6",
    "preferred": "IPv6",
    "ipv4": "203.0.113.42",
    "ipv6": "2001:db8::1"
  },
  "geoip": {
    "country_code": "CZ",
    "country": "Czech Republic",
    "region": "Prague",
    "city": "Prague",
    "latitude": 50.088,
    "longitude": 14.4208
  },
  "asn": {
    "number": 1234,
    "name": "Example ISP",
    "prefix": "203.0.113.0/24",
    "looking_glass": "https://lg.he.net/cgi-bin/bgplookingglass?asn=1234"
  },
  "dns": {
    "resolver_ip": "1.1.1.1",
    "resolver_name": "Cloudflare",
    "is_known_public": true
  },
  "dnssec": null,
  "timing": {
    "server_timestamp": "2025-05-12T10:00:00Z",
    "rtt_ms": 12
  },
  "http": {
    "version": "HTTP/2",
    "protocol": "https",
    "referrer": null
  },
  "meta": {
    "server": "cptv.cz",
    "repo": "https://github.com/pdostal/cptv"
  },
  "quick_links": [
    {
      "label": "My Looking Glass",
      "url": "https://lg.example.net",
      "icon": "🔭",
      "description": "BGP looking glass"
    }
  ]
}
```

---

### 5.2 `GET /ip` · `GET /api/v1/ip`

Current connection IP — whichever protocol was used.

**Plain text:** `2001:db8::1`
**JSON:**

```json
{ "ip": "2001:db8::1", "protocol": "IPv6" }
```

---

### 5.3 `GET /ipv4` · `GET /ip4` · `GET /4` · `GET /api/v1/ipv4`

IPv4 address only. Also served by the `ipv4.<domain>` subdomain.

**Plain text:** `203.0.113.42`
**JSON:**

```json
{ "ipv4": "203.0.113.42" }
```

> **Scripting:** `curl ipv4.cptv.cz` returns a bare IP with no labels or extra whitespace.

---

### 5.4 `GET /ipv6` · `GET /ip6` · `GET /6` · `GET /api/v1/ipv6`

IPv6 address only. Also served by the `ipv6.<domain>` subdomain.

**Plain text:** `2001:db8::1`
**JSON:**

```json
{ "ipv6": "2001:db8::1" }
```

---

### 5.5 `GET /geoip` · `GET /api/v1/geoip`

**Plain text:**

```
🌍 Country:   CZ  Czech Republic
    Region:    Prague
    City:      Prague
    Coords:    50.0880, 14.4208
```

**JSON:**

```json
{
  "country_code": "CZ",
  "country": "Czech Republic",
  "region": "Prague",
  "city": "Prague",
  "latitude": 50.088,
  "longitude": 14.4208
}
```

---

### 5.6 `GET /asn` · `GET /api/v1/asn`

**Plain text:**

```
🔌 ASN:       AS1234
    Name:      Example ISP
    Prefix:    203.0.113.0/24
    Looking glass: https://lg.he.net/...
```

**JSON:**

```json
{
  "asn": 1234,
  "name": "Example ISP",
  "prefix": "203.0.113.0/24",
  "looking_glass": "https://lg.he.net/cgi-bin/bgplookingglass?asn=1234"
}
```

---

### 5.7 `GET /isp` · `GET /api/v1/isp`

Focused alias of ASN data — ISP name only.

**Plain text:** `Example ISP (AS1234)`
**JSON:**

```json
{ "isp": "Example ISP", "asn": 1234 }
```

---

### 5.8 `GET /dns` · `GET /api/v1/dns`

**Plain text:**

```
🔎 Resolver:  1.1.1.1
    Known as:  Cloudflare
```

**JSON:**

```json
{
  "resolver_ip": "1.1.1.1",
  "resolver_name": "Cloudflare",
  "is_known_public": true
}
```

---

### 5.9 `GET /traceroute` · `GET /api/v1/traceroute`

Starts or returns a cached MTR traceroute to the client IP. HTML streams via HTMX. JSON/text block until complete or serve cache immediately.

**Response headers always present:**

```
X-Traceroute-Cached: false
X-Traceroute-Cache-Age: 0
X-Traceroute-Refreshes-In: 3600
```

**Plain text:**

```
🛰️  Traceroute to 203.0.113.42
    ⚡ Live result

  1.  192.168.1.1    router.local              AS0    -            0.4ms
  2.  10.0.0.1       -                         AS0    -            1.1ms
  3.  203.0.113.1    ae-1.router.example.net   AS1234 Example ISP  3.2ms  MPLS:12345/0/1
  4.  198.51.100.5   core1.isp.net             AS1234 Example ISP  8.7ms
  5.  203.0.113.42   target.example.com        AS1234 Example ISP 12.1ms
```

**JSON:**

```json
{
  "cached": false,
  "ran_at": "2025-05-12T10:00:00Z",
  "target": "203.0.113.42",
  "hops": [
    {
      "hop": 1,
      "ip": "192.168.1.1",
      "rdns": "router.local",
      "asn": null,
      "asn_name": null,
      "loss_pct": 0.0,
      "avg_ms": 0.4,
      "best_ms": 0.3,
      "worst_ms": 0.6,
      "mpls": []
    },
    {
      "hop": 3,
      "ip": "203.0.113.1",
      "rdns": "ae-1.router.example.net",
      "asn": 1234,
      "asn_name": "Example ISP",
      "loss_pct": 0.0,
      "avg_ms": 3.2,
      "best_ms": 3.0,
      "worst_ms": 3.8,
      "mpls": [{ "label": 12345, "exp": 0, "ttl": 1 }]
    }
  ]
}
```

---

### 5.10 `GET /traceroute.json` · `GET /api/v1/traceroute.json`

Always returns JSON regardless of `Accept` header.

---

### 5.11 `GET /traceroute.txt` · `GET /api/v1/traceroute.txt`

Always returns plain text regardless of `Accept` header.

---

### 5.12 `GET /details` · `GET /more` · `GET /api/v1/details`

Extended verbose output — all fields plus additional technical detail. Plain text by default when curled, full HTML page in browser.

---

### 5.13 `GET /help` · `GET /api/v1/help`

Usage guide — HTML in browser, plain text when curled.

**Plain text:**

```
cptv.cz — CaPTiVe network diagnostics
https://github.com/pdostal/cptv

ENDPOINTS
  /              Full info (auto-detects format)
  /ip            Current IP address
  /ipv4 /ip4 /4  IPv4 address only
  /ipv6 /ip6 /6  IPv6 address only
  /geoip         Geolocation
  /asn           ASN and network info
  /isp           ISP name
  /dns           DNS resolver info
  /traceroute    Traceroute to your IP
  /details       Extended output
  /help          This help text

SUBDOMAINS
  ipv4.<domain>   Force IPv4  (also: curl ipv4.cptv.cz)
  ipv6.<domain>   Force IPv6  (also: curl ipv6.cptv.cz)
  secure.<domain> HTTPS only

FORMAT
  Append ?format=json or ?format=text to any endpoint.
  Or set Accept: application/json header.
  curl auto-detected — plain text returned by default.

EXAMPLES
  curl cptv.cz
  curl ipv4.cptv.cz
  curl cptv.cz/geoip
  curl cptv.cz/asn?format=json
  curl cptv.cz/traceroute.txt
  MY_IP=$(curl -s ipv4.cptv.cz)
```

---

### 5.14 `GET /health`

Rich health check. `200 OK` if all pass, `503` otherwise. Always JSON.

```json
{
  "status": "ok",
  "checks": {
    "geoip_db": "ok",
    "valkey": "ok",
    "mtr_packet": "ok",
    "mtr_capability": "ok"
  }
}
```

---

### 5.15 `GET /traceroute/stream` · `GET /api/v1/traceroute/stream`

Server-Sent Events stream of traceroute progress. Returns `Content-Type: text/event-stream`. The data payload of each event is an HTML fragment so the `htmx-ext-sse` extension on the home page can swap rows in directly without a custom JSON handler.

| Event    | When                                | Data payload                                                  |
| -------- | ----------------------------------- | ------------------------------------------------------------- |
| `status` | once at the start                   | Banner indicating live or cached, with cache-age if cached    |
| `hop`    | once per hop, in order              | A `<tr>` rendered from `_hop_row.html` (IP, rDNS, ASN, RTT)   |
| `done`   | once at the end                     | `<p><small>Trace complete.</small></p>`                       |
| `error`  | on rate-limit collision or mtr fail | `<p><mark>warning text</mark></p>`                            |

JSON / plain-text consumers should keep using `/traceroute` (blocking) or `/traceroute.json` / `/traceroute.txt`.

---

### 5.16 FastAPI auto-generated docs

| Path            | Description                           |
| --------------- | ------------------------------------- |
| `/docs`         | Swagger UI — interactive API explorer |
| `/redoc`        | ReDoc — clean API reference           |
| `/openapi.json` | Raw OpenAPI schema                    |

---

## 6. Code Quality

- **Test-driven development (TDD)** — tests written before or alongside implementation
- **`ruff`** for Python linting and formatting — enforced in CI
- **`ESLint` + `eslint-plugin-security`** for JS linting — enforced in CI
- Unit tests for all service logic; integration tests for all endpoints
- Code must be **human-readable and reviewer-friendly**: clear naming, short focused functions, docstrings on all public interfaces, no clever one-liners that obscure intent
- HTMX used for all dynamically rendered content; plain HTML fallback always present and tested separately
- All behaviour configurable via environment variables documented in `README.md` — no magic constants buried in code
- Emojis used in UI and plain-text output where they add clarity 🎉

---

## 7. Security & Quality Scanning

### 7.1 Tool overview

| Tool                         | What it catches                         | Needs secrets?         | Runs when       |
| ---------------------------- | --------------------------------------- | ---------------------- | --------------- |
| **Dependabot**               | Outdated Python + npm + Actions deps    | No                     | Weekly PRs      |
| **`pip-audit`**              | Python dep CVEs (OSV / PyPA advisories) | No                     | Every push + PR |
| **`npm audit`**              | JS dep CVEs                             | No                     | Every push + PR |
| **`bandit`**                 | Python code security antipatterns       | No                     | Every push + PR |
| **`eslint-plugin-security`** | JS code security antipatterns           | No                     | Every push + PR |
| **`trivy`**                  | Container image OS-level CVEs           | No                     | On image build  |
| **OSSF Scorecard**           | Supply chain posture score + badge      | Read-only token only   | Weekly cron     |
| **Lighthouse CI**            | Accessibility ≥ 90, best practices ≥ 90 | No                     | Every PR        |
| **GitHub secret scanning**   | Accidentally committed secrets          | N/A (platform feature) | Always on       |

### 7.2 Severity gates

| Tool          | Blocking threshold                 |
| ------------- | ---------------------------------- |
| `pip-audit`   | Any HIGH or CRITICAL → fail        |
| `npm audit`   | `--audit-level=high` → fail        |
| `bandit`      | Any HIGH severity finding → fail   |
| `trivy`       | Any CRITICAL in final image → fail |
| Lighthouse CI | Accessibility < 90 → fail          |

Lower severity findings are reported in the job summary but do not block merging.

### 7.3 Dependabot configuration

`dependabot.yml` covers three ecosystems:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: pip # uv.lock / pyproject.toml
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: npm # package.json / package-lock.json
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
```

---

## 8. CI/CD Workflows & Permissions

### Permissions philosophy

GitHub Actions workflows follow the **principle of least privilege**. Permissions are declared explicitly at the workflow level and overridden per-job where a job needs less. The default for all workflows is `permissions: {}` (nothing) — individual jobs opt in only to what they need.

Security and scanning jobs in particular are **explicitly denied** write access and secret access. `trivy`, `pip-audit`, `npm audit`, `bandit`, and `ESLint` need no GitHub token at all to do their job.

### Permission matrix

| Workflow / Job                   | `contents` | `packages`           | `security-events`      | `id-token` | Secrets used                          |
| -------------------------------- | ---------- | -------------------- | ---------------------- | ---------- | ------------------------------------- |
| `lint.yml`                       | `read`     | —                    | —                      | —          | none                                  |
| `test.yml` / tests               | `read`     | —                    | —                      | —          | none                                  |
| `test.yml` / Lighthouse CI       | `read`     | —                    | —                      | —          | none                                  |
| `security.yml` / pip-audit       | `read`     | —                    | —                      | —          | **none**                              |
| `security.yml` / npm audit       | `read`     | —                    | —                      | —          | **none**                              |
| `security.yml` / bandit          | `read`     | —                    | —                      | —          | **none**                              |
| `security.yml` / eslint-security | `read`     | —                    | —                      | —          | **none**                              |
| `security.yml` / trivy           | `read`     | —                    | `write` (SARIF upload) | —          | **none**                              |
| `scorecard.yml`                  | `read`     | —                    | `write` (SARIF upload) | `write`    | Read-only Scorecard token             |
| `release.yml`                    | `read`     | `write` (push image) | —                      | —          | `MAXMIND_LICENSE_KEY`, `GITHUB_TOKEN` |
| `geolite2-refresh.yml`           | `read`     | `write` (push image) | —                      | —          | `MAXMIND_LICENSE_KEY`, `GITHUB_TOKEN` |

### Workflow descriptions

#### `lint.yml` — Linting

Trigger: every PR.  
Jobs: Super-linter (Python/ruff, YAML, Markdown, Dockerfile, shell, JS/ESLint).  
Permissions: `contents: read`.

#### `test.yml` — Tests + Lighthouse CI

Trigger: every push + PR.  
Jobs:

1. `pytest` — spins up FastAPI with mocked Redis and GeoIP, runs full test suite via `uv run pytest`
2. `lighthouse` — starts the app against the test fixtures, runs Lighthouse CI against `/`, `/help`, `/traceroute`; fails if accessibility < 90 or best-practices < 90

Permissions: `contents: read`.

#### `security.yml` — Security scanning

Trigger: every push + PR.  
Jobs (all run in parallel, all `contents: read`, **no secrets**):

- `pip-audit` — `uv export | pip-audit --stdin`; fails on HIGH/CRITICAL
- `npm-audit` — `npm audit --audit-level=high`
- `bandit` — `bandit -r cptv/ -ll`; fails on HIGH severity
- `eslint-security` — `ESLint` with `eslint-plugin-security` ruleset
- `trivy` — scans the built container image; uploads SARIF to GitHub Security tab; fails on CRITICAL (`contents: read`, `security-events: write`)

#### `scorecard.yml` — OSSF Scorecard

Trigger: weekly cron + push to main.  
Permissions: `contents: read`, `security-events: write`, `id-token: write`.  
Uses a read-only Scorecard token (not `GITHUB_TOKEN`). Uploads results to GitHub Security tab. Generates a public badge for the `README.md`.

#### `release.yml` — Release

Trigger: tagged release (`v*`).  
Permissions: `contents: read`, `packages: write`.  
Secrets: `MAXMIND_LICENSE_KEY`, `GITHUB_TOKEN` (scoped to package push only).  
Steps: calls `geolite2-refresh.yml` → builds image → tags `$version-$date` + `latest` → pushes to `ghcr.io/pdostal/cptv`.

#### `geolite2-refresh.yml` — GeoLite2 DB refresh

Trigger: weekly cron (Monday) + `workflow_dispatch` + called by `release.yml`.  
Permissions: `contents: read`, `packages: write`.  
Secrets: `MAXMIND_LICENSE_KEY`, `GITHUB_TOKEN`.  
Steps: downloads fresh `GeoLite2-City.mmdb` → builds container image → pushes to GHCR.

### Branch protection

The `main` branch requires:

- All of `lint`, `test`, `security` workflows to pass before merge
- At least one approving review
- No force-pushes
- Signed commits (recommended)

---

## 9. GeoLite2 Database

### Obtaining a license key

1. Sign up at [maxmind.com](https://www.maxmind.com/en/geolite2/signup)
2. Generate a license key in the account portal
3. Add as GitHub Actions secret: `MAXMIND_LICENSE_KEY`

The `README.md` documents this process step by step.

### Helper script

`scripts/download-geolite2.sh` — for operators building locally or outside CI. Documents the MaxMind download URL, required env vars, and expected output path.

---

## 10. Repository Layout

```
cptv/                         # https://github.com/pdostal/cptv
├── cptv/
│   ├── __init__.py
│   ├── main.py               # FastAPI app entry point
│   ├── config.py             # All settings via env vars (incl. CPTV_QUICK_LINKS JSON)
│   ├── routes/
│   │   ├── index.py          # /
│   │   ├── ip.py             # /ip, /ipv4, /ip4, /4, /ipv6, /ip6, /6
│   │   ├── geoip.py          # /geoip
│   │   ├── asn.py            # /asn, /isp
│   │   ├── dns.py            # /dns
│   │   ├── traceroute.py     # /traceroute, /traceroute.json, /traceroute.txt
│   │   ├── help.py           # /help
│   │   └── health.py         # /health
│   ├── services/
│   │   ├── ip.py             # IP detection, NAT/CGNAT classification
│   │   ├── geoip.py          # MaxMind GeoLite2 City + ASN queries
│   │   ├── asn.py            # ASN lookups + looking glass URL generation
│   │   ├── dns.py            # Resolver IP detection
│   │   ├── traceroute.py     # mtr wrapper, per-hop enrichment (rDNS + ASN + MPLS),
│   │   │                     # Valkey cache/rate limit, SSE streaming generator,
│   │   │                     # process-wide concurrency semaphore
│   │   ├── valkey.py         # Valkey connection manager (uses redis-py client)
│   │   ├── redirect_origin.py # Captive-portal heuristic from headers
│   │   └── clock.py          # Server timestamp for client clock skew check
│   ├── templates/            # Jinja2 HTML templates (incl. _hop_row.html partial for SSE)
│   ├── middleware.py         # SubdomainMiddleware + RequestTimingMiddleware
│   └── static/
│       ├── app.js            # Clock skew, dual-stack, DNSSEC, anycast PoP,
│       │                     # resolver whoami, history, theme toggle, SSE consumer
│       ├── app.css           # App-authored CSS incl. animations
│       └── vendor/           # Built by npm (gitignored): pico.min.css, htmx.min.js
├── tests/
│   ├── unit/                 # Per-service unit tests
│   └── integration/          # Endpoint integration tests
├── scripts/
│   └── download-geolite2.sh  # Local/manual GeoLite2 download helper
├── package.json              # JS/CSS deps (HTMX, Pico CSS, ESLint, eslint-plugin-security)
├── package-lock.json
├── eslint.config.js          # ESLint flat config with security plugin
├── pyproject.toml            # Python build metadata + ruff + bandit config
├── uv.lock
├── lighthouserc.json         # Lighthouse CI thresholds (accessibility ≥ 90)
├── .github/
│   ├── dependabot.yml        # pip + npm + actions
│   └── workflows/
│       ├── lint.yml
│       ├── test.yml
│       ├── security.yml
│       ├── scorecard.yml
│       ├── release.yml
│       └── geolite2-refresh.yml
├── Containerfile             # OCI image (npm build, mtr, cap_net_raw, GeoLite2 DB)
└── README.md                 # Setup: MaxMind signup, nginx config, Quadlet units, certbot
```

---

## 11. Deployment Notes

All configuration examples live in `README.md`. No standalone config files committed to the repository root.

- App runs on `127.0.0.1:8000`; Valkey on a Podman internal network between the two containers
- nginx handles TLS, redirects, subdomain routing, and `X-Base-Domain` injection
- Certbot manages certificates for `secure.<domain>` (and optionally `ipv4.`/`ipv6.`)
- App is **stateless** — all ephemeral state (traceroute cache, rate limits) lives in Valkey
- `node_modules/` and `cptv/static/vendor/` build output are gitignored; npm build runs in `Containerfile`
- **GeoLite2 MMDBs are not baked into the image** (MaxMind EULA prohibits redistribution). They are bind-mounted at runtime from the host into `/app/vendor/geolite2/`; the env vars `CPTV_GEOIP_CITY_DB` and `CPTV_GEOIP_ASN_DB` point to them.

---

## 12. Open Issues (to be filed on GitHub)

The original list (clock-skew UX, global concurrency cap, anycast PoP,
configurable Quick Links title, hop-by-hop streaming, resolver whoami,
theme toggle, captive portal banner, animations) has been delivered and is
documented in §4. Future items will be tracked directly on GitHub.

Speculative future work that's intentionally out of scope today:

- Operator-run DNS authoritative subdomain that records resolver IPs from
  `<token>.dns-probe.<domain>` queries, cross-referenced with an HTTP probe
  to identify the visitor's actual recursive resolver server-side. Today
  the same need is met client-side via the Google DoH whoami probe.
- HTTP/3 (QUIC) version detection and badge.
- IPv6 reverse DNS using PTR records below the `/64` to surface
  RFC-formatted prefix delegation hints.

---

## 13. Privacy Statement

This statement is displayed at the bottom of every HTML page and included in the `/help` plain-text output.

### What the server logs

- The server logs **only the client's IP address** — the minimum necessary for operating a network diagnostic tool
- No geolocation data, no browser fingerprint, no user-agent string, no cookies, no session identifiers are stored server-side
- Traceroute results are cached in Valkey keyed by IP address (or /64 prefix for IPv6) for up to 1 hour, then automatically expired — this is operational caching, not user tracking
- No data is sold, shared, or sent to third parties

### What stays in your browser

- **Connection history** (IPv4/IPv6 addresses from previous visits, with first/last seen timestamps) is stored exclusively in **your browser's `localStorage`** under the key `cptv:history:v1` — it never leaves your device and is never sent to the server. A **Clear history** button on the home page wipes it.
- **Geolocation** (if you opt in via the "Show my real location" button) is used only to display your position on the map in your browser — the coordinates are never transmitted to the server
- **DNSSEC test** results are determined entirely client-side by your browser loading an image — no result is reported back to the server
- **Anycast PoP probe** to `https://1.1.1.1/cdn-cgi/trace` happens directly from your browser — the response is parsed in JavaScript and never seen by us
- **Resolver whoami probe** to `https://dns.google/resolve?name=o-o.myaddr.l.google.com` happens directly from your browser — the answer is rendered in your DOM and never seen by us
- **Theme preference** (auto / light / dark) is stored in `localStorage` under `cptv:theme:v1`
- **Clock-skew dismissal** is stored in `localStorage` under `cptv:clock-skew-dismissed:v1` so the same warning doesn't keep nagging across visits

### In plain English

> The server sees your IP address. Everything else — your location, your history, your DNSSEC status — is computed in your browser and stays there. 🔒

---

## 14. Non-Goals

- No PyPI package — container only
- No user accounts or authentication
- No persistent server-side storage of visitor data
- No advertising or tracking
- No heavy JavaScript frameworks (React, Vue, etc.)
- No hardcoded domain names in application code

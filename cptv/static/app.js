// cptv client-side progressive enhancements.
// Everything the server can compute is already rendered by Jinja; this file
// only adds dual-stack detection, clock-skew warning, DNSSEC probe, and
// optional browser geolocation comparison. All of it degrades silently.

(() => {
  const qs = (s, root = document) => root.querySelector(s);
  const on = (el, ev, fn) => el && el.addEventListener(ev, fn);
  const ImageCtor = window.Image;

  // ---------- clock skew ----------
  function checkClockSkew() {
    const el = qs("#server-time");
    if (!el) return;
    const serverTs = el.getAttribute("data-server-ts");
    if (!serverTs) return;
    const server = Date.parse(serverTs);
    if (Number.isNaN(server)) return;
    const diffSeconds = Math.round((Date.now() - server) / 1000);
    if (Math.abs(diffSeconds) > 5) {
      const warn = qs("#clock-skew-warning");
      if (!warn) return;
      warn.hidden = false;
      const span = qs("span", warn);
      if (span) span.textContent = `${diffSeconds}s`;
    }
  }

  // ---------- dual-stack detection ----------
  async function detectDualStack() {
    const host = window.location.hostname;
    const port = window.location.port ? `:${window.location.port}` : "";
    if (!host || host === "localhost" || host === "127.0.0.1") return;

    // Peel off any existing "ipv4." / "ipv6." prefix to find the base domain.
    const parts = host.split(".");
    const base = ["ipv4", "ipv6"].includes(parts[0])
      ? parts.slice(1).join(".")
      : host;

    const probes = [
      ["ipv4", `http://ipv4.${base}${port}/4?format=text`],
      ["ipv6", `http://ipv6.${base}${port}/6?format=text`],
    ];
    for (const [stack, url] of probes) {
      try {
        const resp = await fetch(url, { cache: "no-store" });
        if (!resp.ok) continue;
        const text = (await resp.text()).trim();
        if (!text) continue;
        const el = document.querySelector(`[data-ds="${stack}"]`);
        if (el) el.textContent = text;
      } catch {
        /* silent — dual-stack probe is best-effort */
      }
    }
  }

  // ---------- DNSSEC probe ----------
  // Loads an image from www.rhybar.cz (intentionally signed with an invalid
  // DNSSEC signature by CZ.NIC) and from a control host. If only the control
  // loads, the resolver validates DNSSEC.
  function checkDnssec() {
    const badge = qs("#dnssec-status");
    if (!badge) return;

    const results = { control: null, bogus: null };
    const settle = () => {
      if (results.control === null || results.bogus === null) return;
      if (results.control === false) {
        badge.textContent = "⚪ inconclusive (control unreachable)";
      } else if (results.bogus === true) {
        badge.textContent = "🔴 not validating";
      } else {
        badge.textContent = "🟢 validating";
      }
    };
    const probe = (url, key) => {
      const img = new ImageCtor();
      img.onload = () => {
        results[key] = true;
        settle();
      };
      img.onerror = () => {
        results[key] = false;
        settle();
      };
      img.src = url;
    };
    // Control: a validly-signed site. Bogus: rhybar.cz (CZ.NIC DNSSEC test).
    probe(`https://www.iana.org/favicon.ico?t=${Date.now()}`, "control");
    probe(`http://www.rhybar.cz/favicon.ico?t=${Date.now()}`, "bogus");

    // Don't hang forever.
    setTimeout(() => {
      if (results.control === null) results.control = false;
      if (results.bogus === null) results.bogus = false;
      settle();
    }, 8000);
  }

  // ---------- browser geolocation (opt-in) ----------
  function wireGeolocationButton() {
    const btn = qs("#request-geolocation");
    const out = qs("#browser-location");
    if (!btn || !out || !navigator.geolocation) return;
    on(btn, "click", () => {
      out.textContent = "requesting…";
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const { latitude, longitude, accuracy } = pos.coords;
          out.innerHTML = `<code>${latitude.toFixed(4)}, ${longitude.toFixed(4)}</code> (±${Math.round(accuracy)} m)`;
        },
        (err) => {
          out.textContent = `denied or unavailable (${err.message})`;
        },
        { timeout: 10000, maximumAge: 0 },
      );
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    checkClockSkew();
    detectDualStack();
    checkDnssec();
    wireGeolocationButton();
  });
})();

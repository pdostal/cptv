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

  // ---------- session history (localStorage, never sent to server) ----------
  const HISTORY_KEY = "cptv:history:v1";

  function readHistory() {
    try {
      const raw = window.localStorage.getItem(HISTORY_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function writeHistory(entries) {
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
    } catch {
      /* storage may be disabled or full — silently give up */
    }
  }

  function recordSeen(ip, protocol) {
    if (!ip || ip === "…" || ip === "—" || ip === "unknown") return null;
    const now = new Date().toISOString();
    const entries = readHistory();
    const existing = entries.find((e) => e.ip === ip);
    if (existing) {
      existing.last_seen = now;
      existing.count = (existing.count || 1) + 1;
      if (protocol && !existing.protocol) existing.protocol = protocol;
    } else {
      entries.push({
        ip,
        protocol: protocol || null,
        first_seen: now,
        last_seen: now,
        count: 1,
      });
    }
    writeHistory(entries);
    return entries;
  }

  function renderHistory() {
    const list = qs("#ip-history");
    if (!list) return;
    const entries = readHistory();
    list.innerHTML = "";
    if (entries.length === 0) {
      const empty = document.createElement("li");
      empty.innerHTML = "<small>No history yet.</small>";
      list.appendChild(empty);
      return;
    }
    // Most recently seen first.
    const sorted = [...entries].sort((a, b) =>
      (b.last_seen || "").localeCompare(a.last_seen || ""),
    );
    for (const entry of sorted) {
      const li = document.createElement("li");
      const proto = entry.protocol ? ` (${entry.protocol})` : "";
      const seenN = entry.count > 1 ? ` · seen ${entry.count}\u00d7` : "";
      const last = entry.last_seen ? entry.last_seen.replace("T", " ").replace("Z", "Z") : "?";
      li.innerHTML = `<code>${entry.ip}</code><small>${proto}${seenN} · last ${last}</small>`;
      list.appendChild(li);
    }
  }

  function wireHistoryClearButton() {
    const btn = qs("#history-clear");
    if (!btn) return;
    on(btn, "click", () => {
      try {
        window.localStorage.removeItem(HISTORY_KEY);
      } catch {
        /* ignore */
      }
      renderHistory();
    });
  }

  function trackHistory() {
    // Pull current IP / protocol off the page (already rendered server-side).
    const currentEl = qs(".ip-current");
    const dualEl = qs("#dual-stack");
    const currentIp = currentEl ? currentEl.textContent.trim() : null;
    const protocol = dualEl ? dualEl.dataset.protocol || null : null;
    if (currentIp) recordSeen(currentIp, protocol);

    // Also record any other-stack IP discovered by the dual-stack probe.
    // Re-render history when the probe updates the DOM.
    renderHistory();
    document.querySelectorAll("[data-ds]").forEach((el) => {
      const observer = new MutationObserver(() => {
        const stack = el.dataset.ds;
        const ip = el.textContent.trim();
        const proto = stack === "ipv4" ? "IPv4" : stack === "ipv6" ? "IPv6" : null;
        if (recordSeen(ip, proto)) renderHistory();
      });
      observer.observe(el, { childList: true, characterData: true, subtree: true });
    });
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
    trackHistory();
    wireHistoryClearButton();
  });
})();

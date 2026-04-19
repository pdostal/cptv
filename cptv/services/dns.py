from __future__ import annotations

from dataclasses import dataclass

# Well-known public resolvers. Keys are the resolver's anycast IP,
# values the human-readable operator name.
KNOWN_RESOLVERS: dict[str, str] = {
    "1.1.1.1": "Cloudflare",
    "1.0.0.1": "Cloudflare",
    "8.8.8.8": "Google",
    "8.8.4.4": "Google",
    "9.9.9.9": "Quad9",
    "149.112.112.112": "Quad9",
    "208.67.222.222": "OpenDNS",
    "208.67.220.220": "OpenDNS",
    "64.6.64.6": "Verisign",
    "4.2.2.1": "Level3",
    "4.2.2.2": "Level3",
    "185.228.168.9": "CleanBrowsing",
    "76.76.19.19": "Control D",
    "2606:4700:4700::1111": "Cloudflare",
    "2606:4700:4700::1001": "Cloudflare",
    "2001:4860:4860::8888": "Google",
    "2001:4860:4860::8844": "Google",
    "2620:fe::fe": "Quad9",
    "2620:fe::9": "Quad9",
}


@dataclass(frozen=True)
class ResolverInfo:
    resolver_ip: str | None
    resolver_name: str | None
    is_known_public: bool


def classify_resolver(resolver_ip: str | None) -> ResolverInfo:
    """Classify a resolver IP against the well-known public list.

    True server-side detection of the visitor's resolver requires
    a DNS-side probe (unique subdomain → authoritative logs).
    That infrastructure lives outside the web app; this function
    just classifies a resolver IP once you have one.
    """
    if resolver_ip is None:
        return ResolverInfo(resolver_ip=None, resolver_name=None, is_known_public=False)

    name = KNOWN_RESOLVERS.get(resolver_ip)
    return ResolverInfo(
        resolver_ip=resolver_ip,
        resolver_name=name,
        is_known_public=name is not None,
    )

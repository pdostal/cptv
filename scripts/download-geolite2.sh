#!/usr/bin/env bash
# Download MaxMind GeoLite2 City + ASN databases.
# Default output: ~/.local/share/cptv/geolite2/ (override with GEOLITE2_DIR).
#
# Requires: MAXMIND_LICENSE_KEY env var (https://www.maxmind.com/en/geolite2/signup).
# Produces: GeoLite2-City.mmdb, GeoLite2-ASN.mmdb in the output directory.

set -euo pipefail

if [[ -z "${MAXMIND_LICENSE_KEY:-}" ]]; then
  echo "error: MAXMIND_LICENSE_KEY is not set" >&2
  echo "sign up at https://www.maxmind.com/en/geolite2/signup and generate a key" >&2
  exit 1
fi

out_dir="${GEOLITE2_DIR:-${HOME}/.local/share/cptv/geolite2}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

mkdir -p "${out_dir}"

download_edition() {
  local edition="$1"
  local archive="${tmp_dir}/${edition}.tar.gz"
  local url="https://download.maxmind.com/app/geoip_download"
  url+="?edition_id=${edition}&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz"

  echo "fetching ${edition}..."
  curl -fsSL -o "${archive}" "${url}"

  tar -xzf "${archive}" -C "${tmp_dir}"
  local extracted
  extracted="$(find "${tmp_dir}" -maxdepth 2 -name "${edition}.mmdb" | head -n 1)"
  if [[ -z "${extracted}" ]]; then
    echo "error: ${edition}.mmdb not found in archive" >&2
    exit 1
  fi
  mv "${extracted}" "${out_dir}/${edition}.mmdb"
  echo "wrote ${out_dir}/${edition}.mmdb"
}

download_edition "GeoLite2-City"
download_edition "GeoLite2-ASN"

echo "done."

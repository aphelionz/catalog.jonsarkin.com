#!/usr/bin/env bash
# Install the pinned third-party Omeka modules into omeka/volume/modules.
#
#   scripts/fetch-omeka-modules.sh          download, verify SHA-256, replace each module dir
#   scripts/fetch-omeka-modules.sh --check  exit 1 if an installed version differs from the pin
#
# These modules are gitignored. This list is the source of truth for their
# versions on every machine (laptop, prod). Bump a pin here, run the script,
# then upgrade the module in Admin > Modules.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=omeka/volume/modules
# name version sha256 url
PINS="
Common 3.4.94 8bf8c88cea6f727725ded431e8eb24d7bfaac8a8f46beb0740e435080089faf7 https://github.com/Daniel-KM/Omeka-S-module-Common/releases/download/3.4.94/Common-3.4.94.zip
Log 3.4.42 3d9ea3510b5651ced934ef84cb2c49901aa96bb806e805d28ef541c917e5d456 https://github.com/Daniel-KM/Omeka-S-module-Log/releases/download/3.4.42/Log-3.4.42.zip
BulkEdit 3.4.40 43e7c8dad5756afad551355f9789a43d908bb53181d233adb130e3da48f47663 https://github.com/Daniel-KM/Omeka-S-module-BulkEdit/releases/download/3.4.40/BulkEdit-3.4.40.zip
CustomVocab 2.1.0 b365b46ad1e20718bbacebe6884f5e03e93e39a5fdc5eb0bb428570ec0fbec9b https://github.com/omeka-s-modules/CustomVocab/releases/download/v2.1.0/CustomVocab-2.1.0.zip
"

installed_version() {
    sed -n 's/^version *= *"\(.*\)"/\1/p' "$DEST/$1/config/module.ini" 2>/dev/null || true
}

sha256() {
    if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

if [ "${1:-}" = "--check" ]; then
    status=0
    while read -r name version _ _; do
        [ -n "$name" ] || continue
        have=$(installed_version "$name")
        if [ "$have" != "$version" ]; then
            echo "$name: installed '${have:-none}', pinned $version. Run scripts/fetch-omeka-modules.sh" >&2
            status=1
        fi
    done <<< "$PINS"
    exit $status
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/omeka-modules.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
while read -r name version sum url; do
    [ -n "$name" ] || continue
    curl -fsSL -o "$tmp/$name.zip" "$url"
    if [ "$(sha256 "$tmp/$name.zip")" != "$sum" ]; then
        echo "$name $version: checksum mismatch, not installed" >&2
        exit 1
    fi
    unzip -q -o "$tmp/$name.zip" -d "$tmp/$name" < /dev/null
    rm -rf "${DEST:?}/$name"
    mv "$tmp/$name/$name" "$DEST/$name"
    echo "$name $version installed"
done <<< "$PINS"

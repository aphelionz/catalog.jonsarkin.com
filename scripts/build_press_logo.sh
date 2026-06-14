#!/usr/bin/env bash
# Build a square 600x600 dark logo tile for the Press wall (PBS-tile recipe).
#
#   build_press_logo.sh img  <slug> <image-path-or-url> [invert]
#       Composite a logo image (PNG/SVG) centered on a dark canvas.
#       Pass "invert" to negate a dark-on-transparent logo to light.
#
#   build_press_logo.sh text <slug> "Outlet Name" [accent-hex]
#       Typographic wordmark tile for outlets with no cleanly sourceable logo.
#
# Output: omeka/volume/themes/sarkin-jeppesen/asset/img/press-logos/<slug>.png
set -euo pipefail

BG='#06080e'
DEST="$HOME/Projects/catalog.jonsarkin.com/omeka/volume/themes/sarkin-jeppesen/asset/img/press-logos"
FONT="$(ls /System/Library/Fonts/Helvetica.ttc /System/Library/Fonts/Supplemental/Georgia.ttf 2>/dev/null | head -1)"
mkdir -p "$DEST"

mode="${1:?img|text}"; slug="${2:?slug}"; out="$DEST/$slug.png"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

case "$mode" in
  img)
    src="${3:?image path/url}"; invert="${4:-}"
    f="$tmp/src"
    case "$src" in
      http*) curl -sL -o "$f" "$src" ;;
      *)     cp "$src" "$f" ;;
    esac
    # SVG -> PNG via rsvg if needed
    if head -c 256 "$f" | grep -qi '<svg'; then rsvg-convert -h 480 "$f" -o "$tmp/r.png"; else cp "$f" "$tmp/r.png"; fi
    args=(-trim +repage -resize 480x400)
    [ "$invert" = "invert" ] && args+=(-channel RGB -negate +channel)
    magick "$tmp/r.png" "${args[@]}" -background none -gravity center -extent 540x540 \
           -background "$BG" -gravity center -extent 600x600 "$out"
    ;;
  text)
    name="${3:?outlet name}"; accent="${4:-#e8e8ea}"
    magick -size 480x480 -background "$BG" -fill "$accent" -font "$FONT" \
           -gravity center caption:"$name" \
           -background "$BG" -gravity center -extent 600x600 "$out"
    ;;
  *) echo "usage: $0 img|text <slug> ..." >&2; exit 1 ;;
esac

echo "built $out"

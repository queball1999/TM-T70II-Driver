#!/usr/bin/env bash
# Build the .deb inside the WSL/Linux native filesystem (never on /mnt/c, where
# DrvFs loses exec bits and is slow), then copy the artefacts back next to the
# source tree.
#
#   ./tools/build.sh              # build
#   ./tools/build.sh lintian      # build + lintian
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PKG=epson-tmt70ii-escpos
BUILDROOT="${BUILDROOT:-$HOME/build/$PKG}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1"; MISSING=1; }; }
MISSING=0
for c in dpkg-buildpackage dh fakeroot rsync python3; do need "$c"; done
if [ "$MISSING" = 1 ]; then
    echo
    echo "Install build deps:"
    echo "  sudo apt install build-essential debhelper dpkg-dev fakeroot rsync python3 lintian"
    exit 1
fi

echo "==> sync $SRC -> $BUILDROOT"
mkdir -p "$BUILDROOT"
rsync -a --delete \
      --exclude '.git/' --exclude 'build/' --exclude 'capture/' \
      --exclude '*.deb' --exclude 'debian/epson-tmt70ii-escpos/' \
      --exclude 'debian/.debhelper/' --exclude '__pycache__/' \
      "$SRC"/ "$BUILDROOT"/

# /mnt/c (DrvFs) presents every file as 0777; debhelper then tries to *execute*
# debian/*.docs, *.install, ... as "executable config files".  Normalise.
find "$BUILDROOT/debian" -maxdepth 1 -type f -exec chmod 0644 {} +
chmod 0755 "$BUILDROOT/src/filter/rastertoescpos" "$BUILDROOT/src/backend/tmt70"
chmod 0755 "$BUILDROOT/debian/rules" \
           "$BUILDROOT/debian/postinst" "$BUILDROOT/debian/postrm"
find "$BUILDROOT/tools" -name '*.sh' -exec chmod 0755 {} + 2>/dev/null || true

echo "==> dpkg-buildpackage"
( cd "$BUILDROOT" && dpkg-buildpackage -us -uc -b )

OUT="$(dirname "$BUILDROOT")"
echo "==> artefacts:"
ls -1 "$OUT"/${PKG}_*.deb "$OUT"/${PKG}_*.changes 2>/dev/null || true
cp -v "$OUT"/${PKG}_*.deb "$SRC"/ 2>/dev/null || true

if [ "${1:-}" = "lintian" ]; then
    echo "==> lintian"
    lintian -i "$OUT"/${PKG}_*.changes || true
fi

echo
echo "Done.  Install with:  sudo apt install $SRC/${PKG}_1.0.0_all.deb"

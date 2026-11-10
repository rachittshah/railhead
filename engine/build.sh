#!/usr/bin/env bash
# Build Railhead's engine: OpenTTD (pinned tag) + engine/patches/*.patch, dedicated-only, plus OpenGFX.
#
#   engine/build.sh                 # -> .cache/openttd-src/build/openttd  (railhead finds it automatically)
#   PREFIX=/opt/railhead/engine engine/build.sh   # also install binary + data into PREFIX (Docker)
#
# Optional, off by default (the default binary is what sealed tasks pin by sha256; any change to it needs a
# re-seal). Build a variant into its own tree so the default engine stays untouched:
#   RAILHEAD_ENGINE_SRC=$PWD/.cache/openttd-fast CXX_FLAGS="-mcpu=apple-m4 -flto=thin" \
#     EXTRA_PATCHES=engine/optional/0004-stdin-poll.patch engine/build.sh
#   RAILHEAD_OPENTTD=$PWD/.cache/openttd-fast/build/openttd railhead ...
#
# Why patched: stock dedicated servers cannot fast-forward and cap GameScript->admin JSON at
# ~1.4 KB without a size guard. See engine/README.md and docs/research/03-openttd-source.md.
set -euo pipefail

OPENTTD_VERSION="${OPENTTD_VERSION:-15.3}"
OPENGFX_VERSION="${OPENGFX_VERSION:-8.0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${RAILHEAD_ENGINE_SRC:-$ROOT/.cache/openttd-src}"
BUILD="$SRC/build"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
EXTRA_PATCHES="${EXTRA_PATCHES:-}"  # space-separated patch files applied after engine/patches/*.patch
CXX_FLAGS="${CXX_FLAGS:-}"          # compiler and linker flags, e.g. "-mcpu=apple-m4 -flto=thin"

if [ ! -d "$SRC/.git" ]; then
  git clone --quiet --depth 1 --branch "$OPENTTD_VERSION" https://github.com/OpenTTD/OpenTTD.git "$SRC"
fi
# Start from a pristine tree, then apply every Railhead patch in order.
git -C "$SRC" checkout --quiet -- .
for p in "$ROOT"/engine/patches/*.patch $EXTRA_PATCHES; do
  case "$p" in /*) ;; *) p="$ROOT/$p" ;; esac  # EXTRA_PATCHES: absolute, or relative to the repository root
  git -C "$SRC" apply --whitespace=nowarn "$p"
  echo "applied $(basename "$p")"
done

CMAKE_EXTRA=()
if [ -n "$CXX_FLAGS" ]; then
  CMAKE_EXTRA+=("-DCMAKE_CXX_FLAGS=$CXX_FLAGS" "-DCMAKE_EXE_LINKER_FLAGS=$CXX_FLAGS")
fi
cmake -S "$SRC" -B "$BUILD" -G Ninja -DOPTION_DEDICATED=ON -DCMAKE_BUILD_TYPE=Release ${CMAKE_EXTRA[@]+"${CMAKE_EXTRA[@]}"} >/dev/null
ninja -C "$BUILD" -j "$JOBS" openttd

# Base graphics: a dedicated server still needs a base set present.
if ! ls "$BUILD"/baseset/*[Oo]pen[Gg][Ff][Xx]*.tar >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/ogfx.zip" "https://cdn.openttd.org/opengfx-releases/${OPENGFX_VERSION}/opengfx-${OPENGFX_VERSION}-all.zip"
  (cd "$tmp" && unzip -q ogfx.zip)
  cp "$tmp"/opengfx-*.tar "$BUILD/baseset/"
  rm -rf "$tmp"
fi

if [ -n "${PREFIX:-}" ]; then
  mkdir -p "$PREFIX"
  cp "$BUILD/openttd" "$PREFIX/"
  for d in lang baseset ai game; do rm -rf "${PREFIX:?}/$d"; cp -R "$BUILD/$d" "$PREFIX/$d"; done
  echo "installed engine into $PREFIX"
fi
echo "engine ready: $BUILD/openttd ($("$BUILD/openttd" -h 2>&1 | head -1 || true))"

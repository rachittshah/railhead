#!/bin/sh
# Installs the pinned Bend 2 release that bend/exchange is written and checked against.
#
# The upstream installer (bend-lang.com/install.sh) installs whatever version is current; this script pins
# one release and checks the archive's sha256 itself, so CI and every developer build the same compiler.
# It writes only under $BEND_HOME (default ~/.bend): bin/bend, bend2/ and guide/, the upstream layout.
# With --lean it also installs the Lean toolchain that `bend PROOF.bend --verdict` needs (via elan).
#
# Usage: sh bend/install-bend.sh [--lean]
set -eu

VER="2.0.40"
LEAN="leanprover/lean4:v4.34.0"   # bend2/safe.ts LEAN_VERSION for this release
SHA_DARWIN_ARM64="b2be003b041eb6cfc65326be18f79eb2e0bae2189be79a18c507e661b4b6900b"
SHA_DARWIN_X64="9229c8bdd0a93f63fa45cc5af5e9e56389ebb620d655920915daf966c6ca561c"
SHA_LINUX_ARM64="2f42a6fa34ecdaa71ae105e5fa734f9a07e129d333e45448ab6d2ff03a837570"
SHA_LINUX_X64="c9ff8d67bd903fb6ad9a26b43d8a62c01021de82a44d55ce4a3dcd4732113d6b"

case $(uname -s)-$(uname -m) in
  Darwin-arm64) os=darwin arch=arm64 sum=$SHA_DARWIN_ARM64;;
  Darwin-x86_64) os=darwin arch=x64 sum=$SHA_DARWIN_X64;;
  Linux-aarch64|Linux-arm64) os=linux arch=arm64 sum=$SHA_LINUX_ARM64;;
  Linux-x86_64) os=linux arch=x64 sum=$SHA_LINUX_X64;;
  *) echo "install-bend: unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1;;
esac

home=${BEND_HOME:-$HOME/.bend}
name="bend-$VER-$os-$arch.tar.gz"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL -o "$tmp/$name" "https://github.com/bendlang/bend/releases/download/v$VER/$name"
if command -v sha256sum >/dev/null 2>&1; then
  echo "$sum  $tmp/$name" | sha256sum -c - >/dev/null
else
  echo "$sum  $tmp/$name" | shasum -a 256 -c - >/dev/null
fi
tar -xzf "$tmp/$name" -C "$tmp"
mkdir -p "$home/bin"
rm -rf "$home/bend2" "$home/guide"
mv "$tmp/bend/bend2" "$tmp/bend/guide" "$home/"
mv -f "$tmp/bend/bin/bend" "$home/bin/bend"
echo "installed bend $VER at $home/bin/bend"

if [ "${1:-}" = "--lean" ]; then
  elan=$(command -v elan 2>/dev/null || true)
  [ -n "$elan" ] || elan="${ELAN_HOME:-$HOME/.elan}/bin/elan"
  if [ ! -x "$elan" ]; then
    curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
      | sh -s -- -y --default-toolchain none --no-modify-path
  fi
  "$elan" toolchain install "$LEAN"
  echo "installed $LEAN for bend --verdict"
fi

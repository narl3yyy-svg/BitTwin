#!/usr/bin/env bash
# Build a release archive and its SHA-256 checksum on your own machine.
#
#   tools/make-release.sh          -> dist/BitTwin-<version>.tar.gz + dist/SHA256SUMS
#
# Upload both files to a GitHub release yourself, so the release is published
# by you. Users verify their download with:  sha256sum -c SHA256SUMS
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
VERSION="$(python3 -c 'import bittwin; print(bittwin.__version__)')"
NAME="BitTwin-${VERSION}"

if [ -n "$(git status --porcelain)" ]; then
    echo "Commit or stash your changes first - a release must match a commit exactly." >&2
    exit 1
fi

echo "Running the test suite..."
python3 -m unittest discover -s tests >/dev/null 2>&1 || { echo "Tests failed - no release built." >&2; exit 1; }

mkdir -p dist
git archive --format=tar.gz --prefix="${NAME}/" -o "dist/${NAME}.tar.gz" HEAD
( cd dist && sha256sum "${NAME}.tar.gz" > SHA256SUMS )

echo "Built dist/${NAME}.tar.gz from commit $(git rev-parse --short HEAD)"
cat dist/SHA256SUMS

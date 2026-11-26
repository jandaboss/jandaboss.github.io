#!/usr/bin/env bash
# Baut label-converter.zip aus dem Ordner extension/.
# Im ZIP liegt alles im Ordner "label-converter", den man in Chrome lädt.
set -euo pipefail
cd "$(dirname "$0")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -r extension "$tmp/label-converter"
find "$tmp" -name '.DS_Store' -delete
rm -f label-converter.zip
(cd "$tmp" && zip -qr -X -9 "$OLDPWD/label-converter.zip" label-converter)
echo "label-converter.zip: $(du -h label-converter.zip | cut -f1), Version $(grep -o '"version": *"[^"]*"' extension/manifest.json | cut -d'"' -f4)"

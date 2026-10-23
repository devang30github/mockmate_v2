#!/usr/bin/env bash
# Downloads a sherpa-onnx streaming English model into <repo>/models/.
# Usage: bash scripts/fetch_stream_model.sh [model-name]
set -euo pipefail
NAME="${1:-sherpa-onnx-streaming-zipformer-en-2023-06-21}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/models"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${NAME}.tar.bz2"
mkdir -p "$DEST"
if [ -f "$DEST/$NAME/tokens.txt" ]; then
  echo "already present: $DEST/$NAME"
  exit 0
fi
echo "downloading $URL"
curl -fSL "$URL" -o "$DEST/$NAME.tar.bz2"
tar -xjf "$DEST/$NAME.tar.bz2" -C "$DEST"
rm "$DEST/$NAME.tar.bz2"
ls -lh "$DEST/$NAME"
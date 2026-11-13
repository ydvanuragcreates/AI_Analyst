#!/bin/bash
# Push the backend to a Hugging Face Space (Gradio SDK, free CPU tier).
#
# Usage (from the repo root, in Git Bash on Windows):
#   HF_TOKEN=hf_xxx ./deploy/huggingface/deploy.sh <hf-username>/<space-name>
#
# HF_TOKEN: a Hugging Face access token with "write" permission
# (huggingface.co/settings/tokens). Used only for this push; never saved.
#
# The Space repo gets ONLY what it needs (backend app code + this folder's
# app.py/packages.txt/README.md) -- not the frontend, tests, .env, or storage.
set -euo pipefail

SPACE="${1:?usage: HF_TOKEN=... $0 <hf-username>/<space-name>}"
: "${HF_TOKEN:?set HF_TOKEN to a Hugging Face write token}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

cp -r "$ROOT/backend/app" "$ROOT/backend/alembic" "$ROOT/backend/alembic.ini" "$ROOT/backend/requirements.txt" "$BUILD/"
cp "$ROOT/deploy/huggingface/app.py" "$ROOT/deploy/huggingface/packages.txt" "$ROOT/deploy/huggingface/README.md" "$BUILD/"
find "$BUILD" -name '__pycache__' -type d -prune -exec rm -rf {} +

cd "$BUILD"
git init -q -b main
git add -A
git -c user.name="deploy" -c user.email="deploy@localhost" commit -q -m "Deploy $(git -C "$ROOT" rev-parse --short HEAD)"
git push -q --force "https://user:${HF_TOKEN}@huggingface.co/spaces/${SPACE}" main
echo "Pushed. Watch the build: https://huggingface.co/spaces/${SPACE} (Logs tab)"

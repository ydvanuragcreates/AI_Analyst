#!/bin/bash
# Push the backend to a Hugging Face Space (Gradio SDK, free CPU tier).
#
# Usage (from the repo root, in Git Bash on Windows):
#   ./deploy/huggingface/deploy.sh <hf-username>/<space-name>
#
# Git asks for credentials (on Windows, a login popup): username = your
# Hugging Face username, password = an access token with "write" permission
# (huggingface.co/settings/tokens). Git Credential Manager remembers it for
# later deploys. Optionally set HF_TOKEN instead to skip the prompt.
#
# The Space repo gets ONLY what it needs (backend app code + this folder's
# app.py/packages.txt/README.md) -- not the frontend, tests, .env, or storage.
set -euo pipefail

SPACE="${1:?usage: $0 <hf-username>/<space-name>}"

# Token from a file (easiest on Windows: paste it into Notepad, save as
# hf_token.txt in your home folder -- never inside this repo).
TOKEN_FILE="${HF_TOKEN_FILE:-$HOME/hf_token.txt}"
if [[ -z "${HF_TOKEN:-}" && -f "$TOKEN_FILE" ]]; then
  HF_TOKEN="$(grep -o 'hf_[A-Za-z0-9]*' "$TOKEN_FILE" | head -1 || true)"
  [[ -n "$HF_TOKEN" ]] || { echo "No token starting with hf_ found in $TOKEN_FILE" >&2; exit 1; }
fi

# Check the token before pushing, so a bad one gives a clear message.
if [[ -n "${HF_TOKEN:-}" ]]; then
  WHOAMI="$(curl -s -H "Authorization: Bearer ${HF_TOKEN}" https://huggingface.co/api/whoami-v2)"
  NAME="$(printf '%s' "$WHOAMI" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4 || true)"
  ROLE="$(printf '%s' "$WHOAMI" | grep -o '"role":"[^"]*"' | head -1 | cut -d'"' -f4 || true)"
  if [[ -z "$NAME" ]]; then
    echo "Hugging Face rejected this token (invalid, deleted, or copied incompletely). Create a new one." >&2; exit 1
  fi
  echo "Token OK: account '$NAME', type '${ROLE:-unknown}'"
  if [[ "$NAME" != "${SPACE%%/*}" ]]; then
    echo "This token belongs to '$NAME', but the Space is under '${SPACE%%/*}'." >&2; exit 1
  fi
  if [[ "$ROLE" == "read" ]]; then
    echo "This is a READ token; it can't push. Create one with token type 'Write'." >&2; exit 1
  fi
fi

if [[ -n "${HF_TOKEN:-}" ]]; then
  REMOTE="https://user:${HF_TOKEN}@huggingface.co/spaces/${SPACE}"
else
  REMOTE="https://huggingface.co/spaces/${SPACE}"
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

cp -r "$ROOT/backend/app" "$ROOT/backend/alembic" "$ROOT/backend/alembic.ini" "$ROOT/backend/requirements.txt" "$BUILD/"
cp "$ROOT/deploy/huggingface/app.py" "$ROOT/deploy/huggingface/packages.txt" "$ROOT/deploy/huggingface/README.md" "$BUILD/"
find "$BUILD" -name '__pycache__' -type d -prune -exec rm -rf {} +

cd "$BUILD"
git init -q -b main
git config core.autocrlf false
git add -A
git -c user.name="deploy" -c user.email="deploy@localhost" commit -q -m "Deploy $(git -C "$ROOT" rev-parse --short HEAD)"
git push -q --force "$REMOTE" main
echo "Pushed. Watch the build: https://huggingface.co/spaces/${SPACE} (Logs tab)"

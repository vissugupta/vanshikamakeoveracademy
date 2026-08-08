#!/bin/bash
set -e

echo "==> Installing Python dependencies..."
uv pip install --python .pythonlibs/bin/python3 -r salon-app/requirements.txt -q

echo "==> Installing Node dependencies..."
pnpm install --frozen-lockfile

echo "==> Post-merge setup complete."

#!/bin/bash
set -e

echo "==> Installing Python dependencies..."
uv pip install --python .pythonlibs/bin/python3 -r salon-app/requirements.txt -q

echo "==> Installing Node dependencies..."
pnpm install --frozen-lockfile

echo "==> Installing Python desktop dependencies..."
uv pip install --python .pythonlibs/bin/python3 -r desktop/requirements.txt -q

echo "==> Post-merge setup complete."

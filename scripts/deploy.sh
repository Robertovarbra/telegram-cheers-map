#!/bin/bash
set -e

echo "=== Deploy: telegram-cheers-map ==="

export PATH="$HOME/.local/bin:$PATH"
REPO_DIR="$HOME/telegram-cheers-map"

cd "$REPO_DIR"

echo "[1/4] Actualizando código..."
git fetch origin main
git reset --hard origin/main

echo "[2/4] Instalando dependencias..."
uv sync --frozen --no-dev

echo "[3/4] Ejecutando lint..."
uv run ruff check .

echo "[4/4] Reiniciando servicio..."
sudo systemctl restart cheers-bot

echo "=== Deploy completado ==="

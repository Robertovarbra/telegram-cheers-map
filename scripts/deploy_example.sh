#!/bin/bash
set -e
export PATH="$HOME/.local/bin:$PATH"
cd ~/telegram-cheers-map
git pull origin main
uv sync
sudo systemctl restart cheers-bot

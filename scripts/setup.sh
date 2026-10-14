#!/usr/bin/env sh
set -eu

echo "Setting up..."
pip install -r requirements.txt --break-system-packages 2>/dev/null || pip install -r requirements.txt

cat << EOF > deploy.sh
#!/bin/bash

set -e

echo "=== HR-Bot Deployment Script ==="

REMOTE_USER="marcellis"
REMOTE_HOST="93.189.147.71"
REMOTE_BASE_DIR="/home/marcellis"

echo "--> 1. Cleaning up old build artifacts..."
rm -f source.tar.gz packages.tar.gz
rm -rf offline_packages
mkdir offline_packages

echo "--> 2. Packaging ONLY source code..."
tar \
  --exclude='.git' \
  --exclude='.idea' \
  --exclude='*.pyc' \
  --exclude='__pycache__' \
  --exclude='.DS_Store' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='deploy.sh' \
  --exclude='*.sqlite*' \
  --exclude='*.pkl' \
  --exclude='*.pid' \
  --exclude='*.txt' \
  --exclude='logs' \
  --exclude='offline_packages' \
  --exclude='*.tar.gz' \
  -czvf source.tar.gz .

echo "[OK] Source code packaged."

echo "--> 3. Downloading Linux (x86_64) packages..."
pip download \
  -r requirements.txt \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --implementation cp \
  --abi cp311 \
  --only-binary=:all: \
  -d ./offline_packages

echo "[OK] Linux packages downloaded."

echo "--> 4. Packaging dependencies..."
tar -czvf packages.tar.gz -C offline_packages .

echo "[OK] Dependencies packaged."

echo ""
echo "================================================="
echo "✅ Build complete! Run these commands to upload:"
echo ""
echo "scp source.tar.gz ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_BASE_DIR}/"
echo "scp packages.tar.gz ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_BASE_DIR}/"
echo "================================================="
EOF
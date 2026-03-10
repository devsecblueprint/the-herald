#!/bin/bash
set -e

# DEPRECATED: This script has been replaced by tasks.py in the project root
# Please use: python tasks.py build-layer
# This script is kept for reference only

echo "⚠️  WARNING: This script is deprecated!"
echo "Please use the new Python-based build system instead:"
echo "  python tasks.py build-layer"
echo ""
read -p "Continue anyway? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi
echo ""

# AWS Lambda Layer Build Script
# This script packages Python dependencies for AWS Lambda deployment
# Excludes: FastAPI, uvicorn, APScheduler, Google Calendar APIs, Redis, discord.py

echo "Building AWS Lambda layer for Python dependencies..."

# Configuration
LAYER_DIR="lambda_layer"
PYTHON_DIR="${LAYER_DIR}/python"
ZIP_FILE="lambda_layer.zip"

# Clean up previous builds
echo "Cleaning up previous builds..."
rm -rf "${LAYER_DIR}"
rm -f "${ZIP_FILE}"

# Create directory structure required by AWS Lambda
echo "Creating Lambda layer directory structure..."
mkdir -p "${PYTHON_DIR}"

# Create filtered requirements file (exclude unwanted dependencies)
echo "Creating filtered requirements file..."
TEMP_REQUIREMENTS=$(mktemp)

# Dependencies to exclude (not needed in Lambda environment)
EXCLUDED_DEPS=(
    "fastapi"
    "uvicorn"
    "apscheduler"
    "google-api-python-client"
    "google-auth"
    "google-auth-httplib2"
    "google-auth-oauthlib"
    "redis"
    "discord.py"
    "black"
    "pylint"
    "isort"
    "mypy_extensions"
    "astroid"
    "mccabe"
    "dill"
    "tomlkit"
    "pathspec"
    "click"
)

# Filter requirements.txt to exclude unwanted dependencies
grep -v -i -E "$(IFS='|'; echo "${EXCLUDED_DEPS[*]}")" requirements.txt > "${TEMP_REQUIREMENTS}"

echo "Installing dependencies into python/ directory..."
pip install \
    --target "${PYTHON_DIR}" \
    --requirement "${TEMP_REQUIREMENTS}" \
    --upgrade \
    --no-cache-dir \
    --platform manylinux2014_x86_64 \
    --only-binary=:all: \
    --python-version 3.11

# Clean up temporary requirements file
rm "${TEMP_REQUIREMENTS}"

# Remove unnecessary files to reduce layer size
echo "Removing unnecessary files to reduce layer size..."
find "${PYTHON_DIR}" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DIR}" -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true
find "${PYTHON_DIR}" -type f -name "*.pyo" -delete 2>/dev/null || true
find "${PYTHON_DIR}" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DIR}" -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# Create ZIP file for Lambda layer
echo "Creating ZIP file for Lambda layer deployment..."
cd "${LAYER_DIR}"
zip -r "../${ZIP_FILE}" python/ -q
cd ..

# Display results
LAYER_SIZE=$(du -sh "${LAYER_DIR}" | cut -f1)
ZIP_SIZE=$(du -sh "${ZIP_FILE}" | cut -f1)

echo ""
echo "✓ Lambda layer build complete!"
echo "  Layer directory: ${LAYER_DIR}/ (${LAYER_SIZE})"
echo "  ZIP file: ${ZIP_FILE} (${ZIP_SIZE})"
echo ""
echo "To deploy the layer to AWS Lambda:"
echo "  aws lambda publish-layer-version \\"
echo "    --layer-name the-herald-dependencies \\"
echo "    --zip-file fileb://${ZIP_FILE} \\"
echo "    --compatible-runtimes python3.11 \\"
echo "    --description 'Python dependencies for Discord bot Lambda function'"

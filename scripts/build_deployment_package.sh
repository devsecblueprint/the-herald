#!/bin/bash
set -e

# DEPRECATED: This script has been replaced by tasks.py in the project root
# Please use: python tasks.py build-package
# This script is kept for reference only

echo "⚠️  WARNING: This script is deprecated!"
echo "Please use the new Python-based build system instead:"
echo "  python tasks.py build-package"
echo ""
read -p "Continue anyway? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi
echo ""

# AWS Lambda Deployment Package Build Script
# This script packages the application code for AWS Lambda deployment
# Includes: app/ directory, lambda_handler.py, config.yaml
# Excludes: test files, __pycache__, .pyc files

echo "Building AWS Lambda deployment package..."

# Configuration
PACKAGE_DIR="lambda_deployment_package"
ZIP_FILE="lambda_deployment_package.zip"

# Clean up previous builds
echo "Cleaning up previous builds..."
rm -rf "${PACKAGE_DIR}"
rm -f "${ZIP_FILE}"

# Create deployment package directory
echo "Creating deployment package directory..."
mkdir -p "${PACKAGE_DIR}"

# Copy application code
echo "Copying application code..."
cp -r app/ "${PACKAGE_DIR}/"

# Copy Lambda handler entry point
echo "Copying Lambda handler..."
cp lambda_handler.py "${PACKAGE_DIR}/"

# Ensure config.yaml is included
if [ -f "app/static/config.yaml" ]; then
    echo "Config file found and included in package"
else
    echo "Warning: app/static/config.yaml not found"
fi

# Remove test files and unnecessary artifacts
echo "Removing test files and unnecessary artifacts..."
find "${PACKAGE_DIR}" -type f -name "test_*.py" -delete 2>/dev/null || true
find "${PACKAGE_DIR}" -type f -name "*_test.py" -delete 2>/dev/null || true
find "${PACKAGE_DIR}" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find "${PACKAGE_DIR}" -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
find "${PACKAGE_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${PACKAGE_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true
find "${PACKAGE_DIR}" -type f -name "*.pyo" -delete 2>/dev/null || true
find "${PACKAGE_DIR}" -type f -name ".DS_Store" -delete 2>/dev/null || true

# Create ZIP file for Lambda deployment
echo "Creating ZIP file for Lambda deployment..."
cd "${PACKAGE_DIR}"
zip -r "../${ZIP_FILE}" . -q
cd ..

# Display results
PACKAGE_SIZE=$(du -sh "${PACKAGE_DIR}" | cut -f1)
ZIP_SIZE=$(du -sh "${ZIP_FILE}" | cut -f1)
ZIP_SIZE_BYTES=$(stat -f%z "${ZIP_FILE}" 2>/dev/null || stat -c%s "${ZIP_FILE}" 2>/dev/null)
ZIP_SIZE_MB=$((ZIP_SIZE_BYTES / 1024 / 1024))

echo ""
echo "✓ Lambda deployment package build complete!"
echo "  Package directory: ${PACKAGE_DIR}/ (${PACKAGE_SIZE})"
echo "  ZIP file: ${ZIP_FILE} (${ZIP_SIZE}, ${ZIP_SIZE_MB}MB)"

# Check if package is under 50MB limit
if [ ${ZIP_SIZE_MB} -ge 50 ]; then
    echo ""
    echo "⚠ Warning: Deployment package is ${ZIP_SIZE_MB}MB (limit is 50MB)"
    echo "  Consider moving more dependencies to the Lambda layer"
else
    echo "  ✓ Package size is within 50MB limit"
fi

echo ""
echo "To deploy the package to AWS Lambda:"
echo "  aws lambda update-function-code \\"
echo "    --function-name the-herald-handler \\"
echo "    --zip-file fileb://${ZIP_FILE}"
echo ""
echo "Or use Terraform to deploy:"
echo "  cd terraform"
echo "  terraform apply"


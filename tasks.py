"""
Build tasks for AWS Lambda deployment package and layer.
Run with: invoke <task_name>
"""

import shutil
import zipfile
from pathlib import Path

from invoke import task


def clean_pycache(directory: Path) -> None:
    """Remove __pycache__ directories and .pyc files."""
    for item in directory.rglob("__pycache__"):
        shutil.rmtree(item, ignore_errors=True)
    for item in directory.rglob("*.pyc"):
        item.unlink(missing_ok=True)
    for item in directory.rglob("*.pyo"):
        item.unlink(missing_ok=True)


@task
def build_layer(c):
    """Build Lambda layer with Python dependencies."""
    print("Building AWS Lambda layer for Python dependencies...")

    # Configuration
    layer_dir = Path("terraform/lambda_layer")
    python_dir = layer_dir / "python"
    zip_file = Path("terraform/lambda_layer.zip")

    # Clean up previous builds
    print("Cleaning up previous builds...")
    if layer_dir.exists():
        shutil.rmtree(layer_dir)
    if zip_file.exists():
        zip_file.unlink()

    # Create directory structure
    print("Creating Lambda layer directory structure...")
    python_dir.mkdir(parents=True, exist_ok=True)

    # Install dependencies from lambda-requirements.txt
    print("Installing dependencies into python/ directory...")
    c.run(
        f"pip install "
        f"--target {python_dir} "
        f"--requirement lambda-requirements.txt "
        f"--upgrade "
        f"--no-cache-dir"
    )

    # Remove unnecessary files
    print("Removing unnecessary files to reduce layer size...")
    for pattern in ["tests", "test"]:
        for item in python_dir.rglob(pattern):
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)

    clean_pycache(python_dir)

    for pattern in ["*.dist-info", "*.egg-info"]:
        for item in python_dir.rglob(pattern):
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)

    # Create ZIP file
    print("Creating ZIP file for Lambda layer deployment...")
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in python_dir.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(layer_dir)
                zf.write(file, arcname)

    # Display results
    layer_size = sum(f.stat().st_size for f in layer_dir.rglob("*") if f.is_file())
    zip_size = zip_file.stat().st_size

    print()
    print("✓ Lambda layer build complete!")
    print(f"  Layer directory: {layer_dir}/ ({layer_size / 1024 / 1024:.1f}MB)")
    print(f"  ZIP file: {zip_file} ({zip_size / 1024 / 1024:.1f}MB)")
    print()


@task
def build_package(c):
    """Build Lambda deployment package with application code."""
    print("Building AWS Lambda deployment package...")

    # Configuration
    package_dir = Path("terraform/lambda_deployment_package")
    zip_file = Path("terraform/lambda_deployment_package.zip")

    # Clean up previous builds
    print("Cleaning up previous builds...")
    if package_dir.exists():
        shutil.rmtree(package_dir)
    if zip_file.exists():
        zip_file.unlink()

    # Create deployment package directory
    print("Creating deployment package directory...")
    package_dir.mkdir(parents=True, exist_ok=True)

    # Copy application code from lambda/app
    print("Copying application code...")
    shutil.copytree("lambda/app", package_dir / "app", dirs_exist_ok=True)

    # Copy Lambda handler from lambda/lambda_handler.py
    print("Copying Lambda handler...")
    shutil.copy("lambda/lambda_handler.py", package_dir / "lambda_handler.py")

    # Verify config.yaml exists
    config_file = package_dir / "app" / "static" / "config.yaml"
    if config_file.exists():
        print("Config file found and included in package")
    else:
        print("Warning: app/static/config.yaml not found")

    # Remove test files and unnecessary artifacts
    print("Removing test files and unnecessary artifacts...")
    for pattern in ["test_*.py", "*_test.py"]:
        for item in package_dir.rglob(pattern):
            item.unlink(missing_ok=True)

    for pattern in ["tests", "test"]:
        for item in package_dir.rglob(pattern):
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)

    clean_pycache(package_dir)

    for item in package_dir.rglob(".DS_Store"):
        item.unlink(missing_ok=True)

    # Create ZIP file
    print("Creating ZIP file for Lambda deployment...")
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in package_dir.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(package_dir)
                zf.write(file, arcname)

    # Display results
    package_size = sum(f.stat().st_size for f in package_dir.rglob("*") if f.is_file())
    zip_size = zip_file.stat().st_size
    zip_size_mb = zip_size / 1024 / 1024

    print()
    print("✓ Lambda deployment package build complete!")
    print(f"  Package directory: {package_dir}/ ({package_size / 1024:.1f}KB)")
    print(f"  ZIP file: {zip_file} ({zip_size / 1024:.1f}KB, {zip_size_mb:.1f}MB)")

    # Check size limit
    if zip_size_mb >= 50:
        print()
        print(f"⚠ Warning: Deployment package is {zip_size_mb:.1f}MB (limit is 50MB)")
        print("  Consider moving more dependencies to the Lambda layer")
    else:
        print("  ✓ Package size is within 50MB limit")

    print()
    print("Deployment artifacts ready in terraform/ directory for Terraform Cloud")
    print()


@task(pre=[build_layer, build_package])
def build_all(c):
    """Build both layer and deployment package."""
    pass


@task(pre=[build_all])
def apply(c):
    """Run terraform apply to deploy infrastructure."""
    print("Running terraform apply...")
    with c.cd("terraform"):
        c.run("terraform apply --auto-approve")


@task
def clean(c):
    """Clean all build artifacts."""
    print("Cleaning build artifacts...")

    artifacts = [
        Path("terraform/lambda_layer"),
        Path("terraform/lambda_layer.zip"),
        Path("terraform/lambda_deployment_package"),
        Path("terraform/lambda_deployment_package.zip"),
        Path("lambda_deployment_package"),
        Path("lambda_deployment_package.zip"),
        Path("lambda_layer"),
        Path("lambda_layer.zip"),
    ]

    for artifact in artifacts:
        if artifact.exists():
            if artifact.is_dir():
                shutil.rmtree(artifact)
                print(f"  Removed {artifact}/")
            else:
                artifact.unlink()
                print(f"  Removed {artifact}")

    print("✓ Clean complete!")

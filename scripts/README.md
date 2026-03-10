# Build Scripts (Deprecated)

**Note**: The bash scripts in this directory have been replaced by `tasks.py` in the project root. Please use the Python-based build system instead.

## Migration to tasks.py

All build operations are now managed through `tasks.py` in the project root:

```bash
# Build Lambda layer with Python dependencies
python tasks.py build-layer

# Build Lambda deployment package
python tasks.py build-package

# Build both layer and deployment package
python tasks.py build-all

# Clean all build artifacts
python tasks.py clean
```

### Benefits of tasks.py

- **Cross-platform**: Works on macOS, Linux, and Windows
- **Consistent output**: Places artifacts in `terraform/` directory for Terraform Cloud
- **Better error handling**: Python-based with proper exception handling
- **Easier maintenance**: Single Python file instead of multiple bash scripts
- **No shell dependencies**: Uses Python standard library and pip

### Output Location

Build artifacts are now placed in the `terraform/` directory:
- `terraform/lambda_layer.zip` - Lambda layer with Python dependencies
- `terraform/lambda_deployment_package.zip` - Lambda deployment package with application code

This makes it easier to use with Terraform Cloud, which expects artifacts in the terraform directory.

## Legacy Scripts

The bash scripts (`build_layer.sh`, `build_deployment_package.sh`) are kept for reference but should not be used for new deployments.

### What the scripts did

1. **build_layer.sh**: Packaged Python dependencies for AWS Lambda deployment as a Lambda layer
   - Created `lambda_layer/python/` directory structure
   - Filtered out unnecessary dependencies (development tools, excluded services)
   - Installed production dependencies using pip with Lambda-compatible settings
   - Removed test files and bytecode to reduce layer size
   - Created `lambda_layer.zip` file

2. **build_deployment_package.sh**: Packaged application code for Lambda deployment
   - Copied `app/` directory with all services and clients
   - Copied `lambda_handler.py` entry point
   - Included `app/static/config.yaml`
   - Excluded test files and unnecessary artifacts
   - Created `lambda_deployment_package.zip` file

### Excluded Dependencies

The following dependencies are excluded from the Lambda layer:

- **Web Framework**: FastAPI, uvicorn (not needed in Lambda)
- **Scheduler**: APScheduler (replaced by EventBridge)
- **Google Calendar**: google-api-python-client, google-auth* (removed feature)
- **Redis**: redis (replaced by DynamoDB)
- **Discord Library**: discord.py (using REST API directly)
- **Development Tools**: black, pylint, isort, mypy_extensions, astroid, mccabe, dill, tomlkit, pathspec, click

## See Also

- [Root README](../README.md) - Main project documentation
- [Terraform README](../terraform/README.md) - Infrastructure deployment guide
- [tasks.py](../tasks.py) - Build system implementation

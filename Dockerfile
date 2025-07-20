# -----------------------------------------------------------------------------
# Multi-stage Dockerfile for the trading-bot FastAPI application
# -----------------------------------------------------------------------------
# Stage 1: Build environment (install dependencies in a virtual environment)
FROM cgr.dev/chainguard/python:latest-dev AS dev

# Set the working directory inside the container
WORKDIR /app

# Create a Python virtual environment in /app/venv
RUN python -m venv venv

# Add the virtual environment's bin directory to PATH
ENV PATH="/app/venv/bin":$PATH

# Copy the requirements file into the container
COPY requirements.txt requirements.txt

# Install Python dependencies into the virtual environment
RUN pip install -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: Production image (copy only necessary files and the venv)
FROM cgr.dev/chainguard/python:latest

# Set the working directory inside the container
WORKDIR /app

# Copy the application code from the build stage
COPY /app .

# Copy the virtual environment from the build stage
COPY --from=dev /app/venv /app/venv

# Set the virtual environment's bin directory in PATH
ENV PATH="/app/venv/bin:$PATH"

# Set the default command to run the application
ENTRYPOINT ["python", "main.py"]
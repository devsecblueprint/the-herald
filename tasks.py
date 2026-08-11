"""
Build and deployment tasks for The Herald (ECS/Docker).
Run with: invoke <task_name>
"""

import json
import shutil
from pathlib import Path

from invoke import task


ECR_REPOSITORY = "the-herald"
ECS_CLUSTER = "dsb-platform"
ECS_SERVICE = "the-herald"
AWS_REGION = "us-east-2"


@task
def build(c):
    """Build the Docker image locally."""
    print("Building Docker image...")
    c.run("docker build -t the-herald:latest .")
    print("✓ Docker image built: the-herald:latest")


@task
def run(c):
    """Run the application locally with Docker."""
    print("Starting The Herald locally...")
    c.run(
        "docker run --rm -p 8080:8080 "
        "--env-file .env "
        "-e PARAMETER_STORE_PREFIX=/the-herald/prod/ "
        "-e LOG_LEVEL=DEBUG "
        "the-herald:latest",
        pty=True,
    )


@task
def push(c, tag="latest"):
    """Build and push Docker image to ECR."""
    print("Logging in to ECR...")
    login_cmd = c.run(
        f"aws ecr get-login-password --region {AWS_REGION}",
        hide=True,
    )
    account_id = c.run(
        "aws sts get-caller-identity --query Account --output text",
        hide=True,
    ).stdout.strip()

    ecr_url = f"{account_id}.dkr.ecr.{AWS_REGION}.amazonaws.com"
    c.run(
        f"echo {login_cmd.stdout.strip()} | docker login --username AWS --password-stdin {ecr_url}",
        hide=True,
    )

    image_uri = f"{ecr_url}/{ECR_REPOSITORY}:{tag}"

    print(f"Building and pushing image: {image_uri}")
    c.run(f"docker build -t {image_uri} .")
    c.run(f"docker push {image_uri}")

    # Also tag and push as latest
    if tag != "latest":
        latest_uri = f"{ecr_url}/{ECR_REPOSITORY}:latest"
        c.run(f"docker tag {image_uri} {latest_uri}")
        c.run(f"docker push {latest_uri}")

    print(f"✓ Image pushed: {image_uri}")


@task
def deploy(c, tag="latest"):
    """Force a new deployment of the ECS service."""
    print(f"Deploying to ECS cluster '{ECS_CLUSTER}', service '{ECS_SERVICE}'...")
    c.run(
        f"aws ecs update-service "
        f"--cluster {ECS_CLUSTER} "
        f"--service {ECS_SERVICE} "
        f"--force-new-deployment "
        f"--region {AWS_REGION}"
    )
    print("✓ Deployment triggered. Waiting for service stability...")
    c.run(
        f"aws ecs wait services-stable "
        f"--cluster {ECS_CLUSTER} "
        f"--services {ECS_SERVICE} "
        f"--region {AWS_REGION}"
    )
    print("✓ Service is stable.")


@task(pre=[build])
def push_and_deploy(c, tag="latest"):
    """Build, push to ECR, and deploy to ECS."""
    push(c, tag=tag)
    deploy(c, tag=tag)


@task
def terraform_apply(c):
    """Run terraform apply to deploy infrastructure changes."""
    print("Running terraform apply...")
    c.run("terraform -chdir=terraform init && terraform -chdir=terraform apply -auto-approve")


@task
def logs(c, follow=False):
    """Tail CloudWatch logs for the ECS service."""
    follow_flag = "--follow" if follow else ""
    c.run(
        f"aws logs tail /ecs/the-herald --region {AWS_REGION} {follow_flag}",
        pty=True,
    )


@task
def clean(c):
    """Clean build artifacts."""
    print("Cleaning build artifacts...")

    artifacts = [
        Path("terraform/lambda_layer"),
        Path("terraform/lambda_layer.zip"),
        Path("terraform/lambda_deployment_package"),
        Path("terraform/lambda_deployment_package.zip"),
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

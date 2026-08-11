# ============================================================================
# Data Sources
# ============================================================================

data "aws_caller_identity" "current" {}

# Existing ECS cluster
data "aws_ecs_cluster" "dsb_platform" {
  cluster_name = "dsb-platform"
}

# Default VPC
data "aws_vpc" "main" {
  default = true
}

# Default subnets (public) for Fargate tasks
data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

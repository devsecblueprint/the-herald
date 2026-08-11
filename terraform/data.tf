# ============================================================================
# Data Sources
# ============================================================================

data "aws_caller_identity" "current" {}

# Existing ECS cluster
data "aws_ecs_cluster" "dsb_platform" {
  cluster_name = "dsb-platform"
}

# VPC - uses the default VPC or the one tagged for dsb-platform
data "aws_vpc" "main" {
  filter {
    name   = "tag:Name"
    values = [var.vpc_name]
  }
}

# Public subnets for Fargate tasks (no ingress rules — not exposed)
data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }

  filter {
    name   = "tag:Tier"
    values = ["public"]
  }
}

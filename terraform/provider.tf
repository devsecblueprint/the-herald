# ============================================================================
# Terraform and Provider Configuration
# ============================================================================

terraform {
  cloud {

    organization = "devsecblueprint"

    workspaces {
      name = "the-herald"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "the-herald"
      Environment = var.environment
      ManagedBy   = "Terraform Cloud"
    }
  }
}

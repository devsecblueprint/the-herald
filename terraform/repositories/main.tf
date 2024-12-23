# ECR Repository and Lifecycle policies
resource "aws_ecr_repository" "ecr_repo" {
  count                = length(local.ecr_repo_names)
  name                 = local.ecr_repo_names[count.index]
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "ecr_lifecycle_policy" {
  count      = length(local.ecr_repo_names)
  repository = local.ecr_repo_names[count.index]
  policy     = local.ecr_lifecycle_policy
}
data "aws_ecr_image" "this" {
  repository_name = var.ecr_repository_name
  most_recent     = true
}

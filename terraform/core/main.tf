module "youtube_channel_subscriber_exec_role" {
  source = "./modules/iam"

  name = "${var.resource_prefix}-youtube-subscriber-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      },
    ]
  })
  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    "arn:aws:iam::aws:policy/AmazonSSMReadOnlyAccess"
  ]
  inline_policy_enabled = false
}

module "youtube_channel_subscriber" {
    source = "./modules/lambda"

    function_name = "${var.resource_prefix}-youtube-subscriber"
    ecr_repository_name = "${var.resource_prefix}-youtube-subscriber-image"
    timeout = 10
    role_arn = module.youtube_channel_subscriber_exec_role.arn
}
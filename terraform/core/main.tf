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

  function_name       = "${var.resource_prefix}-youtube-subscriber"
  ecr_repository_name = "${var.resource_prefix}-youtube-subscriber-image"
  timeout             = 120 # 2 minutes
  role_arn            = module.youtube_channel_subscriber_exec_role.arn
  environment_variables = {
    "YOUTUBE_CHANNEL_HANDLES" : join(",", local.YOUTUBE_CHANNEL_HANDLES)
    "CALLBACK_URL" : module.youtube_channel_subscriber_api_gw.api_gateway_url
  }

  create_event_rule              = true
  event_rule_name                = "${var.resource_prefix}-sub-lambda-event-rule"
  event_target_id                = "${var.resource_prefix}-sub-lambda-event-target"
  event_rule_description         = "Triggers Lambda function subscriber every day!"
  event_rule_schedule_expression = "rate(1 day)"

  create_permission    = true
  permission_action    = "lambda:InvokeFunction"
  permission_principal = "events.amazonaws.com"
}

module "youtube_channel_subscriber_api_gw" {
  source = "./modules/api_gateway"

  api_name                   = "${var.resource_prefix}-youtube-subscriber-api"
  api_description            = "Youtube Subscriber API"
  lambda_function_name       = module.discord_bot.name
  lambda_function_invoke_arn = module.discord_bot.invoke_arn
}


module "security_newsletter_exec_role" {
  source = "./modules/iam"

  name = "${var.resource_prefix}-security-newsletter-role"
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

module "security_newsletter" {
  source = "./modules/lambda"

  function_name       = "${var.resource_prefix}-security-newsletter"
  ecr_repository_name = "${var.resource_prefix}-security-newsletter-image"
  timeout             = 10
  role_arn            = module.security_newsletter_exec_role.arn
}

module "discord_bot_exec_role" {
  source = "./modules/iam"

  name = "${var.resource_prefix}-role"
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

module "discord_bot" {
  source = "./modules/lambda"

  function_name = var.resource_prefix
  # TODO: Change the ECR Repo Image Below
  ecr_repository_name = "${var.resource_prefix}-security-newsletter-image"
  timeout             = 10
  role_arn            = module.discord_bot_exec_role.arn
}
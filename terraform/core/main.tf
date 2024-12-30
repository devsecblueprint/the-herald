#######
# SSM Parameters
#######
module "discord_token" {
  source = "./modules/ssm"

  name        = "/credentials/discord/token"
  description = "Discord Token"
  value       = var.DISCORD_TOKEN
  type        = "SecureString"
}

# Youtube Channel Subscriber
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
  inline_policy_enabled = true
  inline_policy = jsonencode({
    "Version" : "2012-10-17"
    "Statement" : [
      {
        "Action" : [
          "sqs:SendMessage"
        ]
        "Effect" : "Allow"
        "Resource" : aws_sqs_queue.discord_bot_queue.arn
      },
      {
        "Action" : [
          "lambda:GetFunction",
          "lambda:GetFunctionUrlConfig"
        ]
        "Effect" : "Allow"
        "Resource" : "arn:aws:lambda:*:${local.account_id}:function:${var.resource_prefix}-youtube-subscriber"
      }
    ]
  })
}

module "youtube_channel_subscriber" {
  source = "./modules/lambda"

  function_name       = "${var.resource_prefix}-youtube-subscriber"
  ecr_repository_name = "${var.resource_prefix}-youtube-subscriber-image"
  timeout             = 120 # 2 minutes
  role_arn            = module.youtube_channel_subscriber_exec_role.arn

  create_event_rule              = true
  event_rule_name                = "${var.resource_prefix}-youtube-subscriber-event-rule"
  event_target_id                = "${var.resource_prefix}-youtube-subscriber-event-target"
  event_rule_schedule_expression = "rate(1 day)"

  create_permission    = true
  permission_action    = "lambda:InvokeFunction"
  permission_principal = "events.amazonaws.com"

  create_function_url             = true
  function_url_authorization_type = "NONE"

  environment_variables = {
    "YOUTUBE_CHANNEL_HANDLES" : join(",", local.YOUTUBE_CHANNEL_HANDLES)
    "SQS_QUEUE_URL" : aws_sqs_queue.discord_bot_queue.url
    "DISCORD_BOT_LAMBDA_NAME" : module.discord_bot.name
  }
}

# Security Newsletter Creation
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

  inline_policy_enabled = true
  inline_policy = jsonencode({
    "Version" : "2012-10-17"
    "Statement" : [
      {
        "Action" : [
          "sqs:SendMessage"
        ]
        "Effect" : "Allow"
        "Resource" : aws_sqs_queue.discord_bot_queue.arn
      }
    ]
  })
}

module "security_newsletter" {
  source = "./modules/lambda"

  function_name       = "${var.resource_prefix}-security-newsletter"
  ecr_repository_name = "${var.resource_prefix}-security-newsletter-image"
  timeout             = 10
  role_arn            = module.security_newsletter_exec_role.arn

  environment_variables = {
    "SQS_QUEUE_URL" : aws_sqs_queue.discord_bot_queue.url
  }
}

# Discord Bot
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
  inline_policy_enabled = true
  inline_policy = jsonencode({
    "Version" : "2012-10-17"
    "Statement" : [
      {
        "Action" : [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        "Effect" : "Allow"
        "Resource" : aws_sqs_queue.discord_bot_queue.arn
      }
    ]
  })
}

module "discord_bot" {
  source = "./modules/lambda"

  function_name       = var.resource_prefix
  ecr_repository_name = "${var.resource_prefix}-image"
  timeout             = 60
  role_arn            = module.discord_bot_exec_role.arn

  create_event_rule              = true
  event_rule_name                = "${var.resource_prefix}-event-rule"
  event_target_id                = "${var.resource_prefix}-event-target"
  event_rule_schedule_expression = "rate(1 day)"

  environment_variables = {
    "DISCORD_GUILD_ID" : var.DISCORD_GUILD_ID
    "DISCORD_TOKEN_PARAMETER" : module.discord_token.name
    "SQS_QUEUE_URL" : aws_sqs_queue.discord_bot_queue.url
  }

  create_permission    = true
  permission_action    = "lambda:InvokeFunction"
  permission_principal = "events.amazonaws.com"
}

# Resources - SQS
resource "aws_sqs_queue" "discord_bot_queue" {
  name = "${var.resource_prefix}-queue.fifo"

  visibility_timeout_seconds = 30
  message_retention_seconds  = 86400 # 1 day
  delay_seconds              = 0
  receive_wait_time_seconds  = 10

  fifo_queue                  = true
  content_based_deduplication = true
}

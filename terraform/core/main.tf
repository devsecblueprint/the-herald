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
          "lambda:GetFunction",
          "lambda:GetFunctionUrlConfig"
        ]
        "Effect" : "Allow"
        "Resource" : "arn:aws:lambda:*:${local.account_id}:function:${var.resource_prefix}"
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

  environment_variables = {
    "YOUTUBE_CHANNEL_HANDLES" : join(",", local.YOUTUBE_CHANNEL_HANDLES)
    "DYNAMODB_TABLE_ARN" : aws_dynamodb_table.discord_bot_table.arn
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
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        "Effect" : "Allow"
        "Resource" : aws_dynamodb_table.discord_bot_table.arn
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

  create_event_rule              = true
  event_rule_name                = "${var.resource_prefix}-security-newsletter-event-rule"
  event_target_id                = "${var.resource_prefix}-security-newsletter-event-target"
  event_rule_schedule_expression = "rate(1 hour)"

  create_permission    = true
  permission_action    = "lambda:InvokeFunction"
  permission_principal = "events.amazonaws.com"

  environment_variables = {
    "DYNAMODB_TABLE_ARN" : aws_dynamodb_table.discord_bot_table.arn
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
          "dynamodb:GetItem",
          "dynamodb:DeleteItem",
          "dynamodb:Scan"
        ]
        "Effect" : "Allow"
        "Resource" : aws_dynamodb_table.discord_bot_table.arn
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
  event_rule_schedule_expression = "rate(1 hour)"

  environment_variables = {
    "DISCORD_GUILD_ID" : var.DISCORD_GUILD_ID
    "DISCORD_TOKEN_PARAMETER" : module.discord_token.name
    "DYNAMODB_TABLE_ARN" : aws_dynamodb_table.discord_bot_table.arn
    "NEWSLETTER_CHANNEL_NAME" : "security-news-test"      #"📰-security-news"
    "CONTENT_CORNER_CHANNEL_NAME" : "content-corner-test" #"📹-content-corner"
  }

  create_permission    = true
  permission_action    = "lambda:InvokeFunction"
  permission_principal = "events.amazonaws.com"

  create_function_url             = true
  function_url_authorization_type = "NONE"
}

# DynamoDB Configuration (Table)
resource "aws_dynamodb_table" "discord_bot_table" {
  name         = "${var.resource_prefix}-temp-data"
  billing_mode = "PAY_PER_REQUEST" # On-demand capacity
  hash_key     = "type"
  range_key    = "link"

  attribute {
    name = "type"
    type = "S"
  }

  attribute {
    name = "link"
    type = "S"
  }

  global_secondary_index {
    name            = "type-index"
    hash_key        = "type"
    projection_type = "ALL"
    write_capacity  = 5
    read_capacity   = 5
  }

  ttl {
    enabled        = true
    attribute_name = "ExpirationTime"
  }

  point_in_time_recovery {
    enabled = false
  }

  server_side_encryption {
    enabled = true
  }
}
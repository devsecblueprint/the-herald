# ============================================================================
# The Herald - Discord Bot Lambda Infrastructure
# ============================================================================
# This Terraform configuration deploys a serverless Discord bot on AWS Lambda
# with EventBridge scheduling, DynamoDB state tracking, and Parameter Store
# for secrets management.
#
# Components:
# - Lambda Function with Python 3.11 runtime
# - Lambda Layer for Python dependencies
# - EventBridge rules for scheduled invocations
# - DynamoDB table for reminder tracking
# - Parameter Store for Discord credentials
# - IAM roles and policies (least privilege)
# - CloudWatch Logs for monitoring
# ============================================================================

# ----------------------------------------------------------------------------
# Lambda Function and Layer
# ----------------------------------------------------------------------------

resource "aws_lambda_function" "the_herald_handler" {
  function_name = "the-herald-handler"
  description   = "The Herald Discord bot handler for newsletter publishing and event notifications"

  # Deployment package
  filename         = var.lambda_deployment_package_path
  source_code_hash = filebase64sha256(var.lambda_deployment_package_path)

  # Runtime configuration
  runtime = "python3.13"
  handler = "lambda_handler.main"

  # Resource allocation
  memory_size = 1024 # 1 GB
  timeout     = 300  # 5 minutes

  # IAM role
  role = aws_iam_role.lambda_execution_role.arn

  # Lambda layers
  layers = [aws_lambda_layer_version.the_herald_dependencies.arn]

  # Environment variables
  environment {
    variables = {
      PARAMETER_STORE_PREFIX = var.parameter_store_prefix
      # DYNAMODB_TABLE_NAME    = aws_dynamodb_table.the_herald_reminders.name  # Disabled - newsletters only
      LOG_LEVEL = var.log_level
    }
  }

  # CloudWatch Logs configuration
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda_logs.name
  }

  # Tags
  tags = {
    Name        = "the-herald-handler"
    Environment = var.environment
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic_execution,
    aws_cloudwatch_log_group.lambda_logs
  ]
}

resource "aws_lambda_layer_version" "the_herald_dependencies" {
  layer_name          = "the-herald-dependencies"
  description         = "Python dependencies for Discord bot Lambda function"
  filename            = var.lambda_layer_package_path
  source_code_hash    = filebase64sha256(var.lambda_layer_package_path)
  compatible_runtimes = ["python3.13"]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/the-herald-handler"
  retention_in_days = var.log_retention_days

  tags = {
    Name        = "the-herald-handler-logs"
    Environment = var.environment
  }
}

# ----------------------------------------------------------------------------
# EventBridge Scheduling Rules
# ----------------------------------------------------------------------------

# Newsletter Publishing (every 15 minutes)
resource "aws_cloudwatch_event_rule" "newsletter_schedule" {
  name                = "the-herald-newsletter-schedule"
  description         = "Triggers Discord bot newsletter handler every 60 minutes"
  schedule_expression = "rate(60 minutes)"

  tags = {
    Name        = "the-herald-newsletter-schedule"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "newsletter_lambda" {
  rule      = aws_cloudwatch_event_rule.newsletter_schedule.name
  target_id = "the-herald-newsletter-target"
  arn       = aws_lambda_function.the_herald_handler.arn

  input = jsonencode({
    handler_type = "newsletter"
    source       = "eventbridge.schedule"
  })
}

resource "aws_lambda_permission" "allow_eventbridge_newsletter" {
  statement_id  = "AllowExecutionFromEventBridgeNewsletter"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.the_herald_handler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.newsletter_schedule.arn
}

# Event Notifications (DISABLED - focusing on newsletters only)
# resource "aws_cloudwatch_event_rule" "event_notification_schedule" {
#   name                = "the-herald-event-notification-schedule"
#   description         = "Triggers Discord bot event notification handler every 5 minutes"
#   schedule_expression = "rate(5 minutes)"
#
#   tags = {
#     Name        = "the-herald-event-notification-schedule"
#     Environment = var.environment
#   }
# }
#
# resource "aws_cloudwatch_event_target" "event_notification_lambda" {
#   rule      = aws_cloudwatch_event_rule.event_notification_schedule.name
#   target_id = "the-herald-event-notification-target"
#   arn       = aws_lambda_function.the_herald_handler.arn
#
#   input = jsonencode({
#     handler_type = "event_notification"
#     source       = "eventbridge.schedule"
#   })
# }
#
# resource "aws_lambda_permission" "allow_eventbridge_event_notification" {
#   statement_id  = "AllowExecutionFromEventBridgeEventNotification"
#   action        = "lambda:InvokeFunction"
#   function_name = aws_lambda_function.the_herald_handler.function_name
#   principal     = "events.amazonaws.com"
#   source_arn    = aws_cloudwatch_event_rule.event_notification_schedule.arn
# }

# ----------------------------------------------------------------------------
# DynamoDB Table for Reminder Tracking (DISABLED - focusing on newsletters only)
# ----------------------------------------------------------------------------

# resource "aws_dynamodb_table" "the_herald_reminders" {
#   name         = "the-herald-reminders"
#   billing_mode = "PAY_PER_REQUEST" # On-demand billing mode
#
#   # Partition key: composite key format "{event_id}:{user_id}:{reminder_type}"
#   hash_key = "reminder_key"
#
#   attribute {
#     name = "reminder_key"
#     type = "S" # String
#   }
#
#   # TTL configuration for automatic expiration (2 hours after timestamp)
#   ttl {
#     attribute_name = "ttl"
#     enabled        = true
#   }
#
#   # Point-in-time recovery for data protection
#   point_in_time_recovery {
#     enabled = var.enable_dynamodb_pitr
#   }
#
#   # Tags
#   tags = {
#     Name        = "the-herald-reminders"
#     Environment = var.environment
#     Purpose     = "Track sent Discord event reminders to prevent duplicates"
#   }
# }

# ----------------------------------------------------------------------------
# Parameter Store for Secrets Management
# ----------------------------------------------------------------------------

resource "aws_ssm_parameter" "discord_token" {
  name        = "${var.parameter_store_prefix}discord-token"
  description = "Discord bot authentication token"
  type        = "SecureString"
  value       = var.DISCORD_TOKEN

  # Use AWS managed KMS key for encryption
  # To use a custom KMS key, set var.kms_key_id
  key_id = var.kms_key_id != "" ? var.kms_key_id : null

  tags = {
    Name        = "the-herald-token"
    Environment = var.environment
    Purpose     = "Discord bot authentication"
  }

  lifecycle {
    ignore_changes = [value] # Prevent Terraform from updating the value after initial creation
  }
}

resource "aws_ssm_parameter" "guild_id" {
  name        = "${var.parameter_store_prefix}guild-id"
  description = "Discord server (guild) ID"
  type        = "String"
  value       = var.DISCORD_GUILD_ID

  tags = {
    Name        = "discord-guild-id"
    Environment = var.environment
    Purpose     = "Discord server identification"
  }
}

# ----------------------------------------------------------------------------
# IAM Roles and Policies
# ----------------------------------------------------------------------------

resource "aws_iam_role" "lambda_execution_role" {
  name        = "the-herald-lambda-execution-role"
  description = "IAM role for Discord bot Lambda function execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "the-herald-lambda-execution-role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "cloudwatch_logs_policy" {
  name = "the-herald-cloudwatch-logs-policy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_logs.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "parameter_store_read_policy" {
  name = "the-herald-parameter-store-read-policy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = [
          aws_ssm_parameter.discord_token.arn,
          aws_ssm_parameter.guild_id.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = var.kms_key_id != "" ? var.kms_key_id : "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alias/aws/ssm"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

# DynamoDB IAM Policy (DISABLED - newsletters only)
# resource "aws_iam_role_policy" "dynamodb_policy" {
#   name = "the-herald-dynamodb-policy"
#   role = aws_iam_role.lambda_execution_role.id
#
#   policy = jsonencode({
#     Version = "2012-10-17"
#     Statement = [
#       {
#         Effect = "Allow"
#         Action = [
#           "dynamodb:GetItem",
#           "dynamodb:PutItem",
#           "dynamodb:Query",
#           "dynamodb:UpdateItem"
#         ]
#         Resource = aws_dynamodb_table.the_herald_reminders.arn
#       }
#     ]
#   })
# }

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ----------------------------------------------------------------------------
# Outputs
# ----------------------------------------------------------------------------

output "lambda_function_arn" {
  description = "ARN of the Discord bot Lambda function"
  value       = aws_lambda_function.the_herald_handler.arn
}

output "lambda_function_name" {
  description = "Name of the Discord bot Lambda function"
  value       = aws_lambda_function.the_herald_handler.function_name
}

output "lambda_layer_arn" {
  description = "ARN of the Lambda layer containing Python dependencies"
  value       = aws_lambda_layer_version.the_herald_dependencies.arn
}

output "lambda_layer_version" {
  description = "Version number of the Lambda layer"
  value       = aws_lambda_layer_version.the_herald_dependencies.version
}

output "cloudwatch_log_group_name" {
  description = "Name of the CloudWatch log group for Lambda function"
  value       = aws_cloudwatch_log_group.lambda_logs.name
}

output "cloudwatch_log_group_arn" {
  description = "ARN of the CloudWatch log group"
  value       = aws_cloudwatch_log_group.lambda_logs.arn
}

output "newsletter_schedule_rule_arn" {
  description = "ARN of the EventBridge rule for newsletter publishing"
  value       = aws_cloudwatch_event_rule.newsletter_schedule.arn
}

# output "event_notification_schedule_rule_arn" {
#   description = "ARN of the EventBridge rule for event notifications"
#   value       = aws_cloudwatch_event_rule.event_notification_schedule.arn
# }

# output "dynamodb_table_name" {
#   description = "Name of the DynamoDB table for reminder tracking"
#   value       = aws_dynamodb_table.the_herald_reminders.name
# }

# output "dynamodb_table_arn" {
#   description = "ARN of the DynamoDB table for reminder tracking"
#   value       = aws_dynamodb_table.the_herald_reminders.arn
# }

output "discord_token_parameter_name" {
  description = "Name of the Discord bot token parameter in Parameter Store"
  value       = aws_ssm_parameter.discord_token.name
}

output "guild_id_parameter_name" {
  description = "Name of the Discord guild ID parameter in Parameter Store"
  value       = aws_ssm_parameter.guild_id.name
}

output "lambda_execution_role_arn" {
  description = "ARN of the Lambda execution role"
  value       = aws_iam_role.lambda_execution_role.arn
}

output "lambda_execution_role_name" {
  description = "Name of the Lambda execution role"
  value       = aws_iam_role.lambda_execution_role.name
}

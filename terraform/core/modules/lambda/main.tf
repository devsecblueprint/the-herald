# Lambda Function
resource "aws_lambda_function" "this" {
  function_name = var.function_name
  role          = var.role_arn

  image_uri    = data.aws_ecr_image.this.image_uri
  timeout      = var.timeout
  package_type = "Image"

  environment {
    variables = var.environment_variables
  }
}

# Lambda Permission
resource "aws_lambda_permission" "this" {
  count = var.create_permission ? 1 : 0

  statement_id           = var.permission_statement_id
  action                 = var.permission_action
  function_name          = aws_lambda_function.this.function_name
  principal              = var.permission_principal
  source_arn             = var.permission_source_arn
  function_url_auth_type = var.permission_function_url_auth_type
}

resource "aws_lambda_function_event_invoke_config" "this" {
  count = var.create_event_invoke_config ? 1 : 0

  function_name          = aws_lambda_function.this.function_name
  maximum_retry_attempts = var.event_invoke_maximum_retry_attempts
  qualifier              = var.event_invoke_qualifier
}

# EventBridge Rule
resource "aws_cloudwatch_event_rule" "this" {
  count = var.create_event_rule ? 1 : 0

  name                = var.event_rule_name
  description         = var.event_rule_description
  schedule_expression = var.event_rule_schedule_expression
}

# EventBridge Target
resource "aws_cloudwatch_event_target" "this" {
  count = var.create_event_rule ? 1 : 0

  rule      = aws_cloudwatch_event_rule.this[0].name
  target_id = var.event_target_id
  arn       = aws_lambda_function.this.arn
}

# EventBridge Permission for Lambda
resource "aws_lambda_permission" "allow_eventbridge" {
  count = var.create_event_rule ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.this[0].arn
}
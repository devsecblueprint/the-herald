# Terraform Variables for Discord Bot Lambda Infrastructure
variable "DISCORD_TOKEN" {}
variable "DISCORD_GUILD_ID" {}

# AWS Configuration
variable "aws_region" {
  description = "AWS region where resources will be created"
  type        = string
  default     = "us-east-2"
}

variable "environment" {
  description = "Environment name (e.g., dev, staging, prod)"
  type        = string
  default     = "prod"
}

# Lambda Configuration
variable "lambda_deployment_package_path" {
  description = "Path to the Lambda deployment package ZIP file"
  type        = string
  default     = "lambda_deployment_package.zip"
}

variable "lambda_layer_package_path" {
  description = "Path to the Lambda layer package ZIP file"
  type        = string
  default     = "lambda_layer.zip"
}

variable "log_level" {
  description = "Logging level for Lambda function (DEBUG, INFO, WARNING, ERROR)"
  type        = string
  default     = "INFO"
}

variable "log_retention_days" {
  description = "Number of days to retain CloudWatch logs"
  type        = number
  default     = 30
}

# Parameter Store Configuration
variable "parameter_store_prefix" {
  description = "Prefix for Parameter Store keys (e.g., /the-herald/prod/)"
  type        = string
  default     = "/the-herald/prod/"
}

variable "kms_key_id" {
  description = "KMS key ID for encrypting Parameter Store SecureString values (leave empty to use AWS managed key)"
  type        = string
  default     = ""
}

# DynamoDB Configuration
variable "enable_dynamodb_pitr" {
  description = "Enable point-in-time recovery for DynamoDB table"
  type        = bool
  default     = false
}

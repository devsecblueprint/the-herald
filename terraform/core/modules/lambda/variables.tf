variable "function_name" {
  description = "Name of the Lambda function"
  type        = string
}

variable "role_arn" {
  description = "ARN of the IAM role for the Lambda function"
  type        = string
}

variable "timeout" {
  description = "Timeout for the Lambda function"
  type        = number
}

variable "package_type" {
  description = "The package type of the Lambda function (e.g., Image)"
  type        = string
  default     = "Image"
}

variable "environment_variables" {
  description = "Environment variables for the Lambda function"
  type        = map(string)
  default     = {}
}

# Lambda Permission Variables
variable "create_permission" {
  description = "Whether to create a Lambda permission"
  type        = bool
  default     = false
}

variable "permission_statement_id" {
  description = "Statement ID for the Lambda permission"
  type        = string
  default     = "1"
}

variable "permission_action" {
  description = "Action for the Lambda permission"
  type        = string
  default     = ""
}

variable "permission_principal" {
  description = "Principal for the Lambda permission"
  type        = string
  default     = ""
}

variable "permission_source_arn" {
  description = "Source ARN for the Lambda permission"
  type        = string
  default     = ""
}

variable "permission_function_url_auth_type" {
  description = "Authorization type for the Lambda function URL"
  type        = string
  default     = null
}

# EventBridge Rule Variables
variable "create_event_rule" {
  description = "Whether to create an EventBridge rule"
  type        = bool
  default     = false
}

variable "event_rule_name" {
  description = "Name of the EventBridge rule"
  type        = string
  default     = ""
}

variable "event_rule_description" {
  description = "Description of the EventBridge rule"
  type        = string
  default     = ""
}

variable "event_rule_schedule_expression" {
  description = "Schedule expression for the EventBridge rule"
  type        = string
  default     = ""
}

variable "event_target_id" {
  description = "ID of the EventBridge target"
  type        = string
  default     = ""
}

# Event Invoke Config Variables
variable "create_event_invoke_config" {
  description = "Whether to create an event invoke configuration"
  type        = bool
  default     = false
}

variable "event_invoke_maximum_retry_attempts" {
  description = "Maximum retry attempts for event invoke"
  type        = number
  default     = 0
}

variable "event_invoke_qualifier" {
  description = "Qualifier for event invoke"
  type        = string
  default     = "$LATEST"
}

# ECR Repository Name
variable "ecr_repository_name" {
  description = "Name of the ECR repository"
  type        = string
}
variable "api_name" {
  type        = string
  description = "Name of the API Gateway"
}

variable "api_description" {
  type        = string
  description = "Description of the API Gateway"
}

variable "lambda_function_invoke_arn" {
  type        = string
  description = "ARN to be used for invoking Lambda Function from API Gateway"
}

variable "lambda_function_name" {
  type        = string
  description = "Name of the Lambda Function to be invoked"
}
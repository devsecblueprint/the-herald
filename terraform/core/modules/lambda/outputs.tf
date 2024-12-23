output "arn" {
  value       = aws_lambda_function.this.arn
  description = "The ARN of the Lambda function."
}

output "name" {
  value       = aws_lambda_function.this.function_name
  description = "The name of the Lambda function."
}

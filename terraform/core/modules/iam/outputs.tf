output "name" {
  value       = aws_iam_role.this.name
  description = "The name of the IAM role."
}

output "arn" {
  value       = aws_iam_role.this.arn
  description = "The ARN of the IAM role."
}
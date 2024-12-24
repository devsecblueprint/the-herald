output "name" {
  description = "The name of the created SSM Parameter."
  value       = aws_ssm_parameter.this.name
}
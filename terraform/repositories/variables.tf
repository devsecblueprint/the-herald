variable "region" {
  description = "Default AWS Region"
  default     = "us-east-1"
}

variable "resource_prefix" {
  description = "Prefix for resources"
  default     = "discord-bot"
}

variable "AWS_ACCESS_KEY" {}
variable "AWS_SECRET_ACCESS_KEY" {}
variable "region" {
  description = "Default AWS Region"
  default     = "us-east-1"
}

variable "resource_prefix" {
  description = "Prefix for resources"
  default     = "discord-bot"
}

variable "DISCORD_TOKEN" {}
variable "DISCORD_GUILD_ID" {}
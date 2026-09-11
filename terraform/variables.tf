# ============================================================================
# Terraform Variables for The Herald ECS Infrastructure
# ============================================================================

# Secrets (set via Terraform Cloud workspace variables)
variable "DISCORD_TOKEN" {
  description = "Discord bot authentication token"
  type        = string
  sensitive   = true
}

variable "DISCORD_GUILD_ID" {
  description = "Discord server (guild) ID"
  type        = string
}

variable "YOUTUBE_API_KEY" {
  description = "YouTube Data API v3 key"
  type        = string
  sensitive   = true
}

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

# ECS Task Configuration
variable "task_cpu" {
  description = "CPU units for the Fargate task (256, 512, 1024, 2048, 4096)"
  type        = string
  default     = "256"
}

variable "task_memory" {
  description = "Memory (MB) for the Fargate task"
  type        = string
  default     = "512"
}

variable "desired_count" {
  description = "Number of ECS tasks to run"
  type        = number
  default     = 1
}

# Scheduling Configuration
variable "newsletter_interval_minutes" {
  description = "Interval in minutes for the newsletter publishing job"
  type        = number
  default     = 60
}

variable "event_notification_interval_minutes" {
  description = "Interval in minutes for the event notification job"
  type        = number
  default     = 5
}

# Logging
variable "log_level" {
  description = "Logging level (DEBUG, INFO, WARNING, ERROR)"
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

# YouTube Ingestion
variable "youtube_enabled" {
  description = "Master kill switch for YouTube partner ingestion"
  type        = bool
  default     = true
}

variable "youtube_poll_interval_minutes" {
  description = "Interval in minutes between YouTube polls"
  type        = number
  default     = 30 # making it run every half hour - google quota
}

variable "youtube_exclude_shorts" {
  description = "Announce long-form videos only; set false to announce Shorts too"
  type        = bool
  default     = true
}

variable "content_corner_channel_id" {
  description = "Numeric Discord channel id for #content-corner, where partner uploads are announced"
  type        = string
  default     = "1320592138689319073"
}

variable "notify_role_id" {
  description = "Numeric Discord role id (@Notifs) to ping on every announcement. Empty pings no one."
  type        = string
  default     = "1338013529843826728"
}
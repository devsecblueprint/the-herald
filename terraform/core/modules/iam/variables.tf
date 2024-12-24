variable "name" {
  description = "The name of the IAM role."
  type        = string
}

variable "assume_role_policy" {
  description = "The assume role policy document."
  type        = string
}

variable "managed_policy_arns" {
  description = "List of ARNs for managed policies to attach to the role."
  type        = list(string)
  default     = []
}

variable "inline_policy_enabled" {
  description = "Whether to create an inline policy for the role."
  type        = bool
  default     = false
}

variable "inline_policy_name" {
  description = "The name of the inline policy."
  type        = string
  default     = "DefaultPolicy"
}

variable "inline_policy" {
  description = "The policy document for the inline policy."
  type        = string
  default     = ""
}
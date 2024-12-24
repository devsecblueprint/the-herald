variable "name" {
  description = "Name of the SSM Parameter Store"
  type        = string
}

variable "type" {
  description = "Type of the SSM Parameter Store"
  type        = string
  default     = "String"
  validation {
    condition = (
      var.type == "String" || var.type == "StringList" || var.type == "SecureString"
    )
    error_message = "The `type` must be one of `String`, `StringList`, and `SecureString`."
  }
}

variable "value" {
  description = "Value of the SSM Parameter Store"
  type        = string
}

variable "description" {
  description = "Description of the SSM Parameter Store"
  type        = string
}

variable "tags" {
  description = "Tags for the SSM Parameter Store"
  type        = map(string)
  default     = {}
}
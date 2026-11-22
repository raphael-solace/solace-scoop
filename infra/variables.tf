variable "aws_region" {
  default = "eu-central-1"
}

variable "app_name" {
  default = "scoop"
}

variable "public_api_url" {
  description = "Public HTTPS API origin used by links in Scoop emails"
  type        = string
  default     = "https://x7lihybf8c.execute-api.eu-west-1.amazonaws.com"
}

variable "deployed_api_task_definition" {
  description = "Running API revision preserved while deploy.sh prepares an update"
  type        = string
  default     = null
}

variable "deployed_digest_task_definition" {
  description = "Scheduled digest revision preserved while deploy.sh prepares an update"
  type        = string
  default     = null
}

variable "env_vars" {
  description = "Environment variables for the application"
  type        = map(string)
  sensitive   = true
}

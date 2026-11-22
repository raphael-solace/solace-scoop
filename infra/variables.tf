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

variable "env_vars" {
  description = "Environment variables for the application"
  type        = map(string)
  sensitive   = true
}

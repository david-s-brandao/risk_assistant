variable "aws_region" {
  default = "eu-west-1"
}

variable "project" {
  default = "devunleashed"
}

variable "env" {
  default = "dev"
}

variable "bedrock_model_id" {
  # Default to a Mistral model (requires enabling Model access in Bedrock for this account+region).
  # If you don't have access yet, temporarily set this back to an Amazon Nova model.
  default = "mistral.mixtral-8x7b-instruct-v0:1"
}

variable "web_callback_url" {
  description = "OAuth callback URL for web/extension"
  default     = "https://<EXT_ID>.chromiumapp.org/"
}

variable "web_logout_url" {
  description = "OAuth logout URL for web/extension"
  default     = "https://<EXT_ID>.chromiumapp.org/"
}

variable "billing_alert_email" {
  description = "Email address for billing alerts"
  type        = string
  default     = "admin@example.com"
}

variable "billing_threshold" {
  description = "The threshold in USD/EUR for billing alarm"
  type        = string
  default     = "3"
}

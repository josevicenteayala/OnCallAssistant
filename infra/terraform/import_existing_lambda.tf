terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region where the existing Lambda is deployed."
  type        = string
  default     = "us-east-2"
}

variable "lambda_function_name" {
  description = "Existing Lambda function name behind the Function URL."
  type        = string
}

# Terraform 1.5+ import blocks let us safely pull live infra into state,
# then generate a config file from the real AWS settings.
import {
  to = aws_lambda_function.oncall_assistant
  id = var.lambda_function_name
}

import {
  to = aws_lambda_function_url.oncall_assistant
  id = var.lambda_function_name
}

resource "aws_lambda_function" "oncall_assistant" {
  function_name = var.lambda_function_name
}

resource "aws_lambda_function_url" "oncall_assistant" {
  function_name = aws_lambda_function.oncall_assistant.function_name
}

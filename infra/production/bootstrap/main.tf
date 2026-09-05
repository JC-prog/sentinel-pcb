# One-time setup: creates the S3 bucket that infra/production's own Terraform state lives in.
# Run this once, by hand, before ever running `terraform init` in infra/production/ itself -
# state has to live somewhere before the config that uses it as a backend can exist.
#
# This module's own state is local (deliberately - it manages the bucket that everything else's
# state depends on, so it can't depend on that same bucket). Keep the resulting terraform.tfstate
# file safe (e.g. check it in encrypted, or store it in a team password manager) - losing it
# means losing track of (not losing) the bucket, since `terraform import` can always re-adopt it.
#
# Usage: cd infra/production/bootstrap && terraform init && terraform apply

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "sentinelchat"
}

provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "terraform_state" {
  # Bucket names are globally unique across all of AWS - the account id suffix avoids collisions.
  bucket = "${var.project_name}-terraform-state-${data.aws_caller_identity.current.account_id}"

  tags = {
    Project = var.project_name
    Purpose = "terraform-remote-state"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket                  = aws_s3_bucket.terraform_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_caller_identity" "current" {}

output "bucket_name" {
  value       = aws_s3_bucket.terraform_state.bucket
  description = "Put this in infra/production/backend.tf's backend \"s3\" block."
}

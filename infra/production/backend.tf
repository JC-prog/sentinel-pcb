# Remote state, so more than one person on the deploy team can safely run terraform against the
# same infrastructure. The bucket itself comes from infra/production/bootstrap (run once, first)
# - fill in its "bucket_name" output below. use_lockfile uses S3's own conditional-write locking
# (Terraform >= 1.10) instead of a separate DynamoDB lock table.
terraform {
  backend "s3" {
    bucket       = "sentinelchat-terraform-state-REPLACE_WITH_ACCOUNT_ID" # from bootstrap's output
    key          = "sentinelchat/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

# A shared secret between CloudFront and the ALB. CloudFront adds it to every request it sends to the
# ALB (static_site.tf) and the ALB listener (ecs.tf) answers 403 to anything without it, so only this
# distribution - not some other CloudFront account, and not a direct hit on the ALB - reaches the
# backend. The security group (security_groups.tf) already limits the ALB to CloudFront's addresses.
#
# It sits in Terraform state and in the distribution's config, so treat read access to either as
# access to the backend. Replacing random_password.origin_verify (terraform taint) rotates it; apply
# updates both sides together.

locals {
  origin_verify_header = "X-Origin-Verify"
}

resource "random_password" "origin_verify" {
  length  = 40
  special = false
}

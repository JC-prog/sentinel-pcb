output "cloudfront_domain" {
  description = "The app's URL - both the UI and /api/* are served from here."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "ecr_repository_url" {
  description = "Push backend images here (see README's deploy workflow)."
  value       = aws_ecr_repository.backend.repository_url
}

output "ui_bucket_name" {
  description = "Sync the Angular build here (see README's deploy workflow)."
  value       = aws_s3_bucket.ui.bucket
}

output "cloudfront_distribution_id" {
  description = "Needed to invalidate the cache after a UI deploy."
  value       = aws_cloudfront_distribution.site.id
}

output "alb_dns_name" {
  description = "Debug only - the browser never talks to this directly, CloudFront does."
  value       = aws_lb.main.dns_name
}

output "rds_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

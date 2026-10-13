output "cloudfront_domain" {
  description = "The app's URL - both the UI and /api/* are served from here."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "ecr_repository_url" {
  description = "Push backend images here (see README's deploy workflow)."
  value       = aws_ecr_repository.backend.repository_url
}

output "inference_ecr_repository_url" {
  description = "Push inference images here (see README's deploy workflow)."
  value       = aws_ecr_repository.inference.repository_url
}

output "inference_service_url" {
  description = "Internal only - the backend reaches the classifier here (INFERENCE_BASE_URL)."
  value       = "http://${aws_service_discovery_service.inference.name}.${aws_service_discovery_private_dns_namespace.internal.name}:${var.inference_container_port}"
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

output "ipc_corpus_bucket_name" {
  description = "Upload the IPC corpus export here: aws s3 cp ipc-corpus.jsonl s3://<this>/<ipc_corpus_key>."
  value       = aws_s3_bucket.ipc_corpus.bucket
}

output "ipc_corpus_load_command" {
  description = "Run once after Qdrant is up (and again after every Qdrant restart) to load the IPC corpus."
  value       = "aws ecs run-task --cluster ${aws_ecs_cluster.main.name} --launch-type FARGATE --task-definition ${aws_ecs_task_definition.ipc_corpus_loader.family} --network-configuration 'awsvpcConfiguration={subnets=[${join(",", data.aws_subnets.default.ids)}],securityGroups=[${aws_security_group.ecs_tasks.id}],assignPublicIp=ENABLED}' --region ${var.aws_region}"
}

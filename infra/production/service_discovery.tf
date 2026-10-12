# Private DNS for service-to-service calls inside the default VPC. The backend resolves the
# inference service at http://inference.<namespace>:<port> - no ALB, no public exposure. Cheaper
# and simpler than a second internal load balancer for a single consumer.

resource "aws_service_discovery_private_dns_namespace" "internal" {
  name        = "${var.project_name}.internal"
  description = "Service-to-service DNS within the VPC."
  vpc         = data.aws_vpc.default.id
}

resource "aws_service_discovery_service" "inference" {
  name = "inference"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.internal.id
    routing_policy = "MULTIVALUE"

    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  # ECS updates instance health via the ECS integration; this just has to be present.
  health_check_custom_config {
    failure_threshold = 1
  }
}

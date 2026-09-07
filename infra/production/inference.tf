# The ONNX classification service (inference/). A second Fargate service in the same cluster,
# reachable only from the backend via Cloud Map DNS (service_discovery.tf) - it has no ALB and no
# CloudFront behavior, the browser never calls it. The 4 models are baked into the image at build
# time (inference/scripts/fetch_models.py), so there's no S3 or task role here yet.

resource "aws_cloudwatch_log_group" "inference" {
  name              = "/ecs/${var.project_name}-inference"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "inference" {
  family                   = "${var.project_name}-inference"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.inference_task_cpu
  memory                   = var.inference_task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([
    {
      name      = "inference"
      image     = "${aws_ecr_repository.inference.repository_url}:${var.inference_image_tag}"
      essential = true
      portMappings = [
        { containerPort = var.inference_container_port, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_FORMAT", value = "json" },
        { name = "REQUIRE_MODELS_ON_STARTUP", value = "true" },
        { name = "MODEL_STORE_DIR", value = "/code/model_store" },
      ]
      healthCheck = {
        command     = ["CMD-SHELL", "curl -fsS http://localhost:${var.inference_container_port}/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.inference.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "inference"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "inference" {
  name            = "${var.project_name}-inference"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.inference.arn
  desired_count   = var.inference_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.inference_tasks.id]
    assign_public_ip = true # no NAT Gateway - the task needs a public IP to pull from ECR
  }

  service_registries {
    registry_arn = aws_service_discovery_service.inference.arn
  }
}

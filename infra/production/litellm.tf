# LiteLLM proxy - the OpenAI-compatible endpoint the backend calls instead of api.openai.com, so
# the real provider key never lands in the backend task (see infra/litellm/README.md, app/config/
# settings.py's openai_base_url). A third Fargate service in the same cluster, reachable only
# from the backend over Cloud Map DNS (service_discovery.tf) - no ALB, no CloudFront behavior.
#
# Auth is master-key-only for now: the backend and the shared team proxy use the same
# LITELLM_MASTER_KEY. Add a key database (config.prod.yaml's general_settings.database_url ->
# RDS) when per-consumer budgets or revocable per-developer keys are needed.
#
# The image is pulled straight from ghcr.io (var.litellm_image) - no ECR repo. config.prod.yaml
# is passed in as an env var and written to disk by the container's entrypoint, so a routing or
# model change is a `terraform apply`, not an image build.

resource "random_password" "litellm_master_key" {
  length  = 40
  special = false
}

# One secret, JSON-shaped: the real upstream key plus the proxy's master key. Referenced by ARN
# in "secrets" blocks (here and in ecs.tf) with the ":<json-key>::" suffix, resolved by ECS at
# container start - never a plain env var, never an output.
resource "aws_secretsmanager_secret" "litellm" {
  name = "${var.project_name}/litellm"
}

resource "aws_secretsmanager_secret_version" "litellm" {
  secret_id = aws_secretsmanager_secret.litellm.id
  secret_string = jsonencode({
    OPENAI_API_KEY = var.openai_api_key
    # LiteLLM expects the master key to start with "sk-".
    LITELLM_MASTER_KEY = "sk-${random_password.litellm_master_key.result}"
  })
}

resource "aws_cloudwatch_log_group" "litellm" {
  name              = "/ecs/${var.project_name}-litellm"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "litellm" {
  family                   = "${var.project_name}-litellm"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.litellm_task_cpu
  memory                   = var.litellm_task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([
    {
      name      = "litellm"
      image     = var.litellm_image
      essential = true
      portMappings = [
        { containerPort = var.litellm_container_port, protocol = "tcp" }
      ]
      # Replace the image's entrypoint: write config.prod.yaml (passed as LITELLM_CONFIG_YAML)
      # to disk, then exec the proxy against it. `sh` and `litellm` are both on PATH in the image.
      entryPoint = ["sh", "-c"]
      command = [
        "printf '%s' \"$LITELLM_CONFIG_YAML\" > /tmp/config.yaml && exec litellm --config /tmp/config.yaml --host 0.0.0.0 --port ${var.litellm_container_port}"
      ]
      environment = [
        { name = "LITELLM_CONFIG_YAML", value = file("${path.module}/../litellm/config.prod.yaml") },
      ]
      secrets = [
        { name = "OPENAI_API_KEY", valueFrom = "${aws_secretsmanager_secret.litellm.arn}:OPENAI_API_KEY::" },
        { name = "LITELLM_MASTER_KEY", valueFrom = "${aws_secretsmanager_secret.litellm.arn}:LITELLM_MASTER_KEY::" },
      ]
      healthCheck = {
        # curl isn't guaranteed in the image; python is. /health/liveliness needs no auth.
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:${var.litellm_container_port}/health/liveliness').status==200 else 1)\"",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.litellm.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "litellm"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "litellm" {
  name            = "${var.project_name}-litellm"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.litellm.arn
  desired_count   = var.litellm_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.litellm_tasks.id]
    assign_public_ip = true # no NAT Gateway - the task needs a public IP to pull the image and reach OpenAI
  }

  service_registries {
    registry_arn = aws_service_discovery_service.litellm.arn
  }
}

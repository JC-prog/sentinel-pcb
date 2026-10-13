# Qdrant, the vector store behind chat's long-term memory and the Work tab's run/review points. A
# third Fargate service reachable only from the backend over Cloud Map DNS (service_discovery.tf) -
# no ALB, no CloudFront behavior. Single node, no replication, and deliberately no persistent volume:
# this stack is for short-lived deployments that are torn down afterwards, so Qdrant keeps its data on
# the task's own ephemeral disk and loses it when the task stops. Add an EFS/EBS volume here before
# using this for anything that must outlive a task.

resource "aws_cloudwatch_log_group" "qdrant" {
  name              = "/ecs/${var.project_name}-qdrant"
  retention_in_days = 14
}

resource "aws_security_group" "qdrant_tasks" {
  name_prefix = "${var.project_name}-qdrant-tasks-"
  description = "Allow inbound only from the backend ECS tasks."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "From backend ECS tasks"
    from_port       = var.qdrant_container_port
    to_port         = var.qdrant_container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_ecs_task_definition" "qdrant" {
  family                   = "${var.project_name}-qdrant"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.qdrant_task_cpu
  memory                   = var.qdrant_task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([
    {
      name      = "qdrant"
      image     = "qdrant/qdrant:${var.qdrant_image_tag}"
      essential = true
      portMappings = [
        { containerPort = var.qdrant_container_port, protocol = "tcp" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.qdrant.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "qdrant"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "qdrant" {
  name            = "${var.project_name}-qdrant"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.qdrant.arn
  desired_count   = 1 # single node: Qdrant here has no replication
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.qdrant_tasks.id]
    assign_public_ip = true # no NAT Gateway - the task needs a public IP to pull its image
  }

  service_registries {
    registry_arn = aws_service_discovery_service.qdrant.arn
  }
}

# Scales the three ECS services to zero on a schedule, so a stack someone forgot to destroy stops
# paying for Fargate (about two thirds of its cost) overnight. Off unless var.auto_stop_schedule is
# set. It does not remove the ALB, RDS instance or public IPv4 addresses - only terraform destroy
# does - and it loses nothing that a task restart would not already lose (README "Known gaps").
#
# Start it again with `terraform apply`: desired_count is still managed here, so the next apply sees
# 0 against the configured 1 and scales the services back up.

locals {
  auto_stop_services = var.auto_stop_schedule == "" ? {} : {
    backend   = aws_ecs_service.backend.name
    inference = aws_ecs_service.inference.name
    qdrant    = aws_ecs_service.qdrant.name
  }
}

resource "aws_iam_role" "auto_stop" {
  count = var.auto_stop_schedule == "" ? 0 : 1

  name = "${var.project_name}-auto-stop"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "auto_stop" {
  count = var.auto_stop_schedule == "" ? 0 : 1

  name = "scale-ecs-services-to-zero"
  role = aws_iam_role.auto_stop[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "ecs:UpdateService"
      Resource = [
        aws_ecs_service.backend.id,
        aws_ecs_service.inference.id,
        aws_ecs_service.qdrant.id,
      ]
    }]
  })
}

resource "aws_scheduler_schedule" "auto_stop" {
  for_each = local.auto_stop_services

  name                = "${var.project_name}-stop-${each.key}"
  schedule_expression = var.auto_stop_schedule

  flexible_time_window {
    mode = "OFF"
  }

  target {
    # The universal target calls the ECS API directly - no Lambda needed.
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:updateService"
    role_arn = aws_iam_role.auto_stop[0].arn
    input = jsonencode({
      Cluster      = aws_ecs_cluster.main.name
      Service      = each.value
      DesiredCount = 0
    })
  }
}

# Each tier is only reachable from the tier in front of it - this is the actual security boundary
# (not network isolation, since everything sits in public subnets):
#   cloudfront -> alb (80) -> ecs tasks (container_port) -> rds (5432)
#                                                      \-> inference tasks (inference_container_port)

resource "aws_security_group" "alb" {
  name_prefix = "${var.project_name}-alb-"
  description = "Allow inbound HTTP to the ALB from CloudFront only."
  vpc_id      = data.aws_vpc.default.id

  # Only CloudFront's origin-facing addresses (an AWS-managed prefix list), so the ALB's own DNS name
  # is not a way around CloudFront. The listener additionally requires a secret header (ecs.tf), as
  # that list covers every CloudFront customer, not just this distribution. The prefix list counts as
  # ~55 rules against the group's default quota of 60 - don't add more ingress rules here.
  ingress {
    description     = "HTTP from CloudFront"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
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

resource "aws_security_group" "ecs_tasks" {
  name_prefix = "${var.project_name}-ecs-tasks-"
  description = "Allow inbound only from the ALB."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "From ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
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

resource "aws_security_group" "inference_tasks" {
  name_prefix = "${var.project_name}-inference-tasks-"
  description = "Allow inbound only from the backend ECS tasks."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "From backend ECS tasks"
    from_port       = var.inference_container_port
    to_port         = var.inference_container_port
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

resource "aws_security_group" "rds" {
  name_prefix = "${var.project_name}-rds-"
  description = "Allow inbound Postgres only from the ECS tasks."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "From ECS tasks"
    from_port       = 5432
    to_port         = 5432
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

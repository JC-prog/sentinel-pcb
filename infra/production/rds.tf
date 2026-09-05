resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db"
  subnet_ids = data.aws_subnets.default.ids # RDS requires >= 2 AZs even for a single-AZ instance
}

resource "random_password" "db" {
  length  = 32
  special = false # simplifies embedding it in a connection string / env var
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project_name}-db"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage_gb
  storage_encrypted = true

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # Subnets are "public" (routed to an internet gateway, per the no-NAT-Gateway decision), but
  # the instance itself is not - no public IP is assigned, and the security group above is the
  # actual boundary (only the ECS tasks' SG can reach port 5432).
  publicly_accessible = false

  multi_az                = false # single-AZ - cost over availability, revisit if this needs HA
  backup_retention_period = 7
  skip_final_snapshot     = true # acceptable for this stage; revisit before this holds real data
}

# Full connection string (app.settings.Settings.database_url's expected shape), not just the bare
# password - never appears in a Terraform output or the ECS task definition's plain env vars, only
# referenced by ARN in its "secrets" block, resolved by ECS itself at container start.
resource "aws_secretsmanager_secret" "db_password" {
  name = "${var.project_name}/database-url"
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id = aws_secretsmanager_secret.db_password.id
  secret_string = format(
    "postgresql+asyncpg://%s:%s@%s/%s",
    var.db_username,
    random_password.db.result,
    aws_db_instance.main.endpoint,
    var.db_name,
  )
}

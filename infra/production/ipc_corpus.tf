# Loads Agent 2's IPC-standards corpus into Qdrant. Qdrant here has no persistent volume (qdrant.tf),
# so the `pcb_standards_precedents` collection is empty after every task restart. This puts the
# corpus (an export made with `python -m app.workflow.services.ipc_ingest export FILE`, vectors
# included) in a private bucket and defines a one-shot task that imports it - no embedding calls, no
# OpenAI key. Nothing runs it automatically: run it after Qdrant starts (output `ipc_corpus_load_command`).
# Only the corpus is restored; chat memory and Work-tab runs still live on Qdrant's ephemeral disk
# (README "Known gaps").
#
# The task has two containers sharing a scratch volume: the AWS CLI copies the export from S3, then
# the backend image imports it. The backend image needs no AWS SDK this way.

resource "aws_s3_bucket" "ipc_corpus" {
  bucket        = "${var.project_name}-ipc-corpus-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # an export is re-creatable from the source documents or a developer's Qdrant
}

resource "aws_s3_bucket_public_access_block" "ipc_corpus" {
  bucket                  = aws_s3_bucket.ipc_corpus.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ipc_corpus" {
  bucket = aws_s3_bucket.ipc_corpus.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# The task role: what the loader's own containers may do. Read one bucket, nothing else.
resource "aws_iam_role" "ipc_corpus_loader" {
  name = "${var.project_name}-ipc-corpus-loader"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ipc_corpus_loader" {
  name = "read-ipc-corpus"
  role = aws_iam_role.ipc_corpus_loader.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = ["${aws_s3_bucket.ipc_corpus.arn}/${var.ipc_corpus_key}"]
    }]
  })
}

resource "aws_cloudwatch_log_group" "ipc_corpus_loader" {
  name              = "/ecs/${var.project_name}-ipc-corpus-loader"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "ipc_corpus_loader" {
  family                   = "${var.project_name}-ipc-corpus-loader"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ipc_corpus_loader.arn

  volume {
    name = "corpus"
  }

  container_definitions = jsonencode([
    {
      name      = "fetch"
      image     = "amazon/aws-cli:${var.aws_cli_image_tag}"
      essential = false # it is meant to exit; `import` waits for it to succeed
      command = [
        "s3", "cp",
        "s3://${aws_s3_bucket.ipc_corpus.bucket}/${var.ipc_corpus_key}",
        "/corpus/ipc-corpus.jsonl",
      ]
      mountPoints = [{ sourceVolume = "corpus", containerPath = "/corpus" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ipc_corpus_loader.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "fetch"
        }
      }
    },
    {
      name      = "import"
      image     = "${aws_ecr_repository.backend.repository_url}:${var.container_image_tag}"
      essential = true
      dependsOn = [{ containerName = "fetch", condition = "SUCCESS" }]
      # Qdrant may still be starting: wait for it to answer (up to ~3 minutes), then import. The
      # import is an upsert by point id, so running it twice is harmless.
      entryPoint = ["sh", "-c"]
      command = [
        "for i in $(seq 1 36); do .venv/bin/python -c \"import os,urllib.request; urllib.request.urlopen(os.environ['QDRANT_URL'] + '/readyz', timeout=3)\" && break; sleep 5; done; exec .venv/bin/python -m app.workflow.services.ipc_ingest import /corpus/ipc-corpus.jsonl"
      ]
      environment = [
        { name = "QDRANT_URL", value = "http://${aws_service_discovery_service.qdrant.name}.${aws_service_discovery_private_dns_namespace.internal.name}:${var.qdrant_container_port}" },
      ]
      mountPoints = [{ sourceVolume = "corpus", containerPath = "/corpus", readOnly = true }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ipc_corpus_loader.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "import"
        }
      }
    },
  ])
}

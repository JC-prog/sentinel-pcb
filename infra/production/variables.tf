variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}

variable "project_name" {
  type    = string
  default = "sentinelchat"
}

variable "container_image_tag" {
  description = "Tag of the backend image in ECR to deploy (see infra/production/README.md)."
  type        = string
  default     = "latest"
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "task_cpu" {
  description = "Fargate task vCPU units for the backend (1024 = 1 vCPU). The Work tab's dataset preparation (pandas, OpenCV) and Agent 2's LangGraph review run in this process, which a quarter vCPU cannot carry."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate task memory for the backend, in MiB. 1024 vCPU units allows 2048-8192. 3072 leaves room for the Agent 2 sidecar (explain_enabled)."
  type        = number
  default     = 3072
}

variable "explain_enabled" {
  description = "Run Agent 2's MCP service beside the backend and point chat at it, which switches on \"Explain this result\". Needs the IPC corpus in Qdrant for precedents (ipc_corpus.tf); without it the explanation simply cites none."
  type        = bool
  default     = true
}

variable "agent2_mcp_port" {
  type    = number
  default = 8002
}

variable "desired_count" {
  description = "Number of backend tasks. Keep at 1 until chat image uploads move off local disk (see README) - more than one task otherwise can't reliably serve uploads."
  type        = number
  default     = 1
}

variable "inference_container_port" {
  type    = number
  default = 8001
}

variable "inference_image_tag" {
  description = "Tag of the inference image in ECR to deploy (see infra/production/README.md)."
  type        = string
  default     = "latest"
}

variable "inference_task_cpu" {
  description = "Fargate task vCPU units for the inference service (1024 = 1 vCPU). ONNX CPU inference wants more than the backend."
  type        = number
  default     = 1024
}

variable "inference_task_memory" {
  description = "Fargate task memory for the inference service, in MiB. Raise if the 4 models don't fit."
  type        = number
  default     = 2048
}

variable "inference_desired_count" {
  description = "Number of inference tasks. 1 is fine until classification traffic justifies autoscaling."
  type        = number
  default     = 1
}

variable "openai_api_key" {
  description = "OpenAI API key the backend calls api.openai.com with. Required - supply via a non-committed *.tfvars or TF_VAR_openai_api_key. Never appears in an output or a plain task-def env var; stored only in the sentinelchat/openai secret."
  type        = string
  sensitive   = true
}

variable "db_backup_retention_days" {
  description = "Days RDS keeps automated backups. 1 is the most an AWS free-plan account may use (a higher value is refused with FreeTierRestrictionError); with skip_final_snapshot and a stack meant to be destroyed, backups are not what protects this data. Raise it on a paid plan for anything longer-lived."
  type        = number
  default     = 1
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 20
}

variable "db_name" {
  type    = string
  default = "sentinelchat"
}

variable "db_username" {
  type    = string
  default = "sentinelchat"
}

variable "qdrant_image_tag" {
  description = "Tag of the public qdrant/qdrant image to run. Pinned, not latest - a floating tag could change the on-disk format under an existing EFS volume. Match the version you develop against."
  type        = string
  default     = "v1.19.1"
}

variable "qdrant_container_port" {
  type    = number
  default = 6333
}

variable "qdrant_task_cpu" {
  description = "Fargate task vCPU units for Qdrant (512 = 0.5 vCPU)."
  type        = number
  default     = 512
}

variable "qdrant_task_memory" {
  description = "Fargate task memory for Qdrant, in MiB. 512 vCPU units allows 1024-4096."
  type        = number
  default     = 1024
}

variable "ipc_corpus_key" {
  description = "Object key of the IPC corpus export in the corpus bucket (ipc_corpus.tf), made with `python -m app.workflow.services.ipc_ingest export FILE`."
  type        = string
  default     = "ipc-corpus.jsonl"
}

variable "aws_cli_image_tag" {
  description = "Tag of the public amazon/aws-cli image the corpus loader uses to copy the export from S3."
  type        = string
  default     = "2.17.0"
}

variable "budget_alert_email" {
  description = "Email address for the monthly cost budget alerts (budget.tf). Empty (the default) creates no budget."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly AWS spend, in USD, the budget alerts are measured against. The stack costs about $5/day if left running, so 50 trips in roughly ten days."
  type        = number
  default     = 50
}

variable "daily_budget_usd" {
  description = "Daily AWS spend, in USD, that triggers an alert (budget.tf). The stack costs about $5/day while it is up, so the default of 3 emails you for every day it was running."
  type        = number
  default     = 3
}

variable "auto_stop_schedule" {
  description = "When to scale the three ECS services to zero, as an EventBridge Scheduler expression in UTC, e.g. \"cron(0 14 * * ? *)\" for 14:00 UTC daily. Empty (the default) creates no schedule. This stops the Fargate tasks (about two thirds of the cost); the ALB, RDS and public IPs keep billing until terraform destroy. A later terraform apply scales them back up."
  type        = string
  default     = ""
}

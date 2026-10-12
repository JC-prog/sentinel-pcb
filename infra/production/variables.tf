variable "aws_region" {
  type    = string
  default = "us-east-1"
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
  description = "Fargate task memory for the backend, in MiB. 1024 vCPU units allows 2048-8192."
  type        = number
  default     = 2048
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

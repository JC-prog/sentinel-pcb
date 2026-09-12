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
  description = "Fargate task vCPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate task memory, in MiB."
  type        = number
  default     = 512
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

variable "litellm_container_port" {
  type    = number
  default = 4000
}

variable "litellm_image" {
  description = "LiteLLM proxy image. Pulled straight from ghcr.io (no ECR mirror) - pin a versioned -stable tag rather than main-stable for a real deploy."
  type        = string
  default     = "ghcr.io/berriai/litellm:main-stable"
}

variable "litellm_task_cpu" {
  description = "Fargate task vCPU units for the LiteLLM proxy (256 = 0.25 vCPU). The proxy is I/O-bound glue - it doesn't need much."
  type        = number
  default     = 256
}

variable "litellm_task_memory" {
  description = "Fargate task memory for the LiteLLM proxy, in MiB."
  type        = number
  default     = 512
}

variable "litellm_desired_count" {
  description = "Number of LiteLLM proxy tasks."
  type        = number
  default     = 1
}

variable "openai_api_key" {
  description = "Real OpenAI API key the LiteLLM proxy uses upstream. Required - supply via a non-committed *.tfvars or TF_VAR_openai_api_key. Never appears in an output or a plain task-def env var; stored only in the sentinelchat/litellm secret."
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

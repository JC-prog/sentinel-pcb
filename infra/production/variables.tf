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

"""SentinelChat's internal ONNX image-classification service.

Runs as a separate ECS Fargate task (infra/production/inference.tf), reachable only from the
backend over the VPC's private DNS. The caller names which of the loaded models to run; this
service does no model selection of its own.
"""

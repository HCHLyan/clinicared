# Inputs produced by the US 03 baseline (VPC, RDS, ECS cluster, ALB).
variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  description = "Private subnets in at least two AZs (no internet ingress)."
  type        = list(string)
}

variable "ecs_tasks_security_group_id" {
  description = "Security group attached to API/worker/Airflow tasks."
  type        = string
}

variable "rds_security_group_id" {
  type = string
}

variable "ecs_cluster_name" {
  type    = string
  default = "clinicared-prod"
}

variable "api_service_name" {
  type    = string
  default = "clinicared-api"
}

variable "alb_arn_suffix" {
  description = "e.g. app/clinicared-alb/1234567890abcdef"
  type        = string
}

variable "target_group_arn_suffix" {
  description = "e.g. targetgroup/clinicared-api/abcdef1234567890"
  type        = string
}

variable "alert_email" {
  type = string
}

variable "lake_bucket_name" {
  type = string
}

variable "artifacts_bucket_name" {
  type = string
}

variable "github_repository" {
  description = "owner/repo allowed to deploy through OIDC (main branch only)."
  type        = string
}

variable "create_github_oidc_provider" {
  description = "Set false if the account already has the GitHub OIDC provider."
  type        = bool
  default     = true
}

variable "mq_instance_type" {
  type    = string
  default = "mq.m5.large"
}

variable "mq_deployment_mode" {
  description = "CLUSTER_MULTI_AZ in prod; SINGLE_INSTANCE (with mq.t3.micro) in dev."
  type        = string
  default     = "CLUSTER_MULTI_AZ"
}

variable "emr_release_label" {
  type    = string
  default = "emr-7.5.0"
}

variable "log_retention_days" {
  type    = number
  default = 90
}

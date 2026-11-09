output "mq_broker_id" {
  value = aws_mq_broker.rabbit.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "emr_serverless_application_id" {
  value = aws_emrserverless_application.spark.id
}

output "emr_job_role_arn" {
  value = aws_iam_role.emr_job.arn
}

output "github_deploy_role_arn" {
  description = "Store as the AWS_DEPLOY_ROLE_ARN GitHub secret."
  value       = aws_iam_role.github_deploy.arn
}

output "alerts_topic_arn" {
  description = "Set as ALERTS_SNS_TOPIC_ARN for Airflow failure callbacks."
  value       = aws_sns_topic.alerts.arn
}

output "lake_bucket" {
  value = aws_s3_bucket.lake.bucket
}

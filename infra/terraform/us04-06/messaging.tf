# Amazon MQ for RabbitMQ: patient notifications (vhost "clinicared") and the
# Airflow Celery broker (vhost "airflow"). Private, TLS-only, multi-AZ cluster.
resource "aws_mq_broker" "rabbit" {
  broker_name                = "clinicared-rabbitmq"
  engine_type                = "RabbitMQ"
  engine_version             = "3.13"
  auto_minor_version_upgrade = true
  host_instance_type         = var.mq_instance_type
  deployment_mode            = var.mq_deployment_mode
  publicly_accessible        = false
  subnet_ids                 = var.mq_deployment_mode == "SINGLE_INSTANCE" ? [var.private_subnet_ids[0]] : var.private_subnet_ids
  security_groups            = [aws_security_group.mq.id]

  user {
    username = "clinicared"
    password = random_password.mq.result
  }

  logs {
    general = true
  }

  maintenance_window_start_time {
    day_of_week = "SUNDAY"
    time_of_day = "08:00" # 03:00 America/Bogota
    time_zone   = "UTC"
  }
}

resource "aws_ecr_repository" "api" {
  name                 = "clinicared/api"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.phi.arn
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

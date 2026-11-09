# --- Customer-managed KMS key for PHI data, logs and secrets --------------------
resource "aws_kms_key" "phi" {
  description             = "ClinicaRed PHI data (lake, logs, secrets)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/clinicared/*" } }
      }
    ]
  })
}

resource "aws_kms_alias" "phi" {
  name          = "alias/clinicared-phi"
  target_key_id = aws_kms_key.phi.key_id
}

# --- Secrets (values generated here, never in code; state is encrypted) ---------
resource "random_password" "mq" {
  length  = 32
  special = false
}

resource "random_password" "webhook" {
  length  = 48
  special = false
}

resource "random_password" "pepper" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "rabbitmq" {
  name       = "clinicared/rabbitmq"
  kms_key_id = aws_kms_key.phi.arn
}

resource "aws_secretsmanager_secret_version" "rabbitmq" {
  secret_id = aws_secretsmanager_secret.rabbitmq.id
  secret_string = jsonencode({
    username = "clinicared"
    password = random_password.mq.result
    url      = "amqps://clinicared:${random_password.mq.result}@${replace(aws_mq_broker.rabbit.instances[0].endpoints[0], "amqps://", "")}/clinicared"
  })
}

resource "aws_secretsmanager_secret" "webhook" {
  name       = "clinicared/webhook-signing-secret"
  kms_key_id = aws_kms_key.phi.arn
}

resource "aws_secretsmanager_secret_version" "webhook" {
  secret_id     = aws_secretsmanager_secret.webhook.id
  secret_string = random_password.webhook.result
}

# RN-09: losing the pepper breaks key continuity in the Gold layer -> never rotate
# without a re-keying plan; protected against accidental deletion.
resource "aws_secretsmanager_secret" "pepper" {
  name                    = "clinicared/pseudonymization-pepper"
  kms_key_id              = aws_kms_key.phi.arn
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "pepper" {
  secret_id     = aws_secretsmanager_secret.pepper.id
  secret_string = jsonencode({ pepper = random_password.pepper.result })
}

# Provider token is issued by the messaging vendor; the value is set out of band:
#   aws secretsmanager put-secret-value --secret-id clinicared/messaging-gateway-token --secret-string '<token>'
resource "aws_secretsmanager_secret" "gateway_token" {
  name       = "clinicared/messaging-gateway-token"
  kms_key_id = aws_kms_key.phi.arn
}

# --- Network isolation ----------------------------------------------------------
resource "aws_security_group" "mq" {
  name        = "clinicared-mq"
  description = "Amazon MQ: AMQPS only from ECS tasks"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "mq_from_tasks" {
  security_group_id            = aws_security_group.mq.id
  referenced_security_group_id = var.ecs_tasks_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5671
  to_port                      = 5671
  description                  = "AMQPS from ECS tasks (API, workers, Airflow)"
}

resource "aws_vpc_security_group_ingress_rule" "mq_console_from_tasks" {
  security_group_id            = aws_security_group.mq.id
  referenced_security_group_id = var.ecs_tasks_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Management API (runbook DLQ replay from a bastion task)"
}

resource "aws_security_group" "emr" {
  name        = "clinicared-emr-serverless"
  description = "EMR Serverless Spark jobs"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_egress_rule" "emr_all" {
  security_group_id = aws_security_group.emr.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "S3/Secrets Manager via VPC endpoints, RDS"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_emr" {
  security_group_id            = var.rds_security_group_id
  referenced_security_group_id = aws_security_group.emr.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "Spark JDBC extracts (clinicared_etl role, column-restricted)"
}

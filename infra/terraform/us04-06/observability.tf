# Centralized logging, alarms and dashboard (US 05 T6).
resource "aws_cloudwatch_log_group" "api" {
  name              = "/clinicared/api"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.phi.arn
}

resource "aws_cloudwatch_log_group" "workers" {
  name              = "/clinicared/workers"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.phi.arn
}

resource "aws_cloudwatch_log_group" "spark" {
  name              = "/clinicared/spark"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.phi.arn
}

resource "aws_sns_topic" "alerts" {
  name              = "clinicared-alerts"
  kms_master_key_id = aws_kms_key.phi.id
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 1) DEMONSTRATED FAILURE ALARM (runbook RB-01): anything in the reminder DLQ.
resource "aws_cloudwatch_metric_alarm" "reminders_dlq" {
  alarm_name          = "clinicared-reminders-dlq-not-empty"
  alarm_description   = "Reminder messages dead-lettered. Runbook: docs/runbook/operational_runbook.md#rb-01"
  namespace           = "ClinicaRed"
  metric_name         = "DlqDepth"
  dimensions          = { Service = "reminders-worker", Queue = "q.reminders.dispatch.dlq" }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching" # no datapoints = worker/monitor down = page as well
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "waitlist_dlq" {
  alarm_name          = "clinicared-waitlist-dlq-not-empty"
  alarm_description   = "Waitlist offers dead-lettered (RN-06 at risk). Runbook RB-01."
  namespace           = "ClinicaRed"
  metric_name         = "DlqDepth"
  dimensions          = { Service = "waitlist-worker", Queue = "q.waitlist.offers.dlq" }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# 2) API latency SLO: p95 <= 400 ms measured at the ALB.
resource "aws_cloudwatch_metric_alarm" "api_p95" {
  alarm_name          = "clinicared-api-p95-latency"
  alarm_description   = "API p95 > 400 ms for 10 min. Runbook RB-02."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.target_group_arn_suffix }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 2
  threshold           = 0.4
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "clinicared-api-5xx"
  alarm_description   = "More than 10 target 5xx in 5 min. Runbook RB-02."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.target_group_arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# 3) RN-06 SLA: slot released to the waitlist more than 10 minutes after cancellation.
resource "aws_cloudwatch_metric_alarm" "waitlist_sla" {
  alarm_name          = "clinicared-waitlist-release-sla"
  alarm_description   = "RN-06 breach: release latency > 600 s. Runbook RB-04."
  namespace           = "ClinicaRed"
  metric_name         = "WaitlistReleaseSeconds"
  dimensions          = { Service = "api" }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 600
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# 4) Outbox relay cannot publish (broker down / credentials rotated).
resource "aws_cloudwatch_metric_alarm" "outbox_failures" {
  alarm_name          = "clinicared-outbox-publish-failures"
  alarm_description   = "Outbox relay failing to publish. Runbook RB-03."
  namespace           = "ClinicaRed"
  metric_name         = "OutboxPublishFailures"
  dimensions          = { Service = "outbox-relay" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# 5) Provider rejecting messages at scale (permanent failures).
resource "aws_cloudwatch_metric_alarm" "dispatch_failed" {
  alarm_name          = "clinicared-dispatch-permanent-failures"
  alarm_description   = "More than 50 permanent provider failures in 15 min. Runbook RB-05."
  namespace           = "ClinicaRed"
  metric_name         = "DispatchOutcome"
  dimensions          = { Service = "reminders-worker", Queue = "reminders", Outcome = "FAILED" }
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 50
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# 6) Amazon MQ broker memory pressure (publishers get blocked above the watermark).
resource "aws_cloudwatch_metric_alarm" "mq_memory" {
  alarm_name          = "clinicared-mq-memory"
  alarm_description   = "RabbitMQ memory > 80% of its high watermark. Runbook RB-03."
  evaluation_periods  = 2
  threshold           = 0.8
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  metric_query {
    id          = "ratio"
    expression  = "used / limit"
    label       = "Memory used / limit"
    return_data = true
  }
  metric_query {
    id = "used"
    metric {
      namespace   = "AWS/AmazonMQ"
      metric_name = "RabbitMQMemUsed"
      dimensions  = { Broker = aws_mq_broker.rabbit.broker_name }
      stat        = "Maximum"
      period      = 300
    }
  }
  metric_query {
    id = "limit"
    metric {
      namespace   = "AWS/AmazonMQ"
      metric_name = "RabbitMQMemLimit"
      dimensions  = { Broker = aws_mq_broker.rabbit.broker_name }
      stat        = "Maximum"
      period      = 300
    }
  }
}

resource "aws_cloudwatch_dashboard" "ops" {
  dashboard_name = "clinicared-operations"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title   = "API latency p50/p95 (s)"
          region  = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", var.alb_arn_suffix, "TargetGroup", var.target_group_arn_suffix, { stat = "p50" }],
            ["...", { stat = "p95" }]
          ]
          annotations = { horizontal = [{ value = 0.4, label = "SLO p95" }] }
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title   = "Queues and DLQs"
          region  = var.aws_region
          metrics = [
            ["ClinicaRed", "QueueDepth", "Service", "reminders-worker", "Queue", "q.reminders.dispatch"],
            ["ClinicaRed", "DlqDepth", "Service", "reminders-worker", "Queue", "q.reminders.dispatch.dlq"],
            ["ClinicaRed", "DlqDepth", "Service", "waitlist-worker", "Queue", "q.waitlist.offers.dlq"]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title   = "Dispatch outcomes (reminders)"
          region  = var.aws_region
          stat    = "Sum"
          metrics = [for o in ["SENT", "DUPLICATE", "SKIPPED", "DEFERRED", "RETRY", "FAILED"] : ["ClinicaRed", "DispatchOutcome", "Service", "reminders-worker", "Queue", "reminders", "Outcome", o]]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title   = "RN-06 release latency (s)"
          region  = var.aws_region
          metrics = [["ClinicaRed", "WaitlistReleaseSeconds", "Service", "api", { stat = "Maximum" }]]
          annotations = { horizontal = [{ value = 600, label = "SLA 10 min" }] }
        }
      }
    ]
  })
}

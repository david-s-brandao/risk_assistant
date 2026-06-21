resource "aws_sns_topic" "billing_alerts" {
  provider = aws.us_east_1
  name     = "${local.prefix}-billing-alerts"
}

resource "aws_sns_topic_subscription" "billing_alerts_email" {
  provider  = aws.us_east_1
  topic_arn = aws_sns_topic.billing_alerts.arn
  protocol  = "email"
  endpoint  = var.billing_alert_email
}

resource "aws_cloudwatch_metric_alarm" "billing_alarm" {
  provider            = aws.us_east_1
  alarm_name          = "${local.prefix}-billing-alarm-3-usd"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = "1"
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = "21600" # 6 hours
  statistic           = "Maximum"
  threshold           = var.billing_threshold
  alarm_description   = "Billing alarm when estimated charges exceed ${var.billing_threshold} USD"
  alarm_actions       = [aws_sns_topic.billing_alerts.arn]

  dimensions = {
    Currency = "USD"
  }
}

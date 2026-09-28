resource "aws_sqs_queue" "analyze_dlq" {
  name                      = "${local.prefix}-analyze-dlq"
  message_retention_seconds = 604800 # 7 days for recovery
}

resource "aws_sqs_queue" "analyze" {
  name                       = "${local.prefix}-analyze-queue"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 86400 # 1 day for pending analysis requests

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.analyze_dlq.arn
    maxReceiveCount     = 5
  })
}

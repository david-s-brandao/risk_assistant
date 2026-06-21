resource "aws_sqs_queue" "analyze_dlq" {
  name                      = "${local.prefix}-analyze-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "analyze" {
  name                       = "${local.prefix}-analyze-queue"
  visibility_timeout_seconds = 120

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.analyze_dlq.arn
    maxReceiveCount     = 5
  })
}

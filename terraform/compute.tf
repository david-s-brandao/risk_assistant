# ── Placeholder zips (replace with real build artifacts) ──────────────────────
data "archive_file" "orchestrator" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/orchestrator"
  output_path = "${path.module}/.build/orchestrator.zip"
}

data "archive_file" "validator" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/validator"
  output_path = "${path.module}/.build/validator.zip"
}

data "archive_file" "worker" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/worker"
  output_path = "${path.module}/.build/worker.zip"
}

data "archive_file" "image_worker" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/image_worker"
  output_path = "${path.module}/.build/image_worker.zip"
}

# ── Orchestrator ───────────────────────────────────────────────────────────────
resource "aws_lambda_function" "orchestrator" {
  function_name    = "${local.prefix}-orchestrator"
  role             = aws_iam_role.orchestrator.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.orchestrator.output_path
  source_code_hash = data.archive_file.orchestrator.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      USERS_TABLE         = aws_dynamodb_table.users.name
      DEVICES_TABLE       = aws_dynamodb_table.devices.name
      PARENTAL_TABLE      = aws_dynamodb_table.parental_control.name
      URL_CACHE_TABLE     = aws_dynamodb_table.url_cache.name
      CONTENT_CACHE_TABLE = aws_dynamodb_table.content_cache.name
      JOBS_TABLE          = aws_dynamodb_table.analysis_jobs.name
      ANALYZE_QUEUE_URL   = aws_sqs_queue.analyze.url
      VALIDATOR_FUNCTION  = aws_lambda_function.validator.function_name
      IMAGE_BUCKET        = aws_s3_bucket.image_uploads.bucket
    }
  }
}

# ── Validator ──────────────────────────────────────────────────────────────────
resource "aws_lambda_function" "validator" {
  function_name    = "${local.prefix}-validator"
  role             = aws_iam_role.validator.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.validator.output_path
  source_code_hash = data.archive_file.validator.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      BEDROCK_MODEL_ID = var.bedrock_model_id
      # Keep Bedrock Agent active (preferred path). Validator will fall back to
      # direct model calls if the agent invocation fails.
      BEDROCK_AGENT_ID       = aws_bedrockagent_agent.risk_summary.agent_id
      BEDROCK_AGENT_ALIAS_ID = aws_bedrockagent_agent_alias.risk_summary.agent_alias_id
    }
  }
}

# ── Worker (SQS consumer) ─────────────────────────────────────────────────────
resource "aws_lambda_function" "worker" {
  function_name    = "${local.prefix}-worker"
  role             = aws_iam_role.worker.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.worker.output_path
  source_code_hash = data.archive_file.worker.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      URL_CACHE_TABLE     = aws_dynamodb_table.url_cache.name
      CONTENT_CACHE_TABLE = aws_dynamodb_table.content_cache.name
      JOBS_TABLE          = aws_dynamodb_table.analysis_jobs.name
      VALIDATOR_FUNCTION  = aws_lambda_function.validator.function_name
      # Bump to invalidate DynamoDB cache entries when validator logic/models change.
      CACHE_VERSION = "v6"
    }
  }
}

# ── Image Worker (S3 consumer) ──────────────────────────────────────────────────
resource "aws_lambda_function" "image_worker" {
  function_name    = "${local.prefix}-image-worker"
  role             = aws_iam_role.image_worker.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.image_worker.output_path
  source_code_hash = data.archive_file.image_worker.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      JOBS_TABLE         = aws_dynamodb_table.analysis_jobs.name
      VALIDATOR_FUNCTION = aws_lambda_function.validator.function_name
    }
  }
}

resource "aws_lambda_event_source_mapping" "analyze_queue" {
  event_source_arn = aws_sqs_queue.analyze.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 10
}

# ── CloudWatch Log Groups ──────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/aws/lambda/${aws_lambda_function.orchestrator.function_name}"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "validator" {
  name              = "/aws/lambda/${aws_lambda_function.validator.function_name}"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${aws_lambda_function.worker.function_name}"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "image_worker" {
  name              = "/aws/lambda/${aws_lambda_function.image_worker.function_name}"
  retention_in_days = 7
}

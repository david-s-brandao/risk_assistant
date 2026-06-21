data "aws_caller_identity" "current" {}

# ── Orchestrator Lambda ────────────────────────────────────────────────────────
resource "aws_iam_role" "orchestrator" {
  name = "${local.prefix}-orchestrator-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "orchestrator" {
  name = "orchestrator-policy"
  role = aws_iam_role.orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"]
        Resource = [
          aws_dynamodb_table.users.arn,
          aws_dynamodb_table.devices.arn,
          "${aws_dynamodb_table.devices.arn}/index/*",
          aws_dynamodb_table.parental_control.arn,
          aws_dynamodb_table.url_cache.arn,
          aws_dynamodb_table.content_cache.arn,
          aws_dynamodb_table.analysis_jobs.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.analyze.arn
      },
      {
        Effect = "Allow"
        Action = ["s3:PutObject"]
        Resource = [
          "${aws_s3_bucket.image_uploads.arn}/uploads/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.validator.arn
      }
    ]
  })
}

# ── Image Worker Lambda (S3 consumer + Textract) ───────────────────────────────
resource "aws_iam_role" "image_worker" {
  name = "${local.prefix}-image-worker-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "image_worker" {
  name = "image-worker-policy"
  role = aws_iam_role.image_worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.image_uploads.arn}/uploads/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["textract:DetectDocumentText"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["rekognition:DetectText"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = [
          aws_dynamodb_table.analysis_jobs.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.validator.arn
      }
    ]
  })
}

# ── Worker Lambda (SQS consumer) ──────────────────────────────────────────────
resource "aws_iam_role" "worker" {
  name = "${local.prefix}-worker-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "worker" {
  name = "worker-policy"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.analyze.arn
      },
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.url_cache.arn,
          aws_dynamodb_table.content_cache.arn,
          aws_dynamodb_table.analysis_jobs.arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.validator.arn
      }
    ]
  })
}

# ── Validator Lambda ───────────────────────────────────────────────────────────
resource "aws_iam_role" "validator" {
  name = "${local.prefix}-validator-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "validator" {
  name = "validator-policy"
  role = aws_iam_role.validator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeAgent"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        # Model ARNs vary by provider (e.g. Amazon Nova is not under `foundation-model/`).
        # Keep broad for hackathon usage; restrict later via IAM/SCP if needed.
        Resource = "*"
      }
    ]
  })
}

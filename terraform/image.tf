# ── Image Upload Bucket (presigned PUT) ─────────────────────────────────────────
resource "aws_s3_bucket" "image_uploads" {
  # S3 bucket names are global; include account id to avoid collisions.
  bucket        = "${local.prefix}-${data.aws_caller_identity.current.account_id}-image-uploads"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "image_uploads" {
  bucket                  = aws_s3_bucket.image_uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "image_uploads" {
  bucket = aws_s3_bucket.image_uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Trigger ImageWorker on upload
resource "aws_lambda_permission" "s3_invoke_image_worker" {
  statement_id  = "AllowS3InvokeImageWorker"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.image_worker.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.image_uploads.arn
}

resource "aws_s3_bucket_notification" "image_uploads" {
  bucket = aws_s3_bucket.image_uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.image_worker.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  depends_on = [aws_lambda_permission.s3_invoke_image_worker]
}

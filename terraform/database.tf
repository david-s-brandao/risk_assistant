# ── Users ──────────────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "users" {
  name         = "${local.prefix}-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"

  attribute {
    name = "userId"
    type = "S"
  }
}

# ── Devices ────────────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "devices" {
  name         = "${local.prefix}-devices"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "deviceId"

  attribute {
    name = "deviceId"
    type = "S"
  }
  attribute {
    name = "userId"
    type = "S"
  }

  global_secondary_index {
    name            = "userId-index"
    hash_key        = "userId"
    projection_type = "ALL"
  }
}

# ── ParentalControl ────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "parental_control" {
  name         = "${local.prefix}-parental-control"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"

  attribute {
    name = "userId"
    type = "S"
  }
}

# ── UrlCache ───────────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "url_cache" {
  name         = "${local.prefix}-url-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "urlHash"

  attribute {
    name = "urlHash"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }
}

# ── ContentCache ───────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "content_cache" {
  name         = "${local.prefix}-content-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "contentHash"

  attribute {
    name = "contentHash"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }
}

# ── AnalysisJobs (async processing) ────────────────────────────────────────────
resource "aws_dynamodb_table" "analysis_jobs" {
  name         = "${local.prefix}-analysis-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"
  range_key    = "jobId"

  attribute {
    name = "userId"
    type = "S"
  }
  attribute {
    name = "jobId"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }
}

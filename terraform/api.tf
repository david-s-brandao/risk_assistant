# ── Cognito User Pool ──────────────────────────────────────────────────────────
resource "aws_cognito_user_pool" "main" {
  name = "${local.prefix}-user-pool"

  password_policy {
    minimum_length    = 8
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  auto_verified_attributes = ["email"]
}

resource "aws_cognito_user_pool_client" "mobile" {
  name         = "${local.prefix}-mobile"
  user_pool_id = aws_cognito_user_pool.main.id

  # Public client (mobile app) should not have a client secret.
  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    # Allow direct username/password auth for simple mobile demos.
    # Prefer SRP/Hosted UI in production.
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.prefix}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  # Public client (browser/extension) should not have a client secret.
  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["email", "openid", "profile"]
  callback_urls                        = [var.web_callback_url]
  logout_urls                          = [var.web_logout_url]
  supported_identity_providers         = ["COGNITO"]

  explicit_auth_flows = [
    # Used by our extension/dev UI to exchange email+password for tokens.
    # (Hosted UI / SRP would avoid this, but this is simplest for hackathon.)
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}

# CLI test client (username/password auth) for quick API testing
resource "aws_cognito_user_pool_client" "cli_test" {
  name         = "${local.prefix}-cli-test"
  user_pool_id = aws_cognito_user_pool.main.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}

# ── API Gateway ────────────────────────────────────────────────────────────────
resource "aws_api_gateway_rest_api" "main" {
  name = "${local.prefix}-api"
}

resource "aws_api_gateway_authorizer" "cognito" {
  name            = "cognito-authorizer"
  rest_api_id     = aws_api_gateway_rest_api.main.id
  type            = "COGNITO_USER_POOLS"
  identity_source = "method.request.header.Authorization"
  provider_arns   = [aws_cognito_user_pool.main.arn]
}

# /analyze resource
resource "aws_api_gateway_resource" "analyze" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "analyze"
}

# /history resource
resource "aws_api_gateway_resource" "history" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "history"
}

# /result/{jobId} resource
resource "aws_api_gateway_resource" "result" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "result"
}

resource "aws_api_gateway_resource" "result_job" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.result.id
  path_part   = "{jobId}"
}

# /image resource
resource "aws_api_gateway_resource" "image" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "image"
}

# /image/upload-url resource
resource "aws_api_gateway_resource" "image_upload_url" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.image.id
  path_part   = "upload-url"
}

resource "aws_api_gateway_method" "image_upload_url_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.image_upload_url.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "image_upload_url_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.image_upload_url.id
  http_method             = aws_api_gateway_method.image_upload_url_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orchestrator.invoke_arn
}

resource "aws_api_gateway_method" "analyze_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.analyze.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "history_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.history.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "result_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.result_job.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "analyze_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.analyze.id
  http_method             = aws_api_gateway_method.analyze_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orchestrator.invoke_arn
}

resource "aws_api_gateway_integration" "history_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.history.id
  http_method             = aws_api_gateway_method.history_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orchestrator.invoke_arn
}

resource "aws_api_gateway_integration" "result_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.result_job.id
  http_method             = aws_api_gateway_method.result_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orchestrator.invoke_arn
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orchestrator.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.analyze.id,
      aws_api_gateway_resource.history.id,
      aws_api_gateway_resource.result.id,
      aws_api_gateway_resource.result_job.id,
      aws_api_gateway_resource.image.id,
      aws_api_gateway_resource.image_upload_url.id,
      aws_api_gateway_method.analyze_post.id,
      aws_api_gateway_method.history_get.id,
      aws_api_gateway_method.result_get.id,
      aws_api_gateway_method.image_upload_url_post.id,
      aws_api_gateway_integration.analyze_post.id,
      aws_api_gateway_integration.history_get.id,
      aws_api_gateway_integration.result_get.id,
      aws_api_gateway_integration.image_upload_url_post.id,
    ]))
  }

  lifecycle { create_before_destroy = true }
}


resource "aws_api_gateway_stage" "main" {
  deployment_id = aws_api_gateway_deployment.main.id
  rest_api_id   = aws_api_gateway_rest_api.main.id
  stage_name    = var.env
}

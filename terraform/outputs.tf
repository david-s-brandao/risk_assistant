output "api_gateway_url" {
  value = aws_api_gateway_stage.main.invoke_url
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_mobile_client_id" {
  value = aws_cognito_user_pool_client.mobile.id
}

output "cognito_web_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_cli_test_client_id" {
  value = aws_cognito_user_pool_client.cli_test.id
}

output "bedrock_agent_id" {
  value = aws_bedrockagent_agent.risk_summary.agent_id
}

output "bedrock_agent_alias_id" {
  value = aws_bedrockagent_agent_alias.risk_summary.agent_alias_id
}

output "image_upload_bucket" {
  value = aws_s3_bucket.image_uploads.bucket
}

resource "aws_iam_role" "bedrock_agent" {
  name = "${local.prefix}-bedrock-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_agent" {
  name = "bedrock-agent-policy"
  role = aws_iam_role.bedrock_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        # Note: some model IDs (e.g. Amazon Nova) are not under the `foundation-model/` ARN path.
        # Keep this broad for hackathon usage; restrict via SCP/IAM later if needed.
        Resource = "*"
      }
    ]
  })
}

resource "aws_bedrockagent_agent" "risk_summary" {
  agent_name              = "${local.prefix}-risk-summary"
  agent_resource_role_arn = aws_iam_role.bedrock_agent.arn
  foundation_model        = var.bedrock_model_id
  prepare_agent           = true

  instruction = <<EOT
You are a security analyst. Analyze the provided input and return ONLY a JSON object.
The JSON must include: score (0-100), decision (allow|review|block), category,
confidence (low|medium|high), triggers (array of strings), summary, details (object),
recommended_action, generated_at (ISO-8601 UTC). Use clear English.
Use the user context and the content when forming the summary and recommendation.
Adjust the strictness of your decision based on the user's risk sensitivity (low, medium, high).
If the input is a URL, focus on link risk. If it is an email address, focus on impersonation risk.
If it is a message, focus on social engineering risk.
EOT
}

resource "aws_bedrockagent_agent_alias" "risk_summary" {
  agent_alias_name = "${local.prefix}-risk-summary"
  agent_id         = aws_bedrockagent_agent.risk_summary.agent_id
}

# Risk Assistant - AI-Powered Threat Analysis Platform

**Risk Assistant** is a project built by a team during a **48-hour hackathon** focused on the frontend. We delivered a working app and chose to build a cloud backend and architecture as well to make the project more distinctive, even though the backend was **not part of the evaluation**. The app is **no longer available**; this repository preserves the AWS backend and infrastructure as code, not a live product or the app's source code.

The backend analyzes URLs, email addresses, messages, and images for potential threats. It produces an AI-generated risk score (0 to 100), a decision (`allow` / `review` / `block`), and an explanation with a suggested next step.

### My contribution

I was the **sole contributor to the cloud architecture**. My work focused primarily on designing and implementing the AWS infrastructure and serverless backend documented here: Terraform provisioning, authenticated API, asynchronous processing, data storage, image-analysis pipeline, and Bedrock integration. The functional app was a **team deliverable**, not something I built alone.

### Architecture review: what I would change

This is a **hackathon prototype, not a production-ready security service**. The serverless design let us deliver an authenticated, asynchronous end-to-end flow in 48 hours without operating servers. With more time, I would prioritize:

1. **Fail safely:** Bedrock failures or malformed responses can currently produce an `allow` verdict. I would distinguish unavailable analysis from low risk, validate model output, and cache only valid verdicts.
2. **Make jobs recoverable:** SQS has a dead-letter queue, but batch retries can repeat work and jobs can remain pending. I would make processing idempotent, report partial batch failures, and monitor/reconcile stuck jobs.
3. **Harden security and data handling:** Cognito protects the API and the current backend uses DynamoDB rather than SQL queries, but analyzed content is untrusted input to the AI model. I would test prompt injection, tighten IAM, enforce request and upload limits, and define deletion and incident-response policies. Short TTLs reduce retention; they do not prevent a data breach.
4. **Prove quality and operability:** Add automated tests, classification evaluations, metrics and alerts before relying on risk scores for real users.

These are **proposed changes, not protections already implemented**. The [full architecture analysis](docs/architecture-analysis.md) explains the current controls, specific gaps, retention choices, and priorities.

---

## How It Works

In the hackathon, users submitted content through the app. The backend accepts text, URLs, and email addresses through an authenticated API, creates an analysis job, and queues it in SQS. A Lambda worker checks the DynamoDB cache, calls a validator backed by Amazon Bedrock (Mistral Mixtral 8x7B) on a cache miss, and saves the result for polling. Images follow a separate S3-triggered pipeline using Textract for text extraction, with Rekognition as a fallback if Textract is unavailable due to subscription restrictions.

Illustrative analysis result:

```json
{
  "score": 87,
  "decision": "block",
  "category": "phishing",
  "confidence": "high",
  "triggers": ["brand_impersonation", "credential_collection", "suspicious_domain"],
  "summary": "This link mimics a legitimate login page to harvest credentials.",
  "simple_explanation": "This looks unsafe, it may steal your login details.",
  "safe_next_step": "Open the official app instead.",
  "recommended_action": "Do not enter personal information on this website.",
  "generated_at": "2026-05-23T15:42:10Z"
}
```

---

## Architecture

Serverless, event-driven AWS architecture defined with Terraform. The diagram illustrates the hackathon solution; deployment instructions below describe how to provision the infrastructure in your own account, not a currently hosted service.

<p align="center">
  <img src="img/architecture_diagram.png" width="90%" alt="Architecture Diagram">
</p>

### Lambda Functions

| Function | Runtime | Role |
|---|---|---|
| **Orchestrator** | Python 3.12 | API entry point. Handles authenticated requests, creates jobs, enqueues text analyses in SQS, and generates image upload URLs |
| **Worker** | Python 3.12 | SQS consumer (batch size 10). Checks cache, calls Validator, writes results |
| **Validator** | Python 3.12 | AI layer. Invokes Bedrock Agent; falls back to direct model call on failure |
| **Image Worker** | Python 3.12 | S3 event consumer. Extracts image text with Textract (Rekognition fallback), then calls Validator |

### API Endpoints

All endpoints require a valid Cognito JWT in the `Authorization` header.

| Method | Path | Description |
|---|---|---|
| `POST` | `/analyze` | Submit a URL, email address, or message |
| `GET` | `/result/{jobId}` | Poll for an async job result |
| `GET` | `/history` | Fetch past analyses for the authenticated user |
| `POST` | `/image/upload-url` | Get a presigned S3 URL to upload an image for analysis |

### DynamoDB Tables

| Table | Key | Purpose |
|---|---|---|
| `users` | `userId` | User profiles |
| `devices` | `deviceId` (GSI: `userId`) | Registered devices per user |
| `parental-control` | `userId` | Per-user risk sensitivity settings (`low` / `medium` / `high`) |
| `url-cache` | `urlHash` + TTL | Cached results for URL inputs |
| `content-cache` | `contentHash` + TTL | Cached results for message/text inputs |
| `analysis-jobs` | `userId` + `jobId` + TTL | Async job state, results, and per-user history |

### Caching

For text-based analyses, the Worker hashes the cache version, input type, value, and risk sensitivity. On a valid cache hit it reuses the result; otherwise it calls the Validator and caches the response for **one hour**. The Worker checks the expiry timestamp on read because DynamoDB TTL removal is asynchronous. Bumping the Worker's `CACHE_VERSION` changes the cache key and bypasses previous entries without deleting them. Image analyses use a separate pipeline and do not use this cache. Analysis jobs have a **seven-day TTL**; uploaded images are configured to expire after **one day** via S3 lifecycle (physical deletion may happen later).

### AI Agent

A Bedrock Agent (`risk-summary`) backed by Mistral Mixtral 8x7B. Its system prompt instructs it to act as a security analyst and return a strictly structured JSON response. It adapts the strictness of its decision based on the user's `riskSensitivity` setting from the parental control table:

- URL input → focuses on link/domain risk
- Email address → focuses on impersonation risk  
- Message → focuses on social engineering risk
- Image → text extracted first, then the same logic applies

The Validator Lambda holds the agent ID/alias and falls back to a direct `InvokeModel` call if the agent invocation fails.

---

## Project Structure

```
risk_assistant/
├── terraform/           # IaC — all AWS resources
│   ├── main.tf          # Provider and locals
│   ├── api.tf           # Cognito + API Gateway
│   ├── compute.tf       # Lambda functions
│   ├── database.tf      # DynamoDB tables
│   ├── queue.tf         # SQS queues
│   ├── bedrock.tf       # Bedrock Agent
│   ├── image.tf         # S3 image bucket + notifications
│   ├── iam.tf           # IAM roles and policies
│   ├── billing.tf       # CloudWatch billing alarm
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── lambda/
│   ├── orchestrator/    # API handler
│   ├── worker/          # SQS consumer
│   ├── validator/       # Bedrock AI layer
│   └── image_worker/    # S3 image pipeline
├── img/
│   └── architecture_diagram.png
└── docs/
    ├── data-model.md           # DynamoDB table schemas and example items
    └── architecture-analysis.md  # Architecture review and next steps
```

---

## Deployment

The original hackathon app is no longer available. These steps are for provisioning the backend yourself; they do not restore the original app or provide a public demo.

### Prerequisites

- AWS CLI authenticated with sufficient permissions
- Terraform ≥ 1.5
- Python 3.12+ (Lambda runtime; also used locally for packaging)
- Bedrock model access enabled in your account for the chosen model

### 1. Configure variables

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
aws_region          = "eu-west-1"
project             = "devunleashed"
env                 = "dev"
bedrock_model_id    = "mistral.mixtral-8x7b-instruct-v0:1"
web_callback_url    = "https://<YOUR_EXT_ID>.chromiumapp.org/"
web_logout_url      = "https://<YOUR_EXT_ID>.chromiumapp.org/"
billing_alert_email = "you@example.com"
billing_threshold   = "3"
```

> **Bedrock model access:** Go to AWS Console -> Bedrock -> Model access and enable the model for your account and region before deploying.

### 2. Deploy

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

Terraform outputs the API Gateway URL and all Cognito client IDs needed to configure your clients.

### 3. Create a test user

```bash
aws cognito-idp sign-up \
  --client-id <cognito_cli_test_client_id> \
  --username you@example.com \
  --password "YourPassword1!" \
  --region eu-west-1

aws cognito-idp admin-confirm-sign-up \
  --user-pool-id <cognito_user_pool_id> \
  --username you@example.com \
  --region eu-west-1
```

### 4. Get a token and call the API

```bash
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id <cognito_cli_test_client_id> \
  --auth-parameters USERNAME=you@example.com,PASSWORD="YourPassword1!" \
  --region eu-west-1 \
  --query 'AuthenticationResult.IdToken' \
  --output text)

API_URL=$(terraform output -raw api_gateway_url)

curl -X POST "$API_URL/analyze" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://suspicious-login-page.com"}'
```

Run this from the `terraform/` directory. The Terraform output includes the stage path. The response includes a `jobId`; poll for the result:

```bash
curl "$API_URL/result/<jobId>" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Authentication

Three Cognito app clients are provisioned:

| Client | Flow | Use case |
|---|---|---|
| `mobile` | SRP + password | Native mobile app |
| `web` | OAuth2 PKCE (code flow) | Browser extension / web UI |
| `cli-test` | Password | Local development and testing |

---

## Teardown

```bash
cd terraform
terraform destroy
```

This will remove all provisioned resources. The S3 image bucket has `force_destroy = true` so it will be deleted even if it contains objects.

---

## Data Model

See [`docs/data-model.md`](docs/data-model.md) for full DynamoDB table schemas and example item shapes.

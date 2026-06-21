# Data Model

This document describes the DynamoDB tables and expected item shapes used by the backend.

> Auth note: the API Gateway is protected by a Cognito authorizer. If a request is missing/invalid `Authorization: Bearer <JWT>`, API Gateway returns an auth error (typically `401`/`403`) and the Lambda functions are not invoked.

## Users Table (`${project}-${env}-users`)
Primary key: `userId` (S)

Example item:
```json
{
  "userId": "d2a3d6b9-0f42-4d5b-a3b1-7f3f7a9c1f11",
  "email": "user@example.com",
  "displayName": "Joana Silva",
  "createdAt": "2026-05-23T12:00:00Z",
  "status": "active"
}
```

## Devices Table (`${project}-${env}-devices`)
Primary key: `deviceId` (S) | GSI: `userId-index`

Example item:
```json
{
  "deviceId": "android:3b6d9e0a-3a1f-4b2b-9b7c-9ccf56c1f1aa",
  "userId": "d2a3d6b9-0f42-4d5b-a3b1-7f3f7a9c1f11",
  "deviceType": "android",
  "appVersion": "1.4.2",
  "registeredAt": "2026-05-23T12:10:00Z"
}
```

## Parental Control Table (`${project}-${env}-parental-control`)
Primary key: `userId` (S)

Example item:
```json
{
  "userId": "d2a3d6b9-0f42-4d5b-a3b1-7f3f7a9c1f11",
  "enabled": true,
  "blockCategories": ["phishing", "malware", "scam"],
  "updatedAt": "2026-05-23T12:15:00Z"
}
```

## URL Cache Table (`${project}-${env}-url-cache`)
Primary key: `urlHash` (S) | TTL: `expiresAt`

Example item:
```json
{
  "urlHash": "7b6f58d3f9e0d1b3e1af82f9b6a4a2f7ddfa2c62f9d4f0d2c5b2a312b2f40b2a",
  "result": {
    "score": 87,
    "decision": "block",
    "category": "phishing",
    "confidence": "high",
    "triggers": [
      "brand_impersonation",
      "credential_collection",
      "urgent_language",
      "suspicious_domain"
    ],
    "summary": "This link appears to mimic a legitimate login page to harvest credentials.",
    "simple_explanation": "This looks unsafe because it tries to trick you into signing in.",
    "safe_next_step": "Open the official app instead.",
    "policy_reason": "Blocked because the risk score is high and phishing indicators were detected.",
    "ai_reasoning_steps": [
      "Detected urgent language.",
      "Detected a suspicious link.",
      "Detected possible brand impersonation.",
      "Classified as likely phishing."
    ],
    "details": {
      "domain_age_days": 3,
      "contains_login_keywords": true,
      "uses_https": true,
      "suspicious_tld": false
    },
    "recommended_action": "Do not enter personal information or passwords on this website.",
    "generated_at": "2026-05-23T15:42:10Z"
  },
  "expiresAt": 1784892130,
  "updatedAt": "2026-05-23T15:42:10Z"
}
```

## Content Cache Table (`${project}-${env}-content-cache`)
Primary key: `contentHash` (S) | TTL: `expiresAt`

Example item:
```json
{
  "contentHash": "4b9d7a42f2d1e8f9c3cbbcd7c3a6c09a5c03c7b9f60d2a7a97d0f5ed6f12c51a",
  "result": {
    "score": 52,
    "decision": "review",
    "category": "scam",
    "confidence": "medium",
    "triggers": ["pressure", "payment_request"],
    "summary": "The message asks for payment using urgent language.",
    "simple_explanation": "This may be a scam because it pressures you to pay quickly.",
    "safe_next_step": "Confirm with the person using a known phone number.",
    "policy_reason": "Review because the risk score is moderate and scam indicators were detected.",
    "ai_reasoning_steps": [
      "Detected urgent language.",
      "Detected a payment request.",
      "Classified as possible scam."
    ],
    "details": {
      "contains_payment_request": true
    },
    "recommended_action": "Verify the sender through an official channel before taking action.",
    "generated_at": "2026-05-23T15:44:10Z"
  },
  "expiresAt": 1784892250,
  "updatedAt": "2026-05-23T15:44:10Z"
}
```

## Risk Events Table (`${project}-${env}-risk-events`)
Primary key: `userId` (S) | Sort key: `eventId` (S)

TTL: `expiresAt` (N, epoch seconds). Items are written with ~30 days retention.

Notes:
- `userId` comes from the Cognito JWT claim `sub`.
- `eventId` is time-sortable (format: `${epochSeconds}-${uuid}`) so descending queries return newest first.

Example item:
```json
{
  "userId": "d2a3d6b9-0f42-4d5b-a3b1-7f3f7a9c1f11",
  "eventId": "1763902930-0e12aaf6-2e6f-4b6a-9d2e-7f94e74e9b2a",
  "targetType": "url",
  "targetValue": "https://example-login-secure.com",
  "riskSensitivity": "high",
  "result": {
    "score": 87,
    "decision": "block",
    "category": "phishing",
    "confidence": "high",
    "triggers": ["brand_impersonation", "credential_collection"],
    "summary": "This link appears to mimic a legitimate login page to harvest credentials.",
    "simple_explanation": "This looks unsafe because it may steal your login details.",
    "safe_next_step": "Open the official app instead.",
    "policy_reason": "Blocked because the risk score is high and phishing indicators were detected.",
    "ai_reasoning_steps": [
      "Detected a suspicious domain.",
      "Detected credential collection intent.",
      "Classified as likely phishing."
    ],
    "details": {
      "domain_age_days": 3
    },
    "recommended_action": "Do not enter personal information or passwords on this website.",
    "generated_at": "2026-05-23T15:42:10Z"
  },
  "createdAt": "2026-05-23T15:42:10Z",
  "expiresAt": 1766494930
}
```

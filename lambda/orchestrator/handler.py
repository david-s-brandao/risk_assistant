import json
import os
import re
import time
import uuid
from decimal import Decimal
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

_dynamodb = boto3.resource("dynamodb")
_lambda = boto3.client("lambda")
_sqs = boto3.client("sqs")
_s3 = boto3.client("s3")


def _response(status_code, payload):
    def _json_default(o):
        # DynamoDB returns numbers as Decimal; convert to int/float for JSON.
        if isinstance(o, Decimal):
            return int(o) if o % 1 == 0 else float(o)
        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=_json_default),
    }


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_json(body):
    if body is None:
        return None
    if isinstance(body, dict):
        return body
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _sanitize_value(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value else None


def _normalize_target_type(value):
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in {"url", "link"}:
        return "url"
    if normalized in {"email", "email_address"}:
        return "email"
    if normalized in {"message", "text", "content"}:
        return "message"
    return None


def _normalize_risk_sensitivity(value):
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    if normalized in {"sensitive_low", "sensitivity_low", "low_sensitivity"}:
        return "low"
    if normalized in {"sensitive_medium", "sensitivity_medium", "medium_sensitivity"}:
        return "medium"
    if normalized in {"sensitive_high", "sensitivity_high", "high_sensitivity"}:
        return "high"
    return None


def _looks_like_email(value):
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value) is not None


def _looks_like_url(value):
    lower = value.lower()
    if lower.startswith(("http://", "https://", "www.")):
        return True
    if " " in value or "@" in value:
        return False
    return "." in value


def _infer_target(value, declared_type=None):
    value = _sanitize_value(value)
    if not value:
        return None, None
    normalized = _normalize_target_type(declared_type)
    if normalized:
        return normalized, value
    if _looks_like_email(value):
        return "email", value
    if _looks_like_url(value):
        return "url", value
    return "message", value


def _extract_risk_sensitivity(body):
    if not isinstance(body, dict):
        return None
    for key in ["risk_sensitivity", "riskSensitivity", "sensitivity", "risk"]:
        if key in body:
            return _normalize_risk_sensitivity(body.get(key))
    return None


def _hash_key(value):
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_env():
    return {
        "users_table": os.environ["USERS_TABLE"],
        "devices_table": os.environ["DEVICES_TABLE"],
        "parental_table": os.environ["PARENTAL_TABLE"],
        "url_cache_table": os.environ["URL_CACHE_TABLE"],
        "content_cache_table": os.environ["CONTENT_CACHE_TABLE"],
        "jobs_table": os.environ["JOBS_TABLE"],
        "analyze_queue_url": os.environ["ANALYZE_QUEUE_URL"],
        "validator_function": os.environ["VALIDATOR_FUNCTION"],
        # Optional: only required for the image flow.
        "image_bucket": os.environ.get("IMAGE_BUCKET"),
    }


def _get_path(event):
    # API Gateway REST proxy events provide both `path` and `resource`.
    # We prioritize the concrete path.
    return (event.get("path") or event.get("resource") or "").strip() or "/"


def _presigned_put_url(bucket, key, content_type=None, expires_in=900):
    params = {"Bucket": bucket, "Key": key}
    if isinstance(content_type, str) and content_type.strip():
        params["ContentType"] = content_type.strip()
    return _s3.generate_presigned_url(
        ClientMethod="put_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def _create_job(jobs_table_name, user_id, job_id, target_type, target_value, risk_sensitivity):
    table = _dynamodb.Table(jobs_table_name)
    now_iso = _now_iso()
    table.put_item(
        Item={
            "userId": user_id,
            "jobId": job_id,
            "status": "PENDING",
            "targetType": target_type,
            "targetValue": target_value,
            "riskSensitivity": risk_sensitivity,
            "createdAt": now_iso,
            "updatedAt": now_iso,
            "expiresAt": _cache_ttl(2592000),
        }
    )


def _get_job(jobs_table_name, user_id, job_id):
    table = _dynamodb.Table(jobs_table_name)
    return table.get_item(Key={"userId": user_id, "jobId": job_id}).get("Item")


def _query_jobs(jobs_table_name, user_id, limit):
    table = _dynamodb.Table(jobs_table_name)
    return table.query(
        KeyConditionExpression=Key("userId").eq(user_id),
        Limit=limit,
        ScanIndexForward=False,
    )


def _get_request_context(event):
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("claims") or {}
    user_id = claims.get("sub") or claims.get("username")
    return user_id


def _get_claims(event):
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    return authorizer.get("claims") or {}


def _upsert_user(users_table_name, user_id, claims):
    # Keep a minimal user record in DynamoDB for app-specific data.
    # Cognito remains the source of truth for authentication.
    table = _dynamodb.Table(users_table_name)
    now_iso = _now_iso()
    email = claims.get("email") if isinstance(claims, dict) else None
    name = claims.get("name") if isinstance(claims, dict) else None

    expr_names = {"#status": "status"}
    expr_values = {
        ":updatedAt": now_iso,
        ":createdAt": now_iso,
        ":active": "active",
    }
    set_expr = ["updatedAt = :updatedAt", "#status = if_not_exists(#status, :active)", "createdAt = if_not_exists(createdAt, :createdAt)"]

    if isinstance(email, str) and email.strip():
        expr_values[":email"] = email.strip()
        set_expr.append("email = :email")
    if isinstance(name, str) and name.strip():
        expr_values[":displayName"] = name.strip()
        set_expr.append("displayName = if_not_exists(displayName, :displayName)")

    table.update_item(
        Key={"userId": user_id},
        UpdateExpression="SET " + ", ".join(set_expr),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def _fetch_cache(table_name, cache_key):
    table = _dynamodb.Table(table_name)
    response = table.get_item(Key=cache_key)
    return response.get("Item")


def _write_cache(table_name, item):
    table = _dynamodb.Table(table_name)
    table.put_item(Item=item)


def _invoke_validator(function_name, payload):
    response = _lambda.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = response["Payload"].read().decode("utf-8")
    return json.loads(raw)


def _extract_target(body):
    if not isinstance(body, dict):
        return None, None
    for key, target_type in [
        ("url", "url"),
        ("link", "url"),
        ("email", "email"),
        ("message", "message"),
        ("content", "message"),
    ]:
        if key in body:
            value = _sanitize_value(body.get(key))
            if value:
                return target_type, value
    if "input" in body:
        return _infer_target(body.get("input"), body.get("input_type") or body.get("type"))
    return None, None


def _cache_ttl(seconds):
    return int(time.time()) + seconds


def _event_id():
    # Sort-key that preserves chronological order for Query (lex order == time order).
    return f"{int(time.time()):010d}-{uuid.uuid4()}"


def _parse_int(value, default):
    try:
        n = int(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def lambda_handler(event, _context):
    http_method = (event.get("httpMethod") or "").upper()
    path = _get_path(event)

    # GET /history
    if http_method == "GET":
        user_id = _get_request_context(event)
        if not user_id:
            return _response(401, {"message": "Unauthorized"})
        env = _get_env()

        # Create/update the user record the first time they hit the API.
        _upsert_user(env["users_table"], user_id, _get_claims(event))

        # GET /result/{jobId}
        job_id = (event.get("pathParameters") or {}).get("jobId")
        if job_id:
            item = _get_job(env["jobs_table"], user_id, job_id)
            if not item:
                return _response(404, {"message": "Not found"})
            return _response(200, {"item": item})

        limit = _parse_int((event.get("queryStringParameters") or {}).get("limit"), 50)
        resp = _query_jobs(env["jobs_table"], user_id, limit)
        return _response(200, {"items": resp.get("Items", [])})

    body = _safe_json(event.get("body"))
    user_id = _get_request_context(event)
    if not user_id:
        return _response(401, {"message": "Unauthorized"})
    if not body:
        return _response(400, {"message": "Invalid JSON body"})

    env = _get_env()

    # Create/update the user record the first time they hit the API.
    _upsert_user(env["users_table"], user_id, _get_claims(event))

    # POST /image/upload-url
    if http_method == "POST" and path.rstrip("/") == "/image/upload-url":
        if not env.get("image_bucket"):
            return _response(500, {"message": "IMAGE_BUCKET is not configured"})

        risk_sensitivity = _extract_risk_sensitivity(body) or "medium"
        content_type = body.get("content_type") or body.get("contentType")
        ext = (body.get("ext") or body.get("extension") or "png")
        if not isinstance(ext, str) or not re.match(r"^[a-zA-Z0-9]{1,8}$", ext.strip()):
            ext = "png"
        ext = ext.strip().lower()

        job_id = _event_id()
        s3_key = f"uploads/{user_id}/{job_id}.{ext}"

        # We reuse the existing analysis-jobs table.
        _create_job(env["jobs_table"], user_id, job_id, "image", s3_key, risk_sensitivity)

        upload_url = _presigned_put_url(env["image_bucket"], s3_key, content_type=content_type, expires_in=900)
        return _response(
            200,
            {
                "jobId": job_id,
                "uploadUrl": upload_url,
                "s3Key": s3_key,
                "expiresIn": 900,
            },
        )

    # Default POST: analyze text/url/email
    target_type, target_value = _extract_target(body)
    if not target_type or not target_value:
        return _response(400, {"message": "Provide 'url', 'link', 'email', 'message', 'content', or 'input'"})

    risk_sensitivity = _extract_risk_sensitivity(body) or "medium"
    job_id = _event_id()
    _create_job(env["jobs_table"], user_id, job_id, target_type, target_value, risk_sensitivity)

    _sqs.send_message(
        QueueUrl=env["analyze_queue_url"],
        MessageBody=json.dumps(
            {
                "user_id": user_id,
                "job_id": job_id,
                "target_type": target_type,
                "target_value": target_value,
                "risk_sensitivity": risk_sensitivity,
            }
        ),
    )

    return _response(202, {"jobId": job_id, "status": "PENDING"})

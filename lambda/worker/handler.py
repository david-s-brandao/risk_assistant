import json
import os
import time
import uuid
from datetime import datetime, timezone

import boto3

_dynamodb = boto3.resource("dynamodb")
_lambda = boto3.client("lambda")


def _now_iso():
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _hash_key(value):
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cache_ttl(seconds):
    return int(time.time()) + seconds


def _event_id():
    return f"{int(time.time()):010d}-{uuid.uuid4()}"


def _invoke_validator(function_name, payload):
    resp = _lambda.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = resp["Payload"].read().decode("utf-8")
    return json.loads(raw)


def _fetch_cache(table_name, key):
    return _dynamodb.Table(table_name).get_item(Key=key).get("Item")


def _put_item(table_name, item):
    _dynamodb.Table(table_name).put_item(Item=item)


def _update_job(jobs_table, user_id, job_id, **attrs):
    table = _dynamodb.Table(jobs_table)
    expr_names = {"#status": "status"}
    expr_values = {":updatedAt": _now_iso()}
    set_parts = ["updatedAt = :updatedAt"]

    if "status" in attrs:
        expr_values[":status"] = attrs.pop("status")
        set_parts.append("#status = :status")

    # Store anything else as SET <k> = :k
    for k, v in attrs.items():
        if v is None:
            continue
        name_key = f"#{k}"
        value_key = f":{k}"
        expr_names[name_key] = k
        expr_values[value_key] = v
        set_parts.append(f"{name_key} = {value_key}")

    table.update_item(
        Key={"userId": user_id, "jobId": job_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def _process_message(msg):
    body = msg.get("body")
    if not body:
        return
    payload = json.loads(body)

    user_id = payload["user_id"]
    job_id = payload["job_id"]
    target_type = payload["target_type"]
    target_value = payload["target_value"]
    risk_sensitivity = payload.get("risk_sensitivity", "medium")

    url_cache_table = os.environ["URL_CACHE_TABLE"]
    content_cache_table = os.environ["CONTENT_CACHE_TABLE"]
    jobs_table = os.environ["JOBS_TABLE"]
    validator_function = os.environ["VALIDATOR_FUNCTION"]
    cache_version = os.environ.get("CACHE_VERSION", "v1")

    try:
        # Risk assessments can become stale quickly; reuse them for at most one hour.
        cache_seconds = 60 * 60

        if target_type == "url":
            cache_table = url_cache_table
            key_name = "urlHash"
        else:
            cache_table = content_cache_table
            key_name = "contentHash"

        # Include target_type and a bumpable cache version so we can change
        # validator behavior/model without being stuck with stale safe results.
        cache_hash = _hash_key(f"{cache_version}|{target_type}|{target_value}|{risk_sensitivity}")
        cache_key = {key_name: cache_hash}

        cached = _fetch_cache(cache_table, cache_key)
        # DynamoDB TTL deletion is asynchronous, so never serve an expired item.
        cache_hit = bool(
            cached
            and isinstance(cached.get("result"), dict)
            and int(cached.get("expiresAt") or 0) > int(time.time())
        )

        if cache_hit:
            result = cached["result"]
            validator_fallback = False
            validator_error = None
        else:
            validator_resp = _invoke_validator(
                validator_function,
                {
                    "target_type": target_type,
                    "target_value": target_value,
                    "risk_sensitivity": risk_sensitivity,
                    "user_id": user_id,
                },
            )
            validator_resp = validator_resp or {}
            result = validator_resp.get("result") or {}
            validator_fallback = bool(validator_resp.get("fallback"))
            validator_error = validator_resp.get("error_detail") or validator_resp.get("error")

            _put_item(
                cache_table,
                {
                    **cache_key,
                    "result": result,
                    "riskSensitivity": risk_sensitivity,
                    "updatedAt": _now_iso(),
                    "expiresAt": _cache_ttl(cache_seconds),
                },
            )

        _update_job(
            jobs_table,
            user_id,
            job_id,
            status="COMPLETED",
            cacheHit=cache_hit,
            result=result,
            validatorFallback=validator_fallback,
            validatorError=validator_error,
            completedAt=_now_iso(),
        )
    except Exception as e:
        print(f"Worker error jobId={job_id}: {e}")
        _update_job(jobs_table, user_id, job_id, status="FAILED", error=str(e))
        raise


def lambda_handler(event, _context):
    # SQS batch
    records = event.get("Records") or []
    for record in records:
        _process_message(record)

    return {"ok": True}

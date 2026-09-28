import json
import os
import re
import time
from datetime import datetime, timezone

import boto3

_dynamodb = boto3.resource("dynamodb")
_lambda = boto3.client("lambda")
_textract = boto3.client("textract")
_rekognition = boto3.client("rekognition")
_s3 = boto3.client("s3")


def _now_iso():
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _invoke_validator(function_name, payload):
    resp = _lambda.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = resp["Payload"].read().decode("utf-8")
    return json.loads(raw)


def _update_job(jobs_table, user_id, job_id, **attrs):
    table = _dynamodb.Table(jobs_table)
    expr_names = {"#status": "status"}
    expr_values = {":updatedAt": _now_iso()}
    set_parts = ["updatedAt = :updatedAt"]

    if "status" in attrs:
        expr_values[":status"] = attrs.pop("status")
        set_parts.append("#status = :status")

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


def _get_job(jobs_table, user_id, job_id):
    return _dynamodb.Table(jobs_table).get_item(Key={"userId": user_id, "jobId": job_id}).get("Item")


def _extract_user_and_job_from_key(key):
    # Expected: uploads/<userId>/<jobId>.<ext>
    m = re.match(r"^uploads/([^/]+)/([^/.]+)\.[a-zA-Z0-9]{1,8}$", key or "")
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _ocr_text(bucket, key):
    try:
        resp = _textract.detect_document_text(Document={"S3Object": {"Bucket": bucket, "Name": key}})
        blocks = resp.get("Blocks") or []
        # Prefer LINE blocks for readability.
        lines = [b.get("Text") for b in blocks if b.get("BlockType") == "LINE" and isinstance(b.get("Text"), str)]
        text = "\n".join([ln.strip() for ln in lines if ln.strip()])
        return text
    except Exception as e:
        # Some sandboxed AWS accounts block Textract and return subscription-style errors.
        msg = str(e)
        if "subscription" not in msg.lower():
            raise

    # Fallback: Rekognition DetectText on raw bytes.
    obj = _s3.get_object(Bucket=bucket, Key=key)
    image_bytes = obj["Body"].read()
    resp = _rekognition.detect_text(Image={"Bytes": image_bytes})
    detections = resp.get("TextDetections") or []
    lines = [d.get("DetectedText") for d in detections if d.get("Type") == "LINE" and isinstance(d.get("DetectedText"), str)]
    text = "\n".join([ln.strip() for ln in lines if ln.strip()])
    if text.strip():
        return text
    # If no LINE detections, fall back to WORD detections (best-effort).
    words = [d.get("DetectedText") for d in detections if d.get("Type") == "WORD" and isinstance(d.get("DetectedText"), str)]
    return " ".join([w.strip() for w in words if w.strip()])


_URL_RE = re.compile(r"https?://[^\s)\]}>,\"']+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")


def _extract_items(text):
    if not isinstance(text, str) or not text.strip():
        return {"urls": [], "emails": []}
    urls = _URL_RE.findall(text)
    emails = _EMAIL_RE.findall(text)
    # De-dupe while keeping order.
    def _uniq(xs):
        seen = set()
        out = []
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    return {"urls": _uniq(urls)[:20], "emails": _uniq(emails)[:20]}


def _cache_ttl(seconds):
    return int(time.time()) + seconds


def _handle_record(record):
    s3 = (record.get("s3") or {})
    bucket = ((s3.get("bucket") or {}).get("name"))
    key = ((s3.get("object") or {}).get("key"))
    if not bucket or not key:
        return

    # S3 event keys can be URL-encoded.
    key = key.replace("+", " ")
    try:
        from urllib.parse import unquote_plus

        key = unquote_plus(key)
    except Exception:
        pass

    user_id, job_id = _extract_user_and_job_from_key(key)
    if not user_id or not job_id:
        print(f"Skipping unknown key format: {key}")
        return

    jobs_table = os.environ["JOBS_TABLE"]
    validator_function = os.environ["VALIDATOR_FUNCTION"]

    job = _get_job(jobs_table, user_id, job_id) or {}
    risk_sensitivity = job.get("riskSensitivity") or "medium"

    _update_job(jobs_table, user_id, job_id, status="PROCESSING", imageBucket=bucket, imageKey=key)

    try:
        extracted_text = _ocr_text(bucket, key)
        detected_items = _extract_items(extracted_text)

        # If Textract yields nothing, avoid hallucinating: validate a short factual message.
        validator_input = extracted_text.strip() or "No readable text detected in the image."

        validator_resp = _invoke_validator(
            validator_function,
            {
                "target_type": "message",
                "target_value": validator_input,
                "risk_sensitivity": risk_sensitivity,
                "user_id": user_id,
            },
        )
        validator_resp = validator_resp or {}
        result = validator_resp.get("result") or {}
        validator_fallback = bool(validator_resp.get("fallback"))
        validator_error = validator_resp.get("error_detail") or validator_resp.get("error")

        _update_job(
            jobs_table,
            user_id,
            job_id,
            status="COMPLETED",
            extractedText=extracted_text,
            detectedItems=detected_items,
            result=result,
            validatorFallback=validator_fallback,
            validatorError=validator_error,
            completedAt=_now_iso(),
            # Retain image job metadata for seven days after completion.
            expiresAt=_cache_ttl(7 * 24 * 60 * 60),
        )
    except Exception as e:
        print(f"Image worker error jobId={job_id}: {e}")
        _update_job(jobs_table, user_id, job_id, status="FAILED", error=str(e))
        raise


def lambda_handler(event, _context):
    records = event.get("Records") or []
    for r in records:
        _handle_record(r)
    return {"ok": True}

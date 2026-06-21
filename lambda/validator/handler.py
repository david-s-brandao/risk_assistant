import json
import os
import re
from urllib.parse import urlparse
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError


bedrock = boto3.client("bedrock-runtime")
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")


ALLOWED_DECISIONS = {"allow", "review", "block"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}


TRUSTED_ROOT_DOMAINS = {
    "linkedin.com",
    "google.com",
    "youtube.com",
    "microsoft.com",
    "apple.com",
    "amazon.com",
    "facebook.com",
    "instagram.com",
    "whatsapp.com",
    "github.com",
}


SUSPICIOUS_TLDS = {
    # Commonly abused or high-risk TLDs in phishing, kept small on purpose.
    "zip",
    "mov",
    "top",
    "xyz",
    "click",
    "link",
    "cam",
    "loan",
    "work",
    "country",
    "gq",
    "tk",
    "cf",
    "fun",
}


URL_SHORTENERS = {
    "bit.ly",
    "t.co",
    "tinyurl.com",
    "goo.gl",
    "rb.gy",
    "is.gd",
    "ow.ly",
    "cutt.ly",
    "shorturl.at",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_hostname_from_url(value):
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""

    # urlparse needs a scheme to reliably populate hostname.
    if not re.match(r"^https?://", trimmed, re.IGNORECASE):
        trimmed = "https://" + trimmed

    try:
        parsed = urlparse(trimmed)
    except Exception:
        return ""

    hostname = parsed.hostname or ""
    return hostname.lower().strip(".")


def _root_domain(hostname):
    hostname = (hostname or "").lower().strip(".")
    if not hostname:
        return ""

    # Prefer explicit trusted roots (avoids guessing public suffixes).
    for root in TRUSTED_ROOT_DOMAINS:
        if hostname == root or hostname.endswith("." + root):
            return root

    # Fallback: last two labels (best-effort; not a full PSL implementation).
    parts = hostname.split(".")
    if len(parts) < 2:
        return hostname
    return ".".join(parts[-2:])


def is_trusted_domain(hostname):
    root = _root_domain(hostname)
    return root in TRUSTED_ROOT_DOMAINS


def extract_signals(target_type, target_value):
    signals = {"target_type": target_type}

    value = target_value if isinstance(target_value, str) else ""
    value = value.strip()

    # For messages/emails we still try to extract the first URL so the model can
    # reason about it without hallucinating domain facts.
    url_candidate = ""
    if target_type == "url":
        url_candidate = value
    elif target_type in {"message", "email"}:
        # 1) Prefer explicit scheme URLs.
        m = re.search(r"https?://\S+", value, flags=re.IGNORECASE)
        if m:
            url_candidate = m.group(0)
        else:
            # 2) Catch common phishing SMS/email URLs that omit scheme, e.g.
            #    "www.example.top/pt" or "example.top".
            # Keep it conservative to avoid false positives on normal text.
            m2 = re.search(
                r"\b((?:www\.)?[a-z0-9][a-z0-9-]{0,62}(?:\.[a-z0-9-]{1,63})+(?:/[^\s]*)?)\b",
                value,
                flags=re.IGNORECASE,
            )
            if m2:
                url_candidate = m2.group(1)

    if target_type in {"message", "email"}:
        signals["has_url"] = bool(url_candidate)

    if not url_candidate:
        return signals

    # Best-effort URL parts for keyword signals.
    try:
        parsed_url = urlparse(
            url_candidate
            if re.match(r"^https?://", url_candidate, re.IGNORECASE)
            else "https://" + url_candidate
        )
        signals["url_path"] = (parsed_url.path or "")[:256]
        signals["url_query"] = (parsed_url.query or "")[:256]
        signals["url_scheme"] = (parsed_url.scheme or "").lower()
        signals["url_has_userinfo"] = bool(parsed_url.username or parsed_url.password)
        signals["url_length"] = min(len(url_candidate), 2048)
        signals["url_has_at_symbol"] = "@" in url_candidate
        # Quick, approximate counts.
        signals["url_path_depth"] = (parsed_url.path or "").strip("/").count("/") + (1 if (parsed_url.path or "").strip("/") else 0)
        signals["url_query_param_count"] = 0 if not parsed_url.query else (parsed_url.query.count("=") or parsed_url.query.count("&") + 1)
    except Exception:
        parsed_url = None

    hostname = _safe_hostname_from_url(url_candidate)
    if not hostname:
        return signals

    root = _root_domain(hostname)
    labels = hostname.split(".")
    tld = labels[-1] if labels else ""

    signals.update(
        {
            "hostname": hostname,
            "root_domain": root,
            "tld": tld,
        }
    )

    signals["hostname_dot_count"] = hostname.count(".")
    signals["hostname_has_ip_literal"] = bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", hostname))
    signals["hostname_has_punycode"] = "xn--" in hostname
    signals["tld_is_suspicious"] = tld in SUSPICIOUS_TLDS
    signals["is_url_shortener"] = root in URL_SHORTENERS or hostname in URL_SHORTENERS

    if root and hostname.endswith("." + root):
        sub = hostname[: -len(root)].rstrip(".")
        if sub:
            signals["subdomain"] = sub

    if root in TRUSTED_ROOT_DOMAINS:
        signals["is_trusted_root_domain"] = True
        signals["trusted_root_domain"] = root

    # Keyword-based signals (do not require trust assumptions).
    joined = " ".join(
        [
            str(signals.get("url_path") or ""),
            str(signals.get("url_query") or ""),
            hostname,
        ]
    ).lower()
    if any(k in joined for k in ["phishing", "credential", "login", "password", "verify", "malware", "trojan", "bank", "wallet", "seed"]):
        signals["url_contains_suspicious_keywords"] = True
        signals["suspicious_keywords"] = [
            k
            for k in ["phishing", "credential", "login", "password", "verify", "malware", "trojan", "bank", "wallet", "seed"]
            if k in joined
        ][:6]

    # Message/email specific: shallow social engineering hints.
    if target_type in {"message", "email"}:
        lower = value.lower()
        signals["message_length"] = min(len(value), 4000)
        # Lightweight multilingual heuristics (English + Portuguese) to help the model.
        signals["message_has_urgency_terms"] = bool(
            re.search(
                r"\b(urgent|immediately|asap|now|act\s+now|limited\s+time|final\s+notice|"
                r"urgente|imediat|agora|prazo|expir|ultimo\s+aviso|final\s+aviso|"
                r"expira\s+em\s+breve)\b",
                lower,
            )
        )
        signals["message_has_money_terms"] = bool(
            re.search(
                r"\b(invoice|payment|bank|transfer|refund|crypto|wallet|seed|prize|winner|"
                r"pagamento|banco|transfer|reembolso|premio|vencedor|fatura|referencia|iban|mbway|multibanco)\b",
                lower,
            )
        )
        signals["message_has_credentials_terms"] = bool(
            re.search(
                r"\b(password|otp|2fa|verification\s+code|login|sign\s+in|"
                r"palavra\s*-?passe|codigo|pin|entrar|confirmar|verific)\b",
                lower,
            )
        )

    return signals


def default_result(target_type):
    return {
        "score": 0,
        "decision": "allow",
        "category": "benign",
        "confidence": "low",
        "triggers": [],
        "summary": "No obvious risk indicators found.",
        "simple_explanation": "No clear risk was found.",
        "safe_next_step": "Open only if you trust the source.",
        "policy_reason": "Decision is allow because the risk score is 0.",
        "ai_reasoning_steps": [],
        "details": {
            "target_type": target_type,
            "risk_indicators": [],
            "safe_indicators": []
        },
        "recommended_action": "Proceed with caution and avoid sharing sensitive information.",
        "generated_at": now_iso()
    }


def build_prompt(target_type, target_value, risk_sensitivity):
    extracted_signals = extract_signals(target_type, target_value)
    return f"""
You are a cybersecurity risk classifier.

Analyze the provided input and return ONLY valid JSON.
Do not return markdown.
Do not return text outside the JSON object.

The JSON must follow this exact schema:

{{
  "score": 0,
    "decision": "allow",
  "category": "benign",
  "confidence": "low",
  "triggers": [],
  "summary": "",
  "simple_explanation": "",
  "safe_next_step": "",
  "policy_reason": "",
  "ai_reasoning_steps": [],
  "details": {{
    "target_type": "",
    "risk_indicators": [],
    "safe_indicators": []
  }},
  "recommended_action": ""
}}

 simple_explanation rules:
 - Explain the risk in simple language.
 - Write as if explaining to a non-technical user.
 - 1 short sentence.

 safe_next_step rules:
 - Give one practical next action.
 - Example: "Open the official app instead."

 policy_reason rules:
 - Explain why the decision was allow/review/block.
 - Mention risk sensitivity when relevant.

 ai_reasoning_steps rules (observable reasoning):
 - Return 2-4 short reasoning steps.
 - Each step should describe a detected signal or decision factor.
 - Use concise observable reasoning only.

  Grounding rules:
  - Only claim facts that are present in the input or in Extracted signals below.
  - Do not invent domain/TLD/subdomain facts.
  - Do not claim certificate/TLS issues unless provided in Extracted signals.

  Domain rules:
  - If extracted signals do NOT include hostname/root_domain, do not claim URL/domain structure issues.
  - If Input type is "message" or "email" and has_url=false, do not add triggers about domain/TLD/subdomain formatting.
  - Do not treat regional subdomains like pt.linkedin.com, uk.linkedin.com, fr.linkedin.com as suspicious by default.
 - Do not confuse subdomains with TLDs.
 - For hostname pt.linkedin.com:
   - root_domain is linkedin.com
   - tld is com
   - pt is a subdomain, not a TLD
 - Official brand domains and their subdomains should usually be low risk.
 - If extracted signals include is_trusted_root_domain=true and there are no strong risk indicators,
   the score should usually be 0-15 and decision should be allow.
 - Do not classify official subdomains of trusted root domains as impersonation.

 recommended_action rules:
 - Return a SHORT, concrete, human action (imperative sentence).
 - 5-12 words when possible.
 - Never return UI verbs like "Copy".
 - Examples: "Do not click. Verify sender via a known number." / "Open only if you trust the domain." / "Delete and report as phishing."

 Allowed values:
 - decision: "allow", "review", "block"
 - confidence: "low", "medium", "high"
 - score: integer from 0 to 100

  Scoring guidance (avoid fixed buckets like 0/10/35/95):
 - Compute a CONTINUOUS score based on the presence and strength of signals.
 - Prefer varied integers; do not always round to 0/5/10.
 - Use this approach:
   1) List risk indicators you found (3-8 items when possible).
   2) Assign each indicator an implicit weight (low/medium/high impact).
   3) Derive a score that reflects the combined risk.
 - If there are NO meaningful risk indicators, score should be low (0-15), not always exactly 0 or 10.

  Decision rules:
 - 0-30: allow
 - 31-70: review
 - 71-100: block
 - If the input shows phishing, credential theft, impersonation, scams, malware delivery, or social engineering, increase the score.
 - If the input is unknown but not clearly malicious, use "review".
 - If the input is clearly harmless, use "allow".
 - Ensure decision is consistent with score using the ranges above.

  Content type rules:
- If target_type is "url", analyze link/domain risk.
- If target_type is "message", analyze social engineering, phishing, scam, urgency, and manipulation.
- If target_type is "email", analyze sender risk, impersonation, spoofing indicators, and suspicious patterns.

  Keyword rules:
  - If extracted signals include url_contains_suspicious_keywords=true and the keyword list includes "phishing" or "malware",
    treat it as a strong indicator and usually score 80+ (decision block), even if the domain is well-known.
  - A trusted domain does NOT make a phishing/test path safe. If the path/keywords indicate phishing, score high.

  Extracted-signal rules:
  - Prefer using these fields for reasoning instead of guessing:
    hostname_has_ip_literal, hostname_has_punycode, hostname_dot_count, tld_is_suspicious, is_url_shortener,
    url_has_userinfo, url_has_at_symbol, url_path_depth, url_query_param_count,
    message_has_urgency_terms, message_has_money_terms, message_has_credentials_terms.
  - Add triggers that directly reference extracted fields (e.g. "URL shortener" if is_url_shortener=true).

  User risk sensitivity: {risk_sensitivity}
 Input type: {target_type}
 Input value: {target_value}

 Extracted signals (trusted, verified by Lambda):
 {json.dumps(extracted_signals, ensure_ascii=True)}
""".strip()


def call_bedrock(prompt):
    raise NotImplementedError("Use call_bedrock_any(prompt, user_id)")


def _call_bedrock_agent(prompt, user_id):
    agent_id = os.environ.get("BEDROCK_AGENT_ID")
    agent_alias_id = os.environ.get("BEDROCK_AGENT_ALIAS_ID")
    if not agent_id or not agent_alias_id:
        return None

    # Agent runtime expects short-ish session ids; user_id (Cognito sub) fits.
    session_id = str(user_id or "anonymous")[:100]

    resp = bedrock_agent_runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=agent_alias_id,
        sessionId=session_id,
        inputText=prompt,
    )

    # Response is a streaming iterator of events with chunk bytes.
    parts = []
    stream = resp.get("completion")
    if stream is None:
        return None

    for event in stream:
        chunk = event.get("chunk")
        if not chunk:
            continue
        raw_bytes = chunk.get("bytes")
        if not raw_bytes:
            continue
        try:
            parts.append(raw_bytes.decode("utf-8", errors="replace"))
        except Exception:
            continue

    text = "".join(parts).strip()
    return text or None


def _call_bedrock_model(prompt):
    # Terraform sets BEDROCK_MODEL_ID, but keep a safe default for resilience
    # (e.g. local runs, misconfigured env).
    model_id = os.environ.get("BEDROCK_MODEL_ID") or "mistral.mixtral-8x7b-instruct-v0:1"

    response = bedrock.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={
            "temperature": 0.2,
            # Keep response compact to control latency/cost.
            "maxTokens": 450,
        },
    )

    output = response.get("output", {})
    message = output.get("message", {})
    content = message.get("content", [])

    for item in content:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    return None


def call_bedrock_any(prompt, user_id):
    # Prefer agent if configured; fall back to direct model call.
    try:
        text = _call_bedrock_agent(prompt, user_id)
        if text:
            return text
    except (ClientError, BotoCoreError, ValueError) as error:
        print(f"Bedrock agent error: {error}")

    return _call_bedrock_model(prompt)


def extract_json(text):
    if not text:
        return None

    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def normalize_score(value):
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0

    return max(0, min(100, score))


def decision_from_score(score):
    if score <= 30:
        return "allow"
    if score <= 70:
        return "review"
    return "block"


def normalize_result(raw, target_type):
    base = default_result(target_type)

    if not isinstance(raw, dict):
        return base

    score = normalize_score(raw.get("score", base["score"]))

    decision = raw.get("decision")
    # Accept legacy/alternate naming and normalize.
    if decision == "warn":
        decision = "review"
    if decision not in ALLOWED_DECISIONS:
        decision = decision_from_score(score)

    confidence = raw.get("confidence")
    if confidence not in ALLOWED_CONFIDENCE:
        confidence = base["confidence"]

    triggers = raw.get("triggers")
    if not isinstance(triggers, list):
        triggers = []

    triggers = [str(item) for item in triggers if item]

    details = raw.get("details")
    if not isinstance(details, dict):
        details = base["details"]

    if "target_type" not in details:
        details["target_type"] = target_type

    if "risk_indicators" not in details or not isinstance(details.get("risk_indicators"), list):
        details["risk_indicators"] = []

    if "safe_indicators" not in details or not isinstance(details.get("safe_indicators"), list):
        details["safe_indicators"] = []

    simple_explanation = str(
        raw.get("simple_explanation")
        or raw.get("summary")
        or "No clear risk was found."
    ).strip()

    safe_next_step = str(
        raw.get("safe_next_step")
        or raw.get("recommended_action")
        or "Open only if you trust the source."
    ).strip()

    policy_reason = str(
        raw.get("policy_reason")
        or f"Decision is {decision} because the risk score is {score}."
    ).strip()

    ai_reasoning_steps = raw.get("ai_reasoning_steps")
    if not isinstance(ai_reasoning_steps, list):
        ai_reasoning_steps = []

    ai_reasoning_steps = [
        str(step).strip()
        for step in ai_reasoning_steps
        if str(step).strip()
    ][:4]

    return {
        "score": score,
        "decision": decision,
        "category": str(raw.get("category", base["category"])),
        "confidence": confidence,
        "triggers": triggers,
        "summary": str(raw.get("summary", base["summary"])),
        "simple_explanation": simple_explanation,
        "safe_next_step": safe_next_step,
        "policy_reason": policy_reason,
        "ai_reasoning_steps": ai_reasoning_steps,
        "details": details,
        "recommended_action": str(raw.get("recommended_action", base["recommended_action"])),
        "generated_at": now_iso()
    }


def apply_heuristic_overrides(extracted_signals, result):
    """Hard guardrails for common phishing patterns.

    This prevents under-scoring when the LLM misses obvious red flags.
    """
    if not isinstance(extracted_signals, dict) or not isinstance(result, dict):
        return result

    triggers = result.get("triggers")
    if not isinstance(triggers, list):
        triggers = []

    def add_trigger(label):
        if label not in triggers:
            triggers.append(label)

    suspicious_url = False
    if extracted_signals.get("hostname_has_punycode"):
        suspicious_url = True
        add_trigger("Punycode domain")
    if extracted_signals.get("hostname_has_ip_literal"):
        suspicious_url = True
        add_trigger("IP literal hostname")
    if extracted_signals.get("tld_is_suspicious"):
        suspicious_url = True
        add_trigger("Suspicious TLD")
    if extracted_signals.get("is_url_shortener"):
        suspicious_url = True
        add_trigger("URL shortener")
    if extracted_signals.get("url_has_userinfo") or extracted_signals.get("url_has_at_symbol"):
        suspicious_url = True
        add_trigger("Deceptive URL format")
    keyword_block = False
    if extracted_signals.get("url_contains_suspicious_keywords"):
        suspicious_url = True
        add_trigger("Suspicious URL keywords")

        kws = extracted_signals.get("suspicious_keywords")
        if isinstance(kws, list):
            kws = [str(k).lower() for k in kws if k]
            # These keywords are strong enough to justify blocking regardless of domain.
            if any(k in kws for k in ["phishing", "malware", "trojan", "credential"]):
                keyword_block = True

    # Messages with urgency + link from an untrusted domain often indicate scams.
    if extracted_signals.get("target_type") in {"message", "email"}:
        has_url = bool(extracted_signals.get("has_url"))
        urgency = bool(extracted_signals.get("message_has_urgency_terms"))
        trusted = bool(extracted_signals.get("is_trusted_root_domain"))
        if has_url and urgency and not trusted:
            suspicious_url = True
            add_trigger("Urgent message with link")

    if suspicious_url:
        # Enforce a minimum score so the UI doesn't show 0% safe.
        try:
            current = int(result.get("score") or 0)
        except Exception:
            current = 0
        floor = 90 if keyword_block else 75
        result["score"] = max(current, floor)
        result["decision"] = "block"
        if result.get("confidence") in {"low", None, ""}:
            result["confidence"] = "medium"
        result["triggers"] = triggers[:10]
    return result


def lambda_handler(event, context):
    target_type = event.get("target_type")
    target_value = event.get("target_value")
    risk_sensitivity = event.get("risk_sensitivity", "medium")

    if not target_type or not target_value:
        return {
            "error": "missing target_type or target_value"
        }

    target_type = str(target_type).lower().strip()
    target_value = str(target_value).strip()
    risk_sensitivity = str(risk_sensitivity).lower().strip()

    prompt = build_prompt(
        target_type=target_type,
        target_value=target_value,
        risk_sensitivity=risk_sensitivity
    )

    user_id = event.get("user_id") or event.get("userId")

    try:
        response_text = call_bedrock_any(prompt, user_id)
        raw_result = extract_json(response_text)
        result = normalize_result(raw_result, target_type)
        extracted_signals = extract_signals(target_type, target_value)
        result = apply_heuristic_overrides(extracted_signals, result)

        return {
            "result": result,
            "fallback": False,
        }

    except (ClientError, BotoCoreError, ValueError) as error:
        print(f"Validator error: {error}")

        return {
            "result": default_result(target_type),
            "fallback": True,
            "error": "validator_failed",
            "error_detail": str(error)[:800],
        }

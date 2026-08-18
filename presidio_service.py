"""
Presidio PII Redaction — Production Service for RAG
===================================================
REST API on port 8090. Custom crypto wallet recognizers.
Wired into RAG ingest pipeline.
"""
import os
import logging
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("presidio-rag")

SERVICE_PORT = int(os.environ.get("PRESIDIO_PORT", 8090))

# Crypto wallet address patterns (custom recognizers for RAG)
CRYPTO_PATTERNS = [
    {"name": "ETH_ADDRESS", "regex": r"\b0x[a-fA-F0-9]{40}\b", "score": 0.95},
    {"name": "SOL_ADDRESS", "regex": r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", "score": 0.55},
    {"name": "BTC_LEGACY", "regex": r"\b1[1-9A-HJ-NP-Za-km-z]{25,34}\b", "score": 0.9},
    {"name": "BTC_BECH32", "regex": r"\bbc1[02-9ac-hj-np-z]{6,87}\b", "score": 0.95},
    {"name": "BTC_P2SH", "regex": r"\b3[1-9A-HJ-NP-Za-km-z]{25,34}\b", "score": 0.85},
    {"name": "TON_ADDRESS", "regex": r"\b[UE]Q[A-Za-z0-9_-]{46}\b", "score": 0.7},
    {"name": "TRX_ADDRESS", "regex": r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b", "score": 0.9},
]

REDACTION_MAP = {
    "ETH_ADDRESS": "0x[ETH_REDACTED]",
    "SOL_ADDRESS": "[SOL_REDACTED]",
    "BTC_LEGACY": "[BTC_REDACTED]",
    "BTC_BECH32": "[BTC_REDACTED]",
    "BTC_P2SH": "[BTC_REDACTED]",
    "TON_ADDRESS": "[TON_REDACTED]",
    "TRX_ADDRESS": "[TRX_REDACTED]",
    "EMAIL_ADDRESS": "[EMAIL_REDACTED]",
    "PHONE_NUMBER": "[PHONE_REDACTED]",
    "CREDIT_CARD": "[CC_REDACTED]",
    "IBAN_CODE": "[IBAN_REDACTED]",
    "PERSON": "[PERSON_REDACTED]",
    "US_SSN": "[SSN_REDACTED]",
    "URL": "[URL_REDACTED]",
    "IP_ADDRESS": "[IP_REDACTED]",
    "LOCATION": "[LOCATION_REDACTED]",
}

_engine_cache = {"analyzer": None, "anonymizer": None}

def get_engines():
    if _engine_cache["analyzer"] is None:
        from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        # Force en_core_web_sm (not lg which would need 800MB download)
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])

        for p in CRYPTO_PATTERNS:
            pattern = Pattern(name=p["name"], regex=p["regex"], score=p["score"])
            recognizer = PatternRecognizer(
                supported_entity=p["name"],
                patterns=[pattern],
                name=f"{p['name']}_recognizer",
            )
            analyzer.registry.add_recognizer(recognizer)
        _engine_cache["analyzer"] = analyzer
        _engine_cache["anonymizer"] = AnonymizerEngine()
        log.info("Presidio engines loaded with %d custom crypto recognizers", len(CRYPTO_PATTERNS))
    return _engine_cache["analyzer"], _engine_cache["anonymizer"]


def redact_text(text, language="en"):
    analyzer, anonymizer = get_engines()
    results = analyzer.analyze(text=text, language=language)
    if not results:
        return {"redacted": text, "pii_found": 0, "entities": []}

    from presidio_anonymizer.entities import OperatorConfig
    operators = {}
    for r in results:
        placeholder = REDACTION_MAP.get(r.entity_type, f"[{r.entity_type}_REDACTED]")
        operators[r.entity_type] = OperatorConfig("replace", {"new_value": placeholder})

    anonymized = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
    return {
        "redacted": anonymized.text,
        "pii_found": len(results),
        "entities": [{"type": r.entity_type, "start": r.start, "end": r.end, "score": round(r.score, 2)} for r in results],
    }


# Flask app
app = Flask("presidio-rag")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "presidio-rag", "port": SERVICE_PORT, "custom_recognizers": len(CRYPTO_PATTERNS)})

@app.route("/redact", methods=["POST"])
def redact():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "")
    language = data.get("language", "en")
    if not text:
        return jsonify({"error": "no text provided"}), 400
    return jsonify(redact_text(text, language))

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "")
    if not text:
        return jsonify({"error": "no text provided"}), 400
    analyzer, _ = get_engines()
    results = analyzer.analyze(text=text, language=data.get("language", "en"))
    return jsonify([{"type": r.entity_type, "start": r.start, "end": r.end, "score": round(r.score, 2)} for r in results])

@app.route("/redact/batch", methods=["POST"])
def redact_batch():
    """Batch endpoint for RAG ingest pipeline."""
    data = request.get_json(force=True, silent=True) or {}
    documents = data.get("documents", [])
    if not documents:
        return jsonify({"error": "no documents provided"}), 400
    results = []
    for doc in documents:
        doc_id = doc.get("id", "")
        text = doc.get("text", "")
        meta = doc.get("meta", {})
        result = redact_text(text)
        results.append({"id": doc_id, "redacted": result["redacted"], "pii_found": result["pii_found"], "entities": result["entities"], "meta": meta})
    return jsonify({"count": len(results), "results": results})


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true")
    p.add_argument("--daemon", action="store_true")
    args = p.parse_args()

    if args.test:
        test = """
Contact alice@example.com or 0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb0.
Send SOL to 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU.
BTC: bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh
Tether TRC20: TXYZopuvdj4sFDwv6Ma9d1kC2QjKp8d8vK
IBAN: GB29NWBK60161331926819
Phone: +1-555-123-4567
"""
        result = redact_text(test)
        print(f"\nOriginal:\n{test}")
        print(f"\nRedacted:\n{result['redacted']}")
        print(f"\nPII found: {result['pii_found']} entities")
        for e in result["entities"]:
            print(f"  {e['type']}: score={e['score']:.2f}")
    elif args.daemon:
        log.info("Starting Presidio RAG service on 0.0.0.0:%d", SERVICE_PORT)
        app.run(host="0.0.0.0", port=SERVICE_PORT, debug=False, use_reloader=False)
    else:
        print("Use --daemon to start service, --test to test")

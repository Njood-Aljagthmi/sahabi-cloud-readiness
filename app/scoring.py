import math

AR_6RS = {
    "Rehost": "إعادة استضافة (Rehost)",
    "Replatform": "إعادة منصة (Replatform)",
    "Refactor": "إعادة هيكلة (Refactor)",
    "Re-architect": "إعادة تصميم (Re-architect)",
    "Retain": "إبقاء (Retain)",
    "Retire": "إيقاف (Retire)",
}

def _softmax(scores: dict[str, float]) -> dict[str, float]:
    # stable softmax
    m = max(scores.values())
    exps = {k: math.exp(v - m) for k, v in scores.items()}
    s = sum(exps.values()) or 1.0
    return {k: exps[k] / s for k in scores}

def score_6rs(analysis: dict, features: dict) -> dict:
    # === Base scores (كلها تبدأ متقاربة عشان ما يتحيز خيار واحد) ===
    scores = {
        "Rehost": 1.0,
        "Replatform": 1.0,
        "Refactor": 1.0,
        "Re-architect": 0.8,
        "Retain": 0.6,
        "Retire": 0.4
    }

    reasons = {k: [] for k in scores.keys()}

    # === Signals ===
    loc = int(analysis.get("lines_of_code", 0) or 0)

    has_docker = bool(features.get("has_dockerfile", False))
    has_deps = bool(features.get("has_dependencies_file", False))

    uses_framework = any([
        features.get("uses_flask"),
        features.get("uses_django"),
        features.get("uses_fastapi"),
        features.get("uses_spring"),
        features.get("uses_dotnet"),
        features.get("uses_node"),
    ])

    # إشارات إضافية (إذا مو موجودة عندك الآن، خليها False وتشتغل عادي)
    has_ci = bool(features.get("has_ci", False))                 # GitHub Actions / GitLab CI
    has_tests = bool(features.get("has_tests", False))           # tests/ __tests__
    has_k8s = bool(features.get("has_k8s", False))               # k8s/helm/manifests
    has_terraform = bool(features.get("has_terraform", False))   # *.tf
    secrets_found = bool(features.get("secrets_found", False))   # secrets scan flag
    uses_db = bool(features.get("uses_database", False))         # sqlite/postgres/mysql mentions
    stateful = bool(features.get("is_stateful", False))          # uploads/local storage etc.

    # === Size logic ===
    if loc == 0:
        scores["Retire"] += 4.0
        reasons["Retire"].append("No effective code detected (LOC=0)")
    elif loc < 5000:
        scores["Rehost"] += 1.6
        reasons["Rehost"].append("Low/medium code size")
        scores["Replatform"] += 1.2
        reasons["Replatform"].append("Manageable migration scope")
    elif loc > 25000:
        scores["Refactor"] += 1.2
        reasons["Refactor"].append("Large codebase increases refactor likelihood")
        scores["Re-architect"] += 1.6
        reasons["Re-architect"].append("Very large codebase may require re-architecture")

    # === Framework ===
    if uses_framework:
        scores["Rehost"] += 0.8
        reasons["Rehost"].append("Recognized framework detected")
        scores["Replatform"] += 0.6
        reasons["Replatform"].append("Framework apps often fit managed platforms")

    # === Dependencies ===
    if has_deps:
        scores["Rehost"] += 0.6
        reasons["Rehost"].append("Dependencies file exists (requirements/pyproject/package.json)")
        scores["Replatform"] += 0.6
        reasons["Replatform"].append("Dependencies are defined")
    else:
        scores["Refactor"] += 1.4
        reasons["Refactor"].append("Dependencies not explicitly defined")

    # === Docker ===
    if has_docker:
        scores["Replatform"] += 2.8
        reasons["Replatform"].append("Containerization detected (Dockerfile)")
        # docker وجوده يخفف rehost شوي لأنه يدل على منصة مُدارة مناسبة
        scores["Rehost"] -= 0.4
        reasons["Rehost"].append("Docker suggests a managed container platform fits better")
        # لو عنده K8s بعد -> retain أعلى
        if has_k8s:
            scores["Retain"] += 2.0
            reasons["Retain"].append("K8s manifests detected (already cloud-ready)")
    else:
        scores["Rehost"] += 0.6
        reasons["Rehost"].append("No containerization → VM rehost often quickest")

    # === CI / Tests ===
    if has_ci:
        scores["Replatform"] += 1.2
        reasons["Replatform"].append("CI detected (deployment maturity)")
        scores["Retain"] += 1.0
        reasons["Retain"].append("CI indicates operational readiness")
    else:
        scores["Refactor"] += 0.6
        reasons["Refactor"].append("No CI detected (needs modernization)")

    if has_tests:
        scores["Replatform"] += 0.8
        reasons["Replatform"].append("Tests detected (safer migration)")
        scores["Retain"] += 0.6
        reasons["Retain"].append("Tests indicate quality gates exist")
    else:
        scores["Refactor"] += 0.8
        reasons["Refactor"].append("No tests detected (higher migration risk)")

    # === IaC ===
    if has_terraform:
        scores["Retain"] += 1.4
        reasons["Retain"].append("Terraform detected (cloud infrastructure readiness)")
        scores["Replatform"] += 0.6
        reasons["Replatform"].append("IaC supports replatform execution")

    # === Security / Compliance signals ===
    if secrets_found:
        scores["Refactor"] += 2.2
        reasons["Refactor"].append("Secrets detected → must refactor secrets management")
        scores["Retain"] -= 1.2
        reasons["Retain"].append("Secrets reduce readiness for retain")

    # === Stateful/DB ===
    if stateful:
        scores["Re-architect"] += 1.2
        reasons["Re-architect"].append("Stateful behavior suggests architectural changes")
        scores["Rehost"] -= 0.4
        reasons["Rehost"].append("Stateful reduces lift-and-shift simplicity")

    if uses_db:
        scores["Replatform"] += 0.5
        reasons["Replatform"].append("DB usage fits managed services (PaaS)")

    # === Pick top + confidence (رقمية حقيقية) ===
    probs = _softmax(scores)
    sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    top = sorted_probs[0]
    second = sorted_probs[1] if len(sorted_probs) > 1 else ("—", 0.0)

    # Confidence = top probability (0..1) + bonus لو الفارق كبير
    margin = float(top[1] - second[1])
    confidence = round(min(0.95, max(0.20, float(top[1]) + margin * 0.5)), 2)

    candidates = [
        {"strategy": k, "strategy_ar": AR_6RS.get(k, k), "prob": round(v, 3)}
        for k, v in sorted_probs[:3]
    ]

    return {
        "scores": {k: round(v, 2) for k, v in scores.items()},
        "probabilities": {k: round(v, 3) for k, v in probs.items()},
        "reasons": reasons,
        "confidence": confidence,     # <-- رقم (0..1) مو "Low/Medium"
        "top_choice": top[0],
        "candidates": candidates
    }

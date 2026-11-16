from __future__ import annotations

import re
import uuid
import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from fastapi.openapi.docs import get_swagger_ui_html


# =========================
# Paths + Load .env early
# =========================
BASE_DIR = Path(__file__).resolve().parents[1]   # .../backend
APP_DIR = Path(__file__).resolve().parent        # .../backend/app
DASHBOARD_DIR = APP_DIR / "dashboard"            # .../backend/app/dashboard
TMP_REPOS_DIR = BASE_DIR / "tmp_repos"

load_dotenv(dotenv_path=BASE_DIR / ".env")


# =========================
# Create app (disable default docs; we’ll add custom /docs)
# =========================
app = FastAPI(
    title="Sahabi",
    docs_url=None,
    redoc_url=None,
)

app.mount(
    "/ui",
    StaticFiles(directory=str(DASHBOARD_DIR), html=True),
    name="ui",
)


# =========================
# In-memory report store (MVP)
# =========================
REPORT_STORE: dict[str, dict] = {}


# =========================
# Optional imports (don’t break app if missing)
# =========================
try:
    from app.chatbot.llm_bot import llm_answer  # may depend on OPENAI_API_KEY
except Exception:
    llm_answer = None

try:
    from app.storage.repo_loader import clone_repo
except Exception:
    clone_repo = None


# =========================
# Helpers (Stable analysis that front-end can trust)
# =========================
LANG_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "JavaScript (React)",
    ".tsx": "TypeScript (React)",
    ".java": "Java",
    ".go": "Go",
    ".cs": "C#",
    ".php": "PHP",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".xml": "XML",
    ".md": "Markdown",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".tf": "Terraform",
}

EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache"
}

SECRET_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",                    # AWS access key
    r"(?i)api[_-]?key\s*=\s*['\"][^'\"]+",
    r"(?i)secret\s*=\s*['\"][^'\"]+",
    r"(?i)token\s*=\s*['\"][^'\"]+",
    r"(?i)password\s*=\s*['\"][^'\"]+",
    r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
]


def _safe_read_text(path: Path, max_bytes: int = 250_000) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _count_loc(text: str) -> int:
    # LOC = عدد الأسطر غير الفارغة
    return sum(1 for line in text.splitlines() if line.strip())


def _scan_repo(project_path: Path) -> dict[str, Any]:
    files_scanned = 0
    total_loc = 0
    lang_loc: dict[str, int] = {}
    secrets_found: list[dict[str, str]] = []

    for p in project_path.rglob("*"):
        if not p.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue

        # treat Dockerfile as a known "language"
        if p.name == "Dockerfile":
            files_scanned += 1
            text = _safe_read_text(p)
            loc = _count_loc(text)
            total_loc += loc
            lang_loc["Dockerfile"] = lang_loc.get("Dockerfile", 0) + loc
            continue

        suffix = p.suffix.lower()
        if suffix not in LANG_BY_SUFFIX:
            continue

        files_scanned += 1
        text = _safe_read_text(p)
        loc = _count_loc(text)
        total_loc += loc

        lang = LANG_BY_SUFFIX.get(suffix, "Other")
        lang_loc[lang] = lang_loc.get(lang, 0) + loc

        for pat in SECRET_PATTERNS:
            if re.search(pat, text):
                secrets_found.append({"file": str(p.relative_to(project_path)), "pattern": pat})
                break

    has_docker = (project_path / "Dockerfile").exists()
    has_compose = (project_path / "docker-compose.yml").exists() or (project_path / "docker-compose.yaml").exists()
    has_k8s = any((project_path / d).exists() for d in ["k8s", "kubernetes", "manifests", "helm", "charts"])
    has_ci = (project_path / ".github" / "workflows").exists() or (project_path / ".gitlab-ci.yml").exists()
    has_tests = any((project_path / d).exists() for d in ["tests", "test", "__tests__"])
    has_readme = (project_path / "README.md").exists() or (project_path / "readme.md").exists()
    has_terraform = any(p.suffix.lower() == ".tf" for p in project_path.rglob("*.tf"))
    has_env_file = (project_path / ".env").exists() or any(p.name.endswith(".env") for p in project_path.rglob("*.env"))

    signals = {
        "files_scanned": files_scanned,
        "total_loc": total_loc,
        "has_docker": has_docker,
        "has_compose": has_compose,
        "has_k8s": has_k8s,
        "has_ci": has_ci,
        "has_tests": has_tests,
        "has_readme": has_readme,
        "has_terraform": has_terraform,
        "has_env_file": has_env_file,
        "secrets_found": secrets_found,
    }

    return {"lang_loc": lang_loc, "signals": signals}


def _calc_readiness(signals: dict[str, Any]) -> int:
    score = 20

    if signals["has_readme"]:
        score += 5
    if signals["has_tests"]:
        score += 15
    if signals["has_ci"]:
        score += 15
    if signals["has_docker"]:
        score += 15
    if signals["has_compose"]:
        score += 5
    if signals["has_k8s"]:
        score += 10
    if signals["has_terraform"]:
        score += 10

    # Penalties
    if signals["has_env_file"]:
        score -= 5
    if signals["secrets_found"]:
        score -= 25

    return max(0, min(100, score))


def _recommend_6rs(readiness: int, signals: dict[str, Any]) -> dict[str, Any]:
    total_loc = signals["total_loc"]
    secrets = bool(signals["secrets_found"])

    if total_loc == 0:
        strategy = "Retire"
        strategy_ar = "إيقاف (Retire)"
    elif secrets and readiness < 60:
        strategy = "Refactor"
        strategy_ar = "إعادة هيكلة (Refactor)"
    elif readiness >= 70 and (signals["has_docker"] or signals["has_ci"]):
        strategy = "Replatform"
        strategy_ar = "إعادة منصة (Replatform)"
    elif readiness >= 55:
        strategy = "Rehost"
        strategy_ar = "إعادة استضافة (Rehost / Lift & Shift)"
    else:
        strategy = "Refactor" if total_loc > 5000 else "Rehost"
        strategy_ar = "إعادة هيكلة (Refactor)" if strategy == "Refactor" else "إعادة استضافة (Rehost)"

    reasons: list[str] = []
    risks: list[str] = []
    next_steps: list[str] = []

    if signals["has_tests"]:
        reasons.append("يوجد اختبارات تساعد على تقليل مخاطر الترحيل.")
    else:
        risks.append("غياب الاختبارات يرفع احتمال ظهور أخطاء بعد الترحيل.")
        next_steps.append("إضافة اختبارات أساسية (Smoke/Integration) قبل أي ترحيل.")

    if signals["has_ci"]:
        reasons.append("يوجد CI يسهل بناء ونشر التطبيق بشكل ثابت.")
    else:
        next_steps.append("إعداد CI (GitHub Actions/GitLab CI) لتوحيد البناء والنشر.")

    if signals["has_docker"]:
        reasons.append("وجود Dockerfile يسهل النقل إلى بيئات سحابية.")
    else:
        next_steps.append("إنشاء Dockerfile موحّد للتطبيق.")

    if secrets:
        risks.append("تم رصد مؤشرات أسرار/مفاتيح داخل المشروع.")
        next_steps.insert(0, "نقل الأسرار إلى Secret Manager + تنظيف الريبو.")

    roadmap = [
        {"phase": "Discovery", "items": ["فهم المعمارية والاعتماديات", "حصر الخدمات وقواعد البيانات"]},
        {"phase": "Readiness Fixes", "items": ["Secrets management", "إضافة/تحديث CI", "أساسيات Observability"]},
        {"phase": "Migration", "items": [f"تطبيق استراتيجية {strategy_ar}", "اختبارات بعد النقل", "خطة Rollback"]},
        {"phase": "Optimization", "items": ["تحسين التكلفة", "تحسين الأداء", "حوكمة وأمان"]},
    ]

    signal_points = sum(1 for k in ["has_tests", "has_ci", "has_docker", "has_readme", "has_k8s", "has_terraform"] if signals.get(k))
    confidence = round(min(0.95, 0.35 + signal_points * 0.10), 2)

    return {
        "strategy": strategy,
        "strategy_ar": strategy_ar,
        "readiness_score": readiness,
        "confidence": confidence,
        "reasons": reasons or ["التحليل اعتمد على فحص الملفات والبنية العامة للمشروع."],
        "risks": risks or ["مخاطر عامة: اختلاف الإعدادات بين البيئة المحلية والسحابة."],
        "next_steps": next_steps or ["ابدأ بتثبيت طريقة تشغيل/نشر ثابتة قبل الترحيل."],
        "roadmap": roadmap,
    }


def _compliance(signals: dict[str, Any]) -> dict[str, Any]:
    # IMPORTANT: issues (strings) to avoid [object Object] in frontend
    issues_details: list[dict[str, Any]] = []

    if signals["secrets_found"]:
        issues_details.append({"id": "SEC-001", "title": "Potential secrets in repo", "severity": "high"})
    if not signals["has_ci"]:
        issues_details.append({"id": "GOV-001", "title": "No CI pipeline detected", "severity": "medium"})
    if not signals["has_tests"]:
        issues_details.append({"id": "Q-001", "title": "No tests folder detected", "severity": "medium"})

    score = 95
    for i in issues_details:
        if i["severity"] == "high":
            score -= 30
        elif i["severity"] == "medium":
            score -= 15
        else:
            score -= 5
    score = max(0, min(100, score))

    if score >= 80:
        status = "good"
    elif score >= 60:
        status = "partial"
    else:
        status = "poor"

    issues_strings = [f"{x['id']}: {x['title']} ({x['severity']})" for x in issues_details]

    return {
        "score": score,
        "status": status,
        "compliant": score >= 70,
        "issues": issues_strings,              # <- strings (safe for UI)
        "issues_details": issues_details,      # <- objects (optional)
    }


def _build_language_percent(lang_loc: dict[str, int], total_loc: int) -> list[dict[str, Any]]:
    if total_loc <= 0 or not lang_loc:
        return []

    items = sorted(lang_loc.items(), key=lambda x: x[1], reverse=True)
    result = []
    for name, loc in items:
        percent = round((loc / total_loc) * 100, 1)
        result.append({"name": name, "percent": percent, "loc": loc})
    return result


def analyze_local_repo(project_path: Path) -> dict[str, Any]:
    scanned = _scan_repo(project_path)
    lang_loc = scanned["lang_loc"]
    signals = scanned["signals"]

    readiness = _calc_readiness(signals)
    rec = _recommend_6rs(readiness, signals)
    comp = _compliance(signals)

    total_loc = signals["total_loc"]
    languages_percent = _build_language_percent(lang_loc, total_loc)
    main_language = languages_percent[0]["name"] if languages_percent else "—"

    # LOC breakdown for charts
    breakdown_loc = [{"language": k, "loc": v} for k, v in sorted(lang_loc.items(), key=lambda x: x[1], reverse=True)]

    analysis = {
        "lines_of_code": total_loc,
        "files_scanned": signals["files_scanned"],
        "main_language": main_language,
        # percent list like your screenshot (Python 99.9, Other 0.1)
        "languages": [{"name": x["name"], "percent": x["percent"]} for x in languages_percent],
        # LOC list for bar/pie if needed
        "language_breakdown": breakdown_loc,
    }

    features = {"signals": signals}

    explainability = {
        "confidence": rec["confidence"],
        "signals": {k: signals[k] for k in signals if k != "secrets_found"},
    }

    recommendation = {
        "strategy": rec["strategy"],
        "strategy_ar": rec["strategy_ar"],
        "readiness_score": rec["readiness_score"],
        "confidence": rec["confidence"],
        "reasons": rec["reasons"],
        "risks": rec["risks"],
        "next_steps": rec["next_steps"],
        "roadmap": rec["roadmap"],
    }

    return {
        "analysis": analysis,
        "features": features,
        "recommendation": recommendation,
        "compliance": comp,
        "explainability": explainability,
        # sometimes frontend likes this at top-level:
        "readiness_score": rec["readiness_score"],
    }


def _clone_repo_fallback(repo_url: str, scan_id: str) -> str:
    TMP_REPOS_DIR.mkdir(parents=True, exist_ok=True)
    target = TMP_REPOS_DIR / scan_id
    subprocess.check_call(["git", "clone", "--depth", "1", repo_url, str(target)])
    return str(target)


# =========================
# Routes
# =========================
@app.get("/")
def health():
    return {"status": "Sahabi is running"}


@app.get("/docs", include_in_schema=False)
def custom_docs():
    swagger = get_swagger_ui_html(openapi_url="/openapi.json", title="Sahabi API Docs")
    html = swagger.body.decode("utf-8")

    inject = """
    <script>
    window.addEventListener("load", () => {
      const topbar = document.querySelector(".swagger-ui .topbar");
      if (!topbar) return;

      const btn = document.createElement("a");
      btn.href = "/ui/";
      btn.innerText = "MVP";
      btn.style.marginRight = "12px";
      btn.style.padding = "8px 14px";
      btn.style.borderRadius = "999px";
      btn.style.background = "rgba(24,174,230,.14)";
      btn.style.border = "1px solid rgba(24,174,230,.30)";
      btn.style.color = "#2B2F86";
      btn.style.fontWeight = "800";
      btn.style.textDecoration = "none";

      topbar.appendChild(btn);
    });
    </script>
    """
    html = html.replace("</body>", inject + "</body>")
    return HTMLResponse(html)


class AnalyzeRequest(BaseModel):
    repo_url: str


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    scan_id = str(uuid.uuid4())

    try:
        if clone_repo:
            local_path = clone_repo(req.repo_url, scan_id)
        else:
            local_path = _clone_repo_fallback(req.repo_url, scan_id)

        project_path = Path(local_path)
        core = analyze_local_repo(project_path)

        report = {
            "scan_id": scan_id,
            "repo_url": req.repo_url,
            "status": "completed",
            **core,
        }

        REPORT_STORE[scan_id] = report
        return report

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"git clone failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class ChatAsk(BaseModel):
    question: str


@app.post("/chat/{scan_id}", response_class=PlainTextResponse)
def chat(scan_id: str, req: ChatAsk):
    report = REPORT_STORE.get(scan_id)
    if not report:
        raise HTTPException(status_code=404, detail="scan_id not found. Run /analyze first.")

    # 1) If LLM is available, use it
    if llm_answer:
        try:
            result = llm_answer(report, req.question)
            if isinstance(result, dict):
                text = result.get("answer", "")
            else:
                text = str(result)
            return text.replace("\\n", "\n").strip()
        except Exception:
            pass

    # 2) Fallback: deterministic answer based on report
    q = (req.question or "").strip().lower()
    rec = report.get("recommendation", {})
    comp = report.get("compliance", {})
    analysis = report.get("analysis", {})

    if any(k in q for k in ["6r", "strategy", "استراتيجية", "ليش", "why"]):
        reasons = rec.get("reasons", [])[:5]
        return (
            f"التوصية: {rec.get('strategy_ar', rec.get('strategy',''))}\n"
            f"جاهزية الترحيل: {rec.get('readiness_score', 'N/A')}%\n"
            f"نسبة الثقة: {rec.get('confidence', 'N/A')}\n"
            f"أسباب مختصرة:\n- " + "\n- ".join(reasons)
        ).strip()

    if any(k in q for k in ["compliance", "امتثال", "nca", "dga", "security", "أمان"]):
        issues = comp.get("issues", [])
        if not issues:
            return f"الامتثال ممتاز. Compliance score: {comp.get('score','N/A')}%."
        return f"Compliance score: {comp.get('score','N/A')}%\nالملاحظات:\n- " + "\n- ".join(issues[:8])

    if any(k in q for k in ["loc", "lines", "code", "حجم", "لغات", "language"]):
        langs = analysis.get("languages", [])[:6]
        langs_lines = [f"- {x['name']}: {x['percent']}%" for x in langs]
        return (
            f"إجمالي LOC: {analysis.get('lines_of_code','N/A')}\n"
            f"اللغة الأساسية: {analysis.get('main_language','—')}\n"
            f"توزيع اللغات:\n" + "\n".join(langs_lines)
        ).strip()

    return (
        "اسألني بناءً على التقرير مثل:\n"
        "- ليش اخترت استراتيجية 6Rs هذي؟\n"
        "- وش مشاكل الامتثال؟\n"
        "- عطيني ملخص حجم الكود واللغات.\n"
    )

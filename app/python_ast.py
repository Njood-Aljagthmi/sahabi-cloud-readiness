import ast
from pathlib import Path
from collections import defaultdict

EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache"}

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
}

def _count_loc(text: str) -> int:
    # LOC = عدد الأسطر غير الفارغة
    return sum(1 for line in text.splitlines() if line.strip())

def analyze_python_code(project_path: Path) -> dict:
    # === Python AST stuff (مثل كودك)
    py_files = [p for p in project_path.rglob("*.py") if not any(x in EXCLUDE_DIRS for x in p.parts)]
    imports = set()
    python_loc = 0

    for file in py_files:
        try:
            source = file.read_text(encoding="utf-8", errors="ignore")
            python_loc += _count_loc(source)
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        imports.add(n.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])
        except Exception:
            continue

    # === Repo-wide language breakdown
    lang_loc = defaultdict(int)
    total_loc = 0

    for p in project_path.rglob("*"):
        if not p.is_file():
            continue
        if any(x in EXCLUDE_DIRS for x in p.parts):
            continue

        # Dockerfile / Makefile optional
        if p.name == "Dockerfile":
            text = p.read_text(encoding="utf-8", errors="ignore")
            loc = _count_loc(text)
            lang_loc["Dockerfile"] += loc
            total_loc += loc
            continue

        suffix = p.suffix.lower()
        lang = LANG_BY_SUFFIX.get(suffix)
        if not lang:
            continue

        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        loc = _count_loc(text)
        lang_loc[lang] += loc
        total_loc += loc

    # main language + percentages
    if total_loc > 0:
        main_language = max(lang_loc.items(), key=lambda x: x[1])[0] if lang_loc else "—"
        languages = [
            {"name": k, "percent": round((v / total_loc) * 100, 1)}
            for k, v in sorted(lang_loc.items(), key=lambda x: x[1], reverse=True)
        ]
    else:
        main_language = "—"
        languages = []

    return {
        # هذا الجزء واجهتك تحتاجه
        "lines_of_code": total_loc,
        "main_language": main_language,
        "languages": languages,

        # وهذا يبقى مثل كودك
        "python_files": len(py_files),
        "python_loc": python_loc,
        "imports": sorted(imports),
    }

#  Sahabi | سحابي – AI-Powered Cloud Readiness Assessment

 **1st Place – Saudi Open-Source Hackathon (Dec 2025)**

Sahabi analyzes an application's source code and tells you **how ready it is to move to the cloud**, then recommends a migration strategy based on the **6 R's model**. It includes an interactive Arabic dashboard and an LLM-powered chatbot that guides migration decisions in line with Saudi **DGA** and **NCA** regulations.

##  Features

-  **Code analysis:** clones a repository and scans it (languages, lines of code, Python AST analysis, Dockerfile, dependency files, frameworks such as Flask / Django)
-  **6 R's scoring:** scores each strategy (Rehost, Replatform, Refactor, Re-architect, Retain, Retire) and explains the reasons behind each recommendation
-  **AI chatbot:** answers migration questions with regulatory guidance (DGA / NCA)
-  **Interactive dashboard:** Arabic (RTL) dashboard with charts
-  **API docs:** FastAPI with Swagger UI

##  Tech Stack

Python · FastAPI · Pydantic · OpenAI API · Tailwind CSS · Chart.js

##  Structure

```
app/
├── main.py          # FastAPI app and endpoints
├── scoring.py       # 6 R's scoring engine
├── python_ast.py    # Source code analyzer
└── dashboard/
    └── index.html   # Interactive dashboard
```

##  Team

Built by a team of 5 during the Saudi Open-Source Hackathon.

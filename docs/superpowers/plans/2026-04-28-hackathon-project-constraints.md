# Hackathon-Project-Constraints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettre de créer des hackathons avec des critères mixtes (notation + contraintes techniques). Les projets sont soumis dans un hackathon, et l'IA évalue chaque critère en posant une question et en extrayant un score.

**Architecture:** 
- Les hackathons ont un tableau `criteria` JSONB contenant tous les critères
- Chaque critère a un `name`, une `description` (question pour l'IA), et optionnellement `required` (liste d'éléments à vérifier)
- Les projets sont liés à un hackathon
- L'IA analyse le code et génère une évaluation par critère
- Le score global = moyenne des scores (poids égaux)

**Tech Stack:** Python, FastAPI, PostgreSQL, LangChain, OpenRouter

---

## Task 1: Modifier le schéma de base de données

**Files:**
- Modify: `db.py`

- [ ] **Step 1: Lire le fichier db.py actuel**

```python
import psycopg2
from psycopg2.extras import RealDictCursor
import os

def get_database_connection():
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        database="judgy",
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres")
    )
    return conn
```

- [ ] **Step 2: Ajouter colonnes hackathon_id, evaluation_criteria, weights à projects**

```python
def init_db():
    conn = get_database_connection()
    cur = conn.cursor()
    
    # Drop existing tables for clean schema (add migration logic in production)
    cur.execute("DROP TABLE IF EXISTS evaluations CASCADE")
    cur.execute("DROP TABLE IF EXISTS projects CASCADE")
    cur.execute("DROP TABLE IF EXISTS hackathons CASCADE")
    
    # Create hackathons table with criteria
    cur.execute("""
        CREATE TABLE hackathons (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            description TEXT DEFAULT '',
            theme TEXT DEFAULT '',
            is_allowed BOOLEAN DEFAULT FALSE,
            criteria TEXT DEFAULT '',  -- "Code Quality, Tech Stack (React,Node.js), Innovation"
            deadline TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create projects table with hackathon relation
    cur.execute("""
        CREATE TABLE projects (
            id SERIAL PRIMARY KEY,
            project_id VARCHAR(255) UNIQUE NOT NULL,
            hackathon_id INTEGER REFERENCES hackathons(id) ON DELETE SET NULL,
            short_description TEXT DEFAULT '',
            long_description TEXT DEFAULT '',
            github_link TEXT DEFAULT '',
            theme TEXT DEFAULT '',
            is_reviewed BOOLEAN DEFAULT FALSE,
            code_agent_analysis JSONB DEFAULT '[]'::jsonb,
            market_agent_analysis JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create evaluations table
    cur.execute("""
        CREATE TABLE evaluations (
            id SERIAL PRIMARY KEY,
            project_id VARCHAR(255) REFERENCES projects(project_id) ON DELETE CASCADE,
            criteria_name VARCHAR(255) NOT NULL,
            score DECIMAL(3,2) DEFAULT 0.00,  -- 0.00 to 1.00
            remarks TEXT DEFAULT '',
            agent_type VARCHAR(50) DEFAULT 'code',  -- 'code', 'market', 'manual'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_hackathon_id ON projects(hackathon_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_project_id ON projects(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_evaluations_project_id ON evaluations(project_id)")
    
    conn.commit()
    cur.close()
    conn.close()
```

- [ ] **Step 3: Vérifier que les tables sont créées**

Run: `python -c "from db import init_db; init_db(); print('Tables created successfully')"`

---

## Task 2: Ajouter les endpoints CRUD pour les hackathons

**Files:**
- Modify: `agents/crudagent.py`

- [ ] **Step 1: Ajouter endpoint pour créer un hackathon avec critères**

```python
@router.post("/create-hackathon")
async def create_hackathon(request: Request):
    data = await request.json()
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO hackathons (name, description, theme, is_allowed, criteria, deadline)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            data.get("name", ""),
            data.get("description", ""),
            data.get("theme", ""),
            data.get("isAllowed", False),
            data.get("criteria", ""),  -- "Code Quality, Tech Stack (React,Node.js), Innovation"
            data.get("deadline", None)
        )
    )
    hackathon_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    
    return {"message": "Hackathon created", "hackathon_id": hackathon_id}
```

- [ ] **Step 2: Ajouter endpoint pour récupérer un hackathon**

```python
@router.get("/get-hackathon/{hackathon_id}")
async def get_hackathon(hackathon_id: int):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM hackathons WHERE id = %s", (hackathon_id,))
    hackathon = cur.fetchone()
    cur.close()
    conn.close()
    
    if hackathon and hackathon.get("created_at"):
        hackathon["created_at"] = hackathon["created_at"].isoformat()
    if hackathon and hackathon.get("deadline"):
        hackathon["deadline"] = hackathon["deadline"].isoformat()
    
    return {"message": "successful", "hackathon": hackathon}
```

- [ ] **Step 3: Ajouter endpoint pour lister tous les hackathons**

```python
@router.get("/get-all-hackathons")
async def get_all_hackathons():
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM hackathons ORDER BY created_at DESC")
    hackathons = cur.fetchall()
    cur.close()
    conn.close()
    
    result = []
    for h in hackathons:
        h["_id"] = str(h["id"])
        del h["id"]
        if h.get("created_at"):
            h["created_at"] = h["created_at"].isoformat()
        if h.get("deadline"):
            h["deadline"] = h["deadline"].isoformat()
        result.append(h)
    
    return {"message": "successful", "hackathons": result}
```

- [ ] **Step 4: Tester la création d'un hackathon**

Run: `curl -X POST http://localhost:8000/api/create-hackathon -H "Content-Type: application/json" -d '{"name": "AI Hackathon", "technologies": "Python,FastAPI,LangChain", "judgingCriteria": [{"name": "Code Quality", "weight": 0.3}, {"name": "Innovation", "weight": 0.4}, {"name": "Market Potential", "weight": 0.3}]}'`

---

## Task 3: Modifier la soumission de projet pour inclure hackathon_id

**Files:**
- Modify: `agents/crudagent.py`

- [ ] **Step 1: Modifier create_project pour accepter hackathon_id**

```python
@router.post("/create-project")
async def create_project(request: Request):
    data = await request.json()
    print(data)
    
    project_id = str(uuid.uuid4())
    hackathon_id = data.get("hackathonId")
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO projects (project_id, hackathon_id, short_description, long_description, github_link, theme, is_reviewed)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (project_id, hackathon_id, data.get("shortDescription", ""), data.get("longDescription", ""), 
         data.get("githubLink", ""), data.get("theme", ""), False)
    )
    conn.commit()
    cur.close()
    conn.close()

    # Run agents with hackathon context
    if hackathon_id:
        asyncio.create_task(invoke_market_agent(project_id, data.get("shortDescription", ""), hackathon_id))
        asyncio.create_task(invoke_code_agent(data.get("githubLink", ""), project_id, hackathon_id))
    
    return {"message": "Project created", "project_id": project_id}
```

- [ ] **Step 2: Ajouter endpoint pour lister les projets d'un hackathon**

```python
@router.get("/get-hackathon-projects/{hackathon_id}")
async def get_hackathon_projects(hackathon_id: int):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM projects WHERE hackathon_id = %s ORDER BY created_at DESC", (hackathon_id,))
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    result = []
    for p in projects:
        p["_id"] = str(p["id"])
        del p["id"]
        if p.get("created_at"):
            p["created_at"] = p["created_at"].isoformat()
        result.append(p)
    
    return {"message": "successful", "projects": result}
```

- [ ] **Step 3: Tester la soumission avec hackathon_id**

Run: `curl -X POST http://localhost:8000/api/create-project -H "Content-Type: application/json" -d '{"shortDescription": "My AI Project", "githubLink": "https://github.com/user/repo", "hackathonId": 1}'`

---

## Task 4: Modifier le code agent pour utiliser les contraintes du hackathon

**Files:**
- Modify: `agents/codeagent.py`

- [ ] **Step 1: Modifier invoke_code_agent pour utiliser les critères du hackathon**

```python
async def invoke_code_agent(repolink: str, project_id: str, hackathon_id: int = None):
    """Background task for automatic code analysis"""
    temp_dir = f"./projects_source_code/{project_id}"
    
    try:
        # Get hackathon criteria (text format)
        criteria_text = ""
        if hackathon_id:
            conn = get_database_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT criteria FROM hackathons WHERE id = %s", (hackathon_id,))
            hackathon = cur.fetchone()
            if hackathon:
                criteria_text = hackathon["criteria"] or ""
            cur.close()
            conn.close()
        
        print(f"Cloning {repolink} into {temp_dir}")
        repo = Repo.clone_from(repolink, to_path=temp_dir)
        # ... existing code to clone, index ...
```

- [ ] **Step 2: Évaluer chaque critère directement**

```python
        # Split criteria by comma: "Code Quality, Tech Stack, Innovation"
        criteria_list = [c.strip() for c in criteria_text.split(',') if c.strip()]
        
        # Generate questions for each criteria
        default_questions = {
            "Code Quality": "How is the code quality? Rate 0-10.",
            "Tech Stack": "What technologies and frameworks are used?",
            "Innovation": "How innovative is this project? Rate 0-10.",
            "Market Potential": "What is the market potential? Rate 0-10.",
        }
        
        for name in criteria_list:
            question = default_questions.get(name, f"Evaluate {name}. Rate 0-10.")
            answer = query_codebase(vectorstore, question, llm)
            
            if "Rate 0-10" in question:
                score = extract_score_from_text(answer)
                save_evaluation(project_id, name, score, answer, "code")
            else:
                # For tech stack, check keywords
                score = 1.0 if answer else 0.5
                save_evaluation(project_id, name, score, answer, "code")
```



- [ ] **Step 4: Ajouter fonction pour sauvegarder l'évaluation**

```python
def save_evaluation(project_id: str, criteria_name: str, score: float, remarks: str, agent_type: str):
    """Save evaluation to database"""
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO evaluations (project_id, criteria_name, score, remarks, agent_type)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (project_id, criteria_name, score, remarks, agent_type)
    )
    conn.commit()
    cur.close()
    conn.close()
```

---

## Task 5: Modifier le market agent pour utiliser les critères du hackathon

**Files:**
- Modify: `agents/marketagent.py`

- [ ] **Step 1: Lire le market agent actuel et le modifier**

```python
async def invoke_market_agent(project_id: str, short_description: str, hackathon_id: int = None):
    """Background task for market analysis"""
    try:
        # Get hackathon criteria
        judging_criteria = []
        if hackathon_id:
            conn = get_database_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT judging_criteria FROM hackathons WHERE id = %s", (hackathon_id,))
            hackathon = cur.fetchone()
            if hackathon:
                judging_criteria = hackathon["judging_criteria"] or []
            cur.close()
            conn.close()
        
        # ... existing market analysis code ...
        
        # Save evaluations based on criteria
        for criteria in judging_criteria:
            criteria_name = criteria.get("name", "Market Analysis")
            score = extract_market_score(analysis_text)
            save_evaluation(project_id, criteria_name, score, analysis_text, "market")
```

---

## Task 6: Ajouter endpoint pour calculer le score global

**Files:**
- Modify: `agents/crudagent.py`

- [ ] **Step 1: Ajouter fonction pour calculer le score global**

```python
@router.get("/get-project-score/{project_id}")
async def get_project_score(project_id: str):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Get project and its hackathon
    cur.execute("""
        SELECT p.*, h.criteria, h.name as hackathon_name 
        FROM projects p 
        LEFT JOIN hackathons h ON p.hackathon_id = h.id 
        WHERE p.project_id = %s
    """, (project_id,))
    project = cur.fetchone()
    
    if not project:
        cur.close()
        conn.close()
        return {"message": "error", "error": "Project not found"}
    
    # Get all evaluations for this project
    cur.execute("""
        SELECT criteria_name, score, remarks 
        FROM evaluations 
        WHERE project_id = %s
    """, (project_id,))
    evaluations = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # Calculate score (simple average)
    total_score = sum(float(e["score"]) for e in evaluations) / len(evaluations) if evaluations else 0
    
    scores = {}
    for eval in evaluations:
        scores[eval["criteria_name"]] = {
            "score": float(eval["score"]),
            "remarks": eval["remarks"]
        }
    
    return {
        "message": "successful",
        "project_id": project_id,
        "hackathon_name": project.get("hackathon_name"),
        "total_score": round(total_score, 2),
        "scores": scores
    }
```

- [ ] **Step 2: Ajouter endpoint pour le classement des projets d'un hackathon**

```python
@router.get("/get-hackathon-leaderboard/{hackathon_id}")
async def get_hackathon_leaderboard(hackathon_id: int):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Get hackathon
    cur.execute("SELECT criteria, name FROM hackathons WHERE id = %s", (hackathon_id,))
    hackathon = cur.fetchone()
    if not hackathon:
        cur.close()
        conn.close()
        return {"message": "error", "error": "Hackathon not found"}
    
    criteria_list = [c.strip() for c in (hackathon["criteria"] or "").split(',') if c.strip()]
    num_criteria = len(criteria_list)
    weight = 1.0 / num_criteria if num_criteria else 1.0
    
    # Get all projects
    cur.execute("SELECT project_id, short_description, github_link FROM projects WHERE hackathon_id = %s", (hackathon_id,))
    projects = cur.fetchall()
    
    ranked = []
    for proj in projects:
        cur.execute("SELECT criteria_name, score FROM evaluations WHERE project_id = %s", (proj["project_id"],))
        evals = cur.fetchall()
        
        # Score global = moyenne des scores
        total_score = sum(float(e["score"]) for e in evals) / len(evals) if evals else 0
        ranked.append({
            "project_id": proj["project_id"],
            "short_description": proj["short_description"],
            "github_link": proj["github_link"],
            "score": round(total, 2)
        })
    
    cur.close()
    conn.close()
    ranked.sort(key=lambda x: x["score"], reverse=True)
    
    return {"message": "successful", "hackathon_name": hackathon["name"], "leaderboard": ranked}
```

---

## Task 7: Mettre à jour AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Ajouter la documentation des nouveaux endpoints**

```markdown
## API Endpoints

### Hackathons
- `POST /api/create-hackathon` - Create hackathon with constraints
- `GET /api/get-hackathon/{id}` - Get hackathon details
- `GET /api/get-all-hackathons` - List all hackathons

### Projects
- `POST /api/create-project` - Submit project to hackathon (requires hackathonId)
- `GET /api/get-project/{id}` - Get project with evaluations
- `GET /api/get-hackathon-projects/{hackathon_id}` - List projects in hackathon

### Scoring
- `GET /api/get-project-score/{project_id}` - Get weighted score for project
- `GET /api/get-hackathon-leaderboard/{hackathon_id}` - Get ranked projects

## Criteria Format

`criteria` TEXT field in hackathons:
```
"Code Quality, Innovation, Tech Stack"
```

L'IA utilise chaque nom directement comme critère d'évaluation (avec question par défaut)
```

---

## Verification Checklist

- [ ] Server starts without errors
- [ ] Can create a hackathon with judging criteria
- [ ] Can submit a project to a hackathon
- [ ] Code agent produces evaluations with scores
- [ ] Market agent produces evaluations with scores
- [ ] Project score endpoint returns weighted score
- [ ] Leaderboard endpoint returns ranked projects
- [ ] Tests pass (if any exist)
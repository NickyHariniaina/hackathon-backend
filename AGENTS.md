# AGENTS.md

## Running the Server

```bash
python server.py
```

Runs on `http://0.0.0.0:8000`

## Setup Requirements

1. Copy `.env.example` to `.env` and fill in values
2. PostgreSQL must be running on localhost:5432 with database `judgy`
3. Install deps: `pip install -r requirements.txt`

## Required Environment Variables

- `OPENROUTER_API_KEY` - LLM API key
- `FREE_LLM_MODEL` - Default: `liquid/lfm-2.5-1.2b-thinking:free`
- `HF_TOKEN` - HuggingFace token
- `EMBEDDING_MODEL` - Default: `sentence-transformers/all-MiniLM-L6-v2`
- `GITHUB_TOKEN` - GitHub token for API rate limits (optional)
- `DB_USER`, `DB_PASSWORD` - PostgreSQL credentials

## Project Structure

- `server.py` - FastAPI entry point; loads `.env` via `dotenv`, initializes DB on startup
- `db.py` - PostgreSQL connection and table creation
- `agents/` - Contains 4 agent routers:
  - `marketagent.py` - Market analysis (web research + score 0-100)
  - `codeagent.py` - Code analysis (GitHub API + score 0-100)
  - `chatagent.py` - Chat functionality
  - `crudagent.py` - CRUD operations

All agents mounted under `/api` prefix.

## Scoring System

### Overview
Projects are evaluated by two AI agents that each give a score 0-100:
- **Code Agent (60%)**: Analyzes GitHub repository code
- **Market Agent (40%)**: Researches market potential via web search

**Total Score = (code_score × 0.6) + (market_score × 0.4)**

### Evaluation Flow
```
POST /api/create-project
  → INSERT project
  → asyncio.create_task(invoke_market_agent(...))
  → asyncio.create_task(invoke_code_agent(...))
  → Returns project_id immediately

Background:
  Code Agent:
    → Fetch repo via GitHub API
    → Build code context
    → LLM evaluates with prompt including hackathon name, theme, criteria
    → Returns JSON: {score: 0-100, summary, strengths, weaknesses, criterion_scores}
    → save_evaluation(project_id, "code", score, result_json)

  Market Agent:
    → Web search for market research
    → LLM evaluates with prompt including hackathon info
    → Returns JSON: {score: 0-100, summary, strengths, weaknesses, criterion_scores}
    → save_evaluation(project_id, "market", score, result_json)
```

### Prompt Template
```
Évalue ce projet pour le hackathon "{name}".
Thème: {theme}
Critères: {criteria}

[Code/Market context...]

Analyse et donne un score 0-100. JSON valide uniquement:
{score: <0-100>, summary: "...", strengths: [...], weaknesses: [...], criterion_scores: {...}}
```

## API Endpoints

### Hackathons
- `POST /api/create-hackathon` - Create hackathon with criteria
- `GET /api/get-hackathon/{id}` - Get hackathon details
- `GET /api/get-all-hackathons` - List all hackathons

### Projects
- `POST /api/create-project` - Submit project (triggers async analysis)
- `GET /api/get-project/{id}` - Get project with scores
- `GET /api/get-hackathon-projects/{hackathon_id}` - List projects in hackathon
- `GET /api/get-all` - List all projects

### Scoring
- `GET /api/get-project-score/{project_id}` - Get code, market, and total score
- `GET /api/get-hackathon-leaderboard/{hackathon_id}` - Get ranked projects

### Legacy
- `POST /api/search` - Semantic search projects
- `POST /api/review` - Mark project as reviewed

## Database Schema

```
hackathons:
  id, name, description, theme, is_allowed, criteria, deadline, created_at

projects:
  id, project_id (UUID), hackathon_id (FK nullable),
  short_description, long_description, github_link, theme,
  is_reviewed, created_at

evaluations:
  id, project_id (FK), agent_type ('code'|'market'),
  score (0-100), result_json (JSONB), created_at
  UNIQUE(project_id, agent_type)
```

## Test Commands

```bash
# Start server
python server.py

# 1. Create a hackathon
curl -X POST http://localhost:8000/api/create-hackathon \
  -H "Content-Type: application/json" \
  -d '{"name": "AI Hackathon", "theme": "AI/ML Projects", "criteria": "Code Quality, Innovation, Tech Stack, Market Potential"}'

# 2. Submit a project
curl -X POST http://localhost:8000/api/create-project \
  -H "Content-Type: application/json" \
  -d '{"shortDescription": "My AI Project", "githubLink": "https://github.com/user/repo", "hackathonId": 1}'

# 3. Check project score (after analysis completes)
curl http://localhost:8000/api/get-project-score/{project_id}

# 4. Get leaderboard
curl http://localhost:8000/api/get-hackathon-leaderboard/1

# OpenAPI docs
# Swagger UI: http://localhost:8000/docs
# ReDoc: http://localhost:8000/redoc
```
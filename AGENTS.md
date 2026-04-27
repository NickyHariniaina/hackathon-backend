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
- `DB_USER`, `DB_PASSWORD` - PostgreSQL credentials

## Project Structure

- `server.py` - FastAPI entry point; loads `.env` via `dotenv`, initializes DB on startup
- `db.py` - PostgreSQL connection (`host=localhost:5432, database=judgy`) and table creation
- `agents/` - Contains 4 agent routers:
  - `marketagent.py` - Market analysis
  - `codeagent.py` - Code analysis
  - `chatagent.py` - Chat functionality
  - `crudagent.py` - CRUD operations

All agents mounted under `/api` prefix.

## API Endpoints

### Hackathons
- `POST /api/create-hackathon` - Create hackathon with `criteria`
- `GET /api/get-hackathon/{id}` - Get hackathon details
- `GET /api/get-all-hackathons` - List all hackathons

### Projects
- `POST /api/create-project` - Submit project to hackathon (`hackathonId` required)
- `GET /api/get-project/{id}` - Get project details
- `GET /api/get-hackathon-projects/{id}` - List projects in hackathon
- `GET /api/get-all` - List all projects

### Scoring
- `GET /api/get-project-score/{id}` - Get total score and evaluations
- `GET /api/get-hackathon-leaderboard/{id}` - Get ranked projects

### Legacy
- `POST /api/search` - Semantic search projects
- `POST /api/review` - Mark project as reviewed

## Database Schema

```
hackathons: id, name, description, theme, is_allowed, criteria TEXT, deadline, created_at
projects: id, project_id, hackathon_id (FK), short_description, long_description, github_link, theme, is_reviewed, code_agent_analysis (JSONB), market_agent_analysis (JSONB), created_at
evaluations: id, project_id (FK), criteria_name, score, remarks, agent_type, created_at
```

## Criteria Format

`criteria` TEXT in hackathons: `"Code Quality, Innovation, Tech Stack"`

L'IA génère une question par défaut pour chaque critère.

## Test Commands

```bash
# Create hackathon
curl -X POST http://localhost:8000/api/create-hackathon \
  -H "Content-Type: application/json" \
  -d '{"name": "AI Hack", "criteria": "Code Quality, Innovation"}'

# Submit project
curl -X POST http://localhost:8000/api/create-project \
  -H "Content-Type: application/json" \
  -d '{"shortDescription": "My Project", "githubLink": "https://github.com/user/repo", "hackathonId": 1}'
```
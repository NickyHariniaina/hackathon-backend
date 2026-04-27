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
- `POST /api/create-hackathon` - Create hackathon with `criteria` (e.g., "Code Quality, Innovation, Tech Stack")
- `GET /api/get-hackathon/{id}` - Get hackathon details
- `GET /api/get-all-hackathons` - List all hackathons

### Projects
- `POST /api/create-project` - Submit project to hackathon (requires `hackathonId`)
- `GET /api/get-project/{id}` - Get project with evaluations
- `GET /api/get-hackathon-projects/{hackathon_id}` - List projects in hackathon

### Scoring
- `GET /api/get-project-score/{project_id}` - Get total score and evaluation details
- `GET /api/get-hackathon-leaderboard/{hackathon_id}` - Get ranked projects

## Database Schema

```
hackathons: id, name, description, theme, is_allowed, criteria TEXT, deadline, created_at
projects: id, project_id, hackathon_id (FK), short_description, long_description, github_link, theme, is_reviewed, code_agent_analysis (JSONB), market_agent_analysis (JSONB), created_at
evaluations: id, project_id (FK), criteria_name, score DECIMAL, remarks, agent_type, created_at
```

## Criteria Format

`criteria` TEXT field in hackathons:
```
"Code Quality, Innovation, Tech Stack"
```

The AI uses each name to generate a default evaluation question.

## Test Commands

```bash
# Start server
python server.py

# 1. Create a hackathon with criteria
curl -X POST http://localhost:8000/api/create-hackathon \
  -H "Content-Type: application/json" \
  -d '{"name": "AI Hackathon", "description": "Build with AI", "criteria": "Code Quality, Innovation, Tech Stack"}'

# 2. Get all hackathons
curl http://localhost:8000/api/get-all-hackathons

# 3. Get specific hackathon
curl http://localhost:8000/api/get-hackathon/1

# 4. Submit project to hackathon
curl -X POST http://localhost:8000/api/create-project \
  -H "Content-Type: application/json" \
  -d '{"shortDescription": "My AI Project", "githubLink": "https://github.com/user/repo", "hackathonId": 1}'

# 5. Get project (after evaluation)
curl http://localhost:8000/api/get-project/{project_id}

# 6. Get projects in hackathon
curl http://localhost:8000/api/get-hackathon-projects/1

# 7. Get project score
curl http://localhost:8000/api/get-project-score/{project_id}

# 8. Get leaderboard
curl http://localhost:8000/api/get-hackathon-leaderboard/1
```
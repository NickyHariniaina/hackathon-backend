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

## Database Tables

- `hackathons` - id, technologies, theme, is_allowed, created_at
- `projects` - id, project_id, short_description, long_description, github_link, theme, is_reviewed, code_agent_analysis (JSONB), market_agent_analysis (JSONB), created_at
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

def init_db():
    conn = get_database_connection()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hackathons (
            id SERIAL PRIMARY KEY,
            technologies TEXT DEFAULT '',
            theme TEXT DEFAULT '',
            is_allowed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY,
            project_id VARCHAR(255) UNIQUE NOT NULL,
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
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_project_id ON projects(project_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_is_reviewed ON projects(is_reviewed)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects(created_at)")
    
    conn.commit()
    cur.close()
    conn.close()
-- Hackathon Backend Database Schema
-- PostgreSQL database: judgy
-- Host: localhost, Port: 5432

-- Drop tables if needed (optional, comment out for production)
-- DROP TABLE IF EXISTS evaluations CASCADE;
-- DROP TABLE IF EXISTS projects CASCADE;
-- DROP TABLE IF EXISTS hackathons CASCADE;

-- Create project_type enum
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'project_type') THEN
        CREATE TYPE project_type AS ENUM (
            'VANILLA_JS',
            'REACT',
            'NEXT_JS',
            'VUE',
            'NUXT',
            'ANGULAR',
            'SVELTE',
            'SVELTEKIT',
            'ASTRO',
            'REMIX',
            'TAILWIND',
            'NODE_EXPRESS',
            'FASTAPI',
            'DJANGO',
            'SPRING_BOOT',
            'GIN',
            'RAILS',
            'LARAVEL',
            'ACTIX',
            'SWIFT_UI',
            'KOTLIN_JETPACK',
            'REACT_NATIVE',
            'EXPO',
            'FLUTTER',
            'DOTNET_MAUI',
            'IONIC',
            'NATIVESCRIPT',
            'OTHER'
        );
    END IF;
END
$$;

-- Create hackathons table
CREATE TABLE IF NOT EXISTS hackathons (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    theme TEXT DEFAULT '',
    is_allowed BOOLEAN DEFAULT FALSE,
    criteria TEXT DEFAULT '',
    deadline TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create projects table
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(255) UNIQUE NOT NULL,
    hackathon_id INTEGER REFERENCES hackathons(id) ON DELETE SET NULL,
    short_description TEXT DEFAULT '',
    long_description TEXT DEFAULT '',
    github_link TEXT DEFAULT '',
    demo_link TEXT DEFAULT NULL,
    theme TEXT DEFAULT '',
    is_reviewed BOOLEAN DEFAULT FALSE,
    code_agent_analysis JSONB DEFAULT '[]'::jsonb,
    market_agent_analysis JSONB DEFAULT '[]'::jsonb,
    overall_score DECIMAL(3,2) DEFAULT NULL,
    score_explanation TEXT DEFAULT '',
    project_type project_type DEFAULT 'OTHER',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create evaluations table (legacy, kept for reference)
CREATE TABLE IF NOT EXISTS evaluations (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(255) REFERENCES projects(project_id) ON DELETE CASCADE,
    criteria_name VARCHAR(255) NOT NULL,
    score DECIMAL(3,2) DEFAULT 0.00,
    remarks TEXT DEFAULT '',
    agent_type VARCHAR(50) DEFAULT 'code',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_projects_hackathon_id ON projects(hackathon_id);
CREATE INDEX IF NOT EXISTS idx_projects_project_id ON projects(project_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_project_id ON evaluations(project_id);

-- Migration: Add new columns if they don't exist (safe to run multiple times)
ALTER TABLE projects ADD COLUMN IF NOT EXISTS overall_score DECIMAL(3,2) DEFAULT NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS score_explanation TEXT DEFAULT '';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS demo_link TEXT DEFAULT NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS project_type project_type DEFAULT 'OTHER';

-- Sample data (optional)
-- INSERT INTO hackathons (name, description, theme, is_allowed, criteria)
-- VALUES ('AI Hackathon 2026', 'Build innovative AI solutions', 'Artificial Intelligence', TRUE, 'Code Quality, Innovation, Tech Stack, Market Potential')
-- ON CONFLICT DO NOTHING;

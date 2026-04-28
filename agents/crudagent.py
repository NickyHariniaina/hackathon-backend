from fastapi import APIRouter, Request, HTTPException
from fastapi import Query
from typing import Optional
from db import get_database_connection
from agents.codeagent import invoke_code_agent
import asyncio
from agents.marketagent import invoke_market_agent
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import os
from psycopg2.extras import RealDictCursor
import uuid
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

def get_llm():
    return ChatOpenAI(
        model=os.getenv("FREE_LLM_MODEL", "liquid/lfm-2.5-1.2b-thinking:free"),
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0
    )

def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name=os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    )

def semantic_search(query, documents, k=10):
    if not documents:
        return []
    
    embeddings = get_embeddings()
    
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    
    docs = []
    for doc in documents:
        chunks = text_splitter.split_text(doc)
        for chunk in chunks:
            docs.append(Document(page_content=chunk))
    
    if not docs:
        return []
    
    vectorstore = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name=f"search_{uuid.uuid4().hex[:8]}"
    )
    
    retriever = vectorstore.as_retriever(search_kwargs={"k": k})
    results = retriever.get_relevant_documents(query)
    
    return [doc.page_content for doc in results]

def calculate_total_score(project_id: str) -> dict:
    """Calculate weighted total score: 60% code + 40% market"""
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT agent_type, score, result_json FROM evaluations WHERE project_id = %s", (project_id,))
    evaluations = cur.fetchall()
    cur.close()
    conn.close()
    
    code_score = 0
    market_score = 0
    code_result = None
    market_result = None
    
    for eval in evaluations:
        if eval["agent_type"] == "code":
            code_score = float(eval["score"]) if eval["score"] else 0
            code_result = eval["result_json"]
        elif eval["agent_type"] == "market":
            market_score = float(eval["score"]) if eval["score"] else 0
            market_result = eval["result_json"]
    
    total_score = (code_score * 0.6) + (market_score * 0.4)
    
    return {
        "code_score": code_score,
        "market_score": market_score,
        "total_score": round(total_score, 2),
        "code_result": code_result,
        "market_result": market_result
    }

@router.get("/crud-agent")
def crudAgent_endpoint():
    return {"message": "Crud Agent is running"}

@router.post("/create-project", tags=["Projects"], summary="Create project and trigger AI analysis")
async def create_project(request: Request):
    """Create a new project. Triggers code and market analysis asynchronously."""
    data = await request.json()
    print(data)
    
    project_id = str(uuid.uuid4())
    hackathon_id = data.get("hackathonId")
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO projects (project_id, hackathon_id, short_description, long_description, github_link, theme, is_reviewed)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (project_id, hackathon_id, data.get("shortDescription", ""), data.get("longDescription", ""),
         data.get("githubLink", ""), data.get("theme", ""), False)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    asyncio.create_task(invoke_market_agent(project_id, data.get("shortDescription", ""), hackathon_id))
    asyncio.create_task(invoke_code_agent(data.get("githubLink", ""), project_id, hackathon_id))
    
    return {"message": "Project created", "project_id": project_id}

@router.post("/create-hackathon", tags=["Hackathons"], summary="Create a new hackathon")
async def create_hackathon(request: Request):
    """Create a new hackathon with evaluation criteria."""
    data = await request.json()
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO hackathons (name, description, theme, is_allowed, criteria, deadline)
           VALUES (%s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (data.get("name", ""), data.get("description", ""), data.get("theme", ""),
         data.get("isAllowed", False), data.get("criteria", ""), data.get("deadline", None))
    )
    hackathon_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    
    return {"message": "Hackathon created", "hackathon_id": hackathon_id}

@router.get("/get-hackathon/{hackathon_id}", tags=["Hackathons"], summary="Get hackathon details")
async def get_hackathon(hackathon_id: int):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM hackathons WHERE id = %s", (hackathon_id,))
    hackathon = cur.fetchone()
    cur.close()
    conn.close()
    
    if hackathon:
        hackathon["_id"] = str(hackathon["id"])
        del hackathon["id"]
        if hackathon.get("created_at"):
            hackathon["created_at"] = hackathon["created_at"].isoformat()
        if hackathon.get("deadline"):
            hackathon["deadline"] = hackathon["deadline"].isoformat()
    
    return {"message": "successful", "hackathon": hackathon}

@router.get("/get-all-hackathons", tags=["Hackathons"], summary="List all hackathons")
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

@router.get("/get-hackathon-projects/{hackathon_id}", tags=["Projects"], summary="List projects in a hackathon")
async def get_hackathon_projects(hackathon_id: int):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT project_id, short_description, long_description, github_link, created_at FROM projects WHERE hackathon_id = %s ORDER BY created_at DESC", (hackathon_id,))
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    result = []
    for p in projects:
        p["_id"] = p["project_id"]
        if p.get("created_at"):
            p["created_at"] = p["created_at"].isoformat()
        result.append(p)
    
    return {"message": "successful", "projects": result}

@router.get("/get-project-score/{project_id}", tags=["Scoring"], summary="Get project score")
async def get_project_score(project_id: str):
    """Get total score (60% code + 40% market) and evaluation details."""
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("""
        SELECT p.project_id, p.short_description, p.github_link, h.name as hackathon_name, h.criteria
        FROM projects p
        LEFT JOIN hackathons h ON p.hackathon_id = h.id
        WHERE p.project_id = %s""", (project_id,))
    project = cur.fetchone()
    
    if not project:
        cur.close()
        conn.close()
        return {"message": "error", "error": "Project not found"}
    
    scores = calculate_total_score(project_id)
    cur.close()
    conn.close()
    
    return {
        "message": "successful",
        "project_id": project_id,
        "short_description": project["short_description"],
        "hackathon_name": project["hackathon_name"],
        "code_score": scores["code_score"],
        "market_score": scores["market_score"],
        "total_score": scores["total_score"],
        "code_evaluation": scores["code_result"],
        "market_evaluation": scores["market_result"]
    }

@router.get("/get-hackathon-leaderboard/{hackathon_id}", tags=["Scoring"], summary="Get hackathon leaderboard")
async def get_hackathon_leaderboard(hackathon_id: int):
    """Get ranked projects for a hackathon based on weighted total score."""
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT name FROM hackathons WHERE id = %s", (hackathon_id,))
    hackathon = cur.fetchone()
    if not hackathon:
        cur.close()
        conn.close()
        return {"message": "error", "error": "Hackathon not found"}
    
    cur.execute("SELECT project_id, short_description, github_link FROM projects WHERE hackathon_id = %s", (hackathon_id,))
    projects = cur.fetchall()
    
    ranked = []
    for proj in projects:
        scores = calculate_total_score(proj["project_id"])
        ranked.append({
            "project_id": proj["project_id"],
            "short_description": proj["short_description"],
            "github_link": proj["github_link"],
            "code_score": scores["code_score"],
            "market_score": scores["market_score"],
            "total_score": scores["total_score"]
        })
    
    cur.close()
    conn.close()
    ranked.sort(key=lambda x: x["total_score"], reverse=True)
    
    return {"message": "successful", "hackathon_name": hackathon["name"], "leaderboard": ranked}

@router.get("/get-project/{project_id}")
async def get_project(project_id: str):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM projects WHERE project_id = %s", (project_id,))
    project = cur.fetchone()
    cur.close()
    conn.close()
    
    if project:
        project["_id"] = str(project["id"])
        del project["id"]
        if project.get("created_at"):
            project["created_at"] = project["created_at"].isoformat()
        
        scores = calculate_total_score(project_id)
        project["code_score"] = scores["code_score"]
        project["market_score"] = scores["market_score"]
        project["total_score"] = scores["total_score"]
    
    return {"message": "successful", "project": project}

@router.get("/get-all")
async def get_all_projects():
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT project_id, hackathon_id, short_description, github_link, created_at FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    result = []
    for project in projects:
        project["_id"] = project["project_id"]
        if project.get("created_at"):
            project["created_at"] = project["created_at"].isoformat()
        result.append(project)
    
    return {"message": "successful", "projects": result}

@router.post("/review")
async def review_project(request: Request):
    data = await request.json()
    project_id = data["project_id"]
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute("UPDATE projects SET is_reviewed = %s WHERE project_id = %s", (data.get("isReviewed", False), project_id))
    conn.commit()
    cur.close()
    conn.close()
    
    return {"message": "successful", "project_id": project_id}

@router.post("/search")
async def search_projects(request: Request):
    data = await request.json()
    query = data.get("query", "")
    
    if not query:
        return {"message": "error", "error": "Query is required"}
    
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT project_id, short_description, long_description, github_link, created_at FROM projects")
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    if not projects:
        return {"message": "successful", "projects": []}
    
    formatted_projects = []
    documents = []
    
    for project in projects:
        project_dict = dict(project)
        formatted_projects.append(project_dict)
        search_text = f"Project: {project_dict.get('short_description', '')} {project_dict.get('long_description', '')}"
        documents.append(search_text)
    
    try:
        relevant_docs = semantic_search(query, documents, k=min(10, len(documents)))
        
        results = []
        seen_ids = set()
        
        for doc in relevant_docs:
            for project in formatted_projects:
                pid = project["project_id"]
                if pid not in seen_ids:
                    search_text = f"Project: {project.get('short_description', '')} {project.get('long_description', '')}"
                    if doc in search_text:
                        project["_id"] = pid
                        scores = calculate_total_score(pid)
                        project["total_score"] = scores["total_score"]
                        results.append(project)
                        seen_ids.add(pid)
        
        return {"message": "successful", "projects": results}
    
    except Exception as e:
        print(f"Search error: {e}")
        results = []
        query_lower = query.lower()
        
        for project in formatted_projects:
            if query_lower in project.get('short_description', '').lower() or query_lower in project.get('long_description', '').lower():
                project["_id"] = project["project_id"]
                results.append(project)
        
        return {"message": "successful", "projects": results[:10]}
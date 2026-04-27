from fastapi import APIRouter, Request
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
import numpy as np
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
    """Search documents using embeddings similarity"""
    if not documents:
        return []
    
    embeddings = get_embeddings()
    
    # Create temporary vectorstore for search
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    
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

@router.get("/crud-agent")
def crudAgent_endpoint():
    return {"message": "Hello from Crud Agent, Okay I'm not really an agent"}

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

    if hackathon_id:
        asyncio.create_task(invoke_market_agent(project_id, data.get("shortDescription", ""), hackathon_id))
        asyncio.create_task(invoke_code_agent(data.get("githubLink", ""), project_id, hackathon_id))
    else:
        asyncio.create_task(invoke_market_agent(project_id, data.get("shortDescription", ""), None))
        asyncio.create_task(invoke_code_agent(data.get("githubLink", ""), project_id, None))
    
    return {"message": "Project created", "project_id": project_id}

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
        (data.get("name", ""), data.get("description", ""), data.get("theme", ""),
         data.get("isAllowed", False), data.get("criteria", ""), data.get("deadline", None))
    )
    hackathon_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return {"message": "Hackathon created", "hackathon_id": hackathon_id}


@router.get("/get-hackathon/{hackathon_id}")
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

@router.get("/get-project-score/{project_id}")
async def get_project_score(project_id: str):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
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
    
    cur.execute("SELECT criteria_name, score, remarks FROM evaluations WHERE project_id = %s", (project_id,))
    evaluations = cur.fetchall()
    cur.close()
    conn.close()
    
    total_score = sum(float(e["score"]) for e in evaluations) / len(evaluations) if evaluations else 0
    
    scores = {}
    for eval in evaluations:
        scores[eval["criteria_name"]] = {"score": float(eval["score"]), "remarks": eval["remarks"]}
    
    return {
        "message": "successful",
        "project_id": project_id,
        "hackathon_name": project.get("hackathon_name"),
        "total_score": round(total_score, 2),
        "scores": scores
    }

@router.get("/get-hackathon-leaderboard/{hackathon_id}")
async def get_hackathon_leaderboard(hackathon_id: int):
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT criteria, name FROM hackathons WHERE id = %s", (hackathon_id,))
    hackathon = cur.fetchone()
    if not hackathon:
        cur.close()
        conn.close()
        return {"message": "error", "error": "Hackathon not found"}
    
    cur.execute("SELECT project_id, short_description, github_link FROM projects WHERE hackathon_id = %s", (hackathon_id,))
    projects = cur.fetchall()
    
    ranked = []
    for proj in projects:
        cur.execute("SELECT criteria_name, score FROM evaluations WHERE project_id = %s", (proj["project_id"],))
        evals = cur.fetchall()
        total_score = sum(float(e["score"]) for e in evals) / len(evals) if evals else 0
        ranked.append({
            "project_id": proj["project_id"],
            "short_description": proj["short_description"],
            "github_link": proj["github_link"],
            "score": round(total_score, 2)
        })
    
    cur.close()
    conn.close()
    ranked.sort(key=lambda x: x["score"], reverse=True)
    
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
        if "created_at" in project and project["created_at"]:
            project["created_at"] = project["created_at"].isoformat()
    
    return {"message": "successful", "project": project}

@router.get("/get-all")
async def get_all_projects():
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    final = []
    for project in projects:
        project["_id"] = str(project["id"])
        del project["id"]
        if "created_at" in project and project["created_at"]:
            project["created_at"] = project["created_at"].isoformat()
        final.append(project)
    
    return {"message": "successful", "projects": final}

@router.post("/review")
async def review_project(request: Request):
    data = await request.json()
    project_id = data["project_id"]
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE projects SET is_reviewed = %s WHERE project_id = %s",
        (data["isReviewed"], project_id)
    )
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
    cur.execute("SELECT * FROM projects")
    projects = cur.fetchall()
    cur.close()
    conn.close()
    
    if not projects:
        return {"message": "successful", "projects": []}
    
    # Prepare documents for semantic search
    formatted_projects = []
    documents = []
    
    for project in projects:
        project_dict = dict(project)
        project_dict["_id"] = str(project_dict["id"])
        del project_dict["id"]
        if "created_at" in project_dict and project_dict["created_at"]:
            project_dict["created_at"] = project_dict["created_at"].isoformat()
        formatted_projects.append(project_dict)
        
        # Create searchable text for each project
        search_text = f"""
        Project Description: {project_dict.get('long_description', '')}
        Short Description: {project_dict.get('short_description', '')}
        Theme: {project_dict.get('theme', '')}
        """
        documents.append(search_text)
    
    try:
        # Use semantic search with embeddings
        relevant_docs = semantic_search(query, documents, k=min(10, len(documents)))
        
        # Find matching projects
        results = []
        seen_ids = set()
        
        for doc in relevant_docs:
            for project in formatted_projects:
                if project["_id"] not in seen_ids:
                    search_text = f"""
                    Project Description: {project.get('long_description', '')}
                    Short Description: {project.get('short_description', '')}
                    Theme: {project.get('theme', '')}
                    """
                    if doc in search_text or any(word in search_text for word in doc.split()[:5]):
                        results.append(project)
                        seen_ids.add(project["_id"])
        
        # If no results from semantic search, use LLM to rank projects
        if not results:
            llm = get_llm()
            prompt = ChatPromptTemplate.from_messages([
                ("system", """You are a project search engine. Rank the following projects by relevance to the query.
                Return only the project IDs in order of relevance, separated by commas. Example: "id1,id2,id3"
                
                Projects:
                {projects}
                
                Query: {query}
                
                Relevant project IDs (in order):"""),
            ])
            
            # Limit to first 10 projects for LLM processing
            projects_text = "\n".join([
                f"ID: {p['_id']} - {p.get('short_description', '')[:100]} - {p.get('theme', '')}"
                for p in formatted_projects[:10]
            ])
            
            chain = prompt | llm | StrOutputParser()
            response = chain.invoke({
                "projects": projects_text,
                "query": query
            })
            
            # Parse LLM response to get project IDs
            ranked_ids = [id.strip() for id in response.split(",")]
            
            for pid in ranked_ids:
                for project in formatted_projects:
                    if project["_id"] == pid:
                        results.append(project)
                        break
        
        return {"message": "successful", "projects": results}
    
    except Exception as e:
        # Fallback to simple text matching if semantic search fails
        print(f"Search error: {e}")
        results = []
        query_lower = query.lower()
        
        for project in formatted_projects:
            if (query_lower in project.get('short_description', '').lower() or
                query_lower in project.get('long_description', '').lower() or
                query_lower in project.get('theme', '').lower()):
                results.append(project)
        
        return {"message": "successful", "projects": results[:10]}
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
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO projects (project_id, short_description, long_description, github_link, theme, is_reviewed)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (project_id, data.get("shortDescription", ""), data.get("longDescription", ""), 
         data.get("githubLink", ""), "", False)
    )
    conn.commit()
    cur.close()
    conn.close()

    # Run agents asynchronously
    asyncio.create_task(invoke_market_agent(project_id, data.get("shortDescription", "")))
    asyncio.create_task(invoke_code_agent(data.get("githubLink", ""), project_id))
    
    return {"message": "Project created", "project_id": project_id}

@router.post("/create-hackathon")
async def create_hackathon(request: Request):
    data = await request.json()
    
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO hackathons (technologies, theme, is_allowed)
        VALUES (%s, %s, %s)
        """,
        (data.get("technologies", ""), data.get("theme", ""), data.get("isAllowed", False))
    )
    conn.commit()
    cur.close()
    conn.close()

    return {"message": "Hackathon created"}

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
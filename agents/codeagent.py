from langchain_openai import ChatOpenAI
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from fastapi import APIRouter, Request, HTTPException
import asyncio
import re
from db import get_database_connection
from psycopg2.extras import RealDictCursor
import json
import os
import uuid
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import base64
from dotenv import load_dotenv


def get_github_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
router = APIRouter()


def get_github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def parse_repo_url(repo_url):
    match = re.search(r"github\.com/([^/]+)/([^/?#]+?)(?:\.git)?(?:/|$|[?#])", repo_url)
    if match:
        owner = match.group(1)
        repo = match.group(2).replace(".git", "").split("/")[0]
        return owner, repo
    return None, None


def get_default_branch(owner, repo):
    session = get_github_session()
    url = f"https://api.github.com/repos/{owner}/{repo}"
    response = session.get(url, headers=get_github_headers())
    if response.status_code == 404:
        raise ValueError(f"Repository '{owner}/{repo}' not found.")
    if response.status_code == 403:
        raise ValueError(f"Access forbidden to '{owner}/{repo}'.")
    response.raise_for_status()
    return response.json().get("default_branch", "main")


def get_repo_tree(owner, repo, branch=None):
    session = get_github_session()
    if branch is None:
        branch = get_default_branch(owner, repo)
    
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    response = session.get(url, headers=get_github_headers())
    
    if response.status_code == 404:
        for fallback in ["main", "master", "prod", "develop"]:
            if fallback == branch:
                continue
            url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{fallback}?recursive=1"
            response = session.get(url, headers=get_github_headers())
            if response.status_code == 200:
                branch = fallback
                break
    
    if response.status_code == 404:
        raise ValueError(f"Repository '{owner}/{repo}' not found or has no commits.")
    if response.status_code == 403:
        raise ValueError(f"Access forbidden. Check GITHUB_TOKEN.")
    if response.status_code == 429:
        raise ValueError("GitHub API rate limit exceeded. Set GITHUB_TOKEN in .env.")
    response.raise_for_status()
    return response.json().get("tree", [])


def get_file_content(owner, repo, path, branch="main"):
    session = get_github_session()
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    response = session.get(url, headers=get_github_headers())
    if response.status_code == 404:
        raise FileNotFoundError(f"File '{path}' not found")
    if response.status_code == 403:
        raise PermissionError(f"Access forbidden to '{path}'")
    response.raise_for_status()
    data = response.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    return data.get("content", "")


def fetch_repo_contents(owner, repo, branch=None):
    if branch is None:
        branch = get_default_branch(owner, repo)
    
    tree = get_repo_tree(owner, repo, branch)
    
    code_extensions = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c", ".go",
        ".rs", ".rb", ".php", ".cs", ".swift", ".kt", ".scala", ".sh",
        ".bash", ".html", ".css", ".scss", ".less", ".vue", ".svelte",
        ".json", ".yaml", ".yml", ".toml", ".md", ".txt"
    }
    
    skip_dirs = {
        ".git", "node_modules", "venv", "__pycache__", ".venv", "dist",
        "build", "target", ".github", "assets", "static", "public"
    }
    
    files = []
    for item in tree:
        if item.get("type") == "blob":
            path = item.get("path", "")
            if any(skip_dir in path.split("/") for skip_dir in skip_dirs):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in code_extensions or path.endswith("requirements.txt") or path.endswith("package.json") or path.endswith("Dockerfile"):
                try:
                    content = get_file_content(owner, repo, path, branch)
                    files.append({"path": path, "content": content, "type": ext})
                except Exception as e:
                    print(f"Skipping {path}: {e}")
    return files


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


def save_evaluation(project_id: str, agent_type: str, score: float, result_json: dict):
    """Save evaluation to database"""
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO evaluations (project_id, agent_type, score, result_json)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (project_id, agent_type) DO UPDATE
           SET score = EXCLUDED.score, result_json = EXCLUDED.result_json, created_at = CURRENT_TIMESTAMP""",
        (project_id, agent_type, score, json.dumps(result_json))
    )
    conn.commit()
    cur.close()
    conn.close()


def get_hackathon_info(hackathon_id: int) -> dict:
    """Get hackathon details"""
    if not hackathon_id:
        return {"name": "General", "theme": "", "criteria": "Code Quality, Innovation, Tech Stack"}
    
    conn = get_database_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT name, theme, criteria FROM hackathons WHERE id = %s", (hackathon_id,))
    hackathon = cur.fetchone()
    cur.close()
    conn.close()
    
    if hackathon:
        return {
            "name": hackathon.get("name") or "General",
            "theme": hackathon.get("theme") or "",
            "criteria": hackathon.get("criteria") or "Code Quality, Innovation, Tech Stack"
        }
    return {"name": "General", "theme": "", "criteria": "Code Quality, Innovation, Tech Stack"}


def query_codebase(vectorstore, question: str, llm):
    """Query the codebase with a specific question"""
    if vectorstore is None:
        return "No code found to analyze"
    
    retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
    try:
        relevant_docs = retriever.invoke(question)
    except AttributeError:
        try:
            relevant_docs = retriever.get_relevant_documents(question)
        except:
            return "Unable to retrieve code context"
    
    context = "\n\n".join([doc.page_content for doc in relevant_docs])
    if not context:
        return "No code found to analyze"
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a code reviewer. Answer based on the code provided. Be concise, one paragraph, max 100 words. If you can't determine something, say so honestly."),
        ("human", "Code Context:\n{context}\n\nQuestion: {question}\n\nAnswer:")
    ])
    
    chain = prompt | llm | StrOutputParser()
    try:
        return chain.invoke({"context": context[:3000], "question": question})
    except Exception as e:
        return f"Analysis error: {str(e)}"


def build_code_context(files: list, max_chars: int = 15000) -> str:
    """Build code context from files"""
    context_parts = []
    total_chars = 0
    
    for f in files[:30]:
        file_content = f"File: {f['path']}\n{f['content'][:2000]}\n"
        if total_chars + len(file_content) > max_chars:
            break
        context_parts.append(file_content)
        total_chars += len(file_content)
    
    return "\n".join(context_parts)


@router.get("/code-agent")
async def codeAgent_endpoint():
    return {"message": "Code Agent is running", "capabilities": ["Analyze GitHub repositories", "Code quality assessment"]}


@router.post("/code-agent/analyze")
async def code_agent_analyze(request: Request):
    """Analyze a GitHub repository"""
    try:
        data = await request.json()
        repo_url = data.get("repo_url", "")
        
        if not repo_url:
            raise HTTPException(status_code=400, detail="Repository URL is required")
        
        hackathon_id = data.get("hackathon_id")
        hackathon = get_hackathon_info(hackathon_id)
        
        owner, repo_name = parse_repo_url(repo_url)
        if not owner or not repo_name:
            raise HTTPException(status_code=400, detail="Invalid repository URL format")
        
        print(f"Analyzing repository: {owner}/{repo_name}")
        files = fetch_repo_contents(owner, repo_name)
        
        if not files:
            return {"message": "No code files found", "files_analyzed": 0}
        
        from langchain_core.documents import Document
        documents = [Document(page_content=f"File: {f['path']}\n\n{f['content']}", metadata={"source": f["path"]}) for f in files]
        
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        chunks = text_splitter.split_documents(documents)
        
        embeddings = get_embeddings()
        vectorstore = Chroma.from_documents(documents=chunks, embedding=embeddings, collection_name=f"repo_{uuid.uuid4().hex[:8]}")
        
        code_context = build_code_context(files)
        llm = get_llm()
        
        evaluation_prompt = f"""Évalue ce projet pour le hackathon "{hackathon['name']}".
Thème: {hackathon['theme']}
Critères: {hackathon['criteria']}

Contexte du code (fichiers: {len(files)}):
{code_context}

Analyse le code et donne un score 0-100 avec justification. Réponds UNIQUEMENT en JSON:
{{"score": <0-100>, "summary": "<résumé 1-2 phrases>", "strengths": ["<point fort>", ...], "weaknesses": ["<point faible>", ...], "criterion_scores": {{"<critère>": <score 0-100>, ...}}}}"""

        chain = ChatPromptTemplate.from_messages([
            ("system", "Tu es un reviewer de code expert. Réponds uniquement en JSON valide, sans texte additionnel."),
            ("human", "{evaluation_prompt}")
        ]) | llm | StrOutputParser()
        
        result = chain.invoke({"evaluation_prompt": evaluation_prompt})
        
        try:
            result_json = json.loads(result)
            score = result_json.get("score", 0)
        except:
            score = 50
            result_json = {"summary": result, "score": score}
        
        return {
            "message": "Code analysis complete",
            "repo_url": repo_url,
            "files_analyzed": len(files),
            "evaluation": result_json
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


async def invoke_code_agent(repolink: str, project_id: str, hackathon_id: int = None):
    """Background task for automatic code analysis"""
    try:
        hackathon = get_hackathon_info(hackathon_id)
        
        if not repolink or not repolink.startswith("http"):
            print(f"Invalid repo URL: {repolink}")
            save_evaluation(project_id, "code", 0, {"error": "Invalid GitHub URL", "score": 0})
            return
        
        owner, repo_name = parse_repo_url(repolink)
        if not owner or not repo_name:
            print(f"Invalid repo URL format: {repolink}")
            save_evaluation(project_id, "code", 0, {"error": "Invalid repository URL format", "score": 0})
            return
        
        print(f"Fetching {owner}/{repo_name} via GitHub API")
        files = fetch_repo_contents(owner, repo_name)
        print(f"Fetched {len(files)} files")
        
        if not files:
            save_evaluation(project_id, "code", 0, {"error": "No code files found", "score": 0})
            return
        
        from langchain_core.documents import Document
        documents = [Document(page_content=f"File: {f['path']}\n\n{f['content']}", metadata={"source": f["path"]}) for f in files]
        
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        chunks = text_splitter.split_documents(documents)
        
        embeddings = get_embeddings()
        vectorstore = Chroma.from_documents(documents=chunks, embedding=embeddings, collection_name=f"repo_{project_id}")
        
        code_context = build_code_context(files)
        llm = get_llm()
        
        evaluation_prompt = f"""Évalue ce projet pour le hackathon "{hackathon['name']}".
Thème: {hackathon['theme']}
Critères: {hackathon['criteria']}

Contexte du code (fichiers: {len(files)}):
{code_context}

Analyse le code et donne un score 0-100 avec justification. Réponds UNIQUEMENT en JSON valide:
{{"score": <0-100>, "summary": "<résumé 1-2 phrases>", "strengths": ["<point fort>", ...], "weaknesses": ["<point faible>", ...], "criterion_scores": {{"<critère>": <score 0-100>, ...}}}}"""

        chain = ChatPromptTemplate.from_messages([
            ("system", "Tu es un reviewer de code expert. Réponds uniquement en JSON valide, sans texte additionnel."),
            ("human", "{evaluation_prompt}")
        ]) | llm | StrOutputParser()
        
        print(f"Evaluating code for project {project_id}")
        result = chain.invoke({"evaluation_prompt": evaluation_prompt})
        print(f"Raw result: {result[:200]}...")
        
        try:
            result_json = json.loads(result)
            score = float(result_json.get("score", 50))
        except (json.JSONDecodeError, TypeError):
            print(f"Failed to parse result: {result[:200]}")
            score = 50
            result_json = {"summary": result, "score": score, "error": "Parse error"}
        
        save_evaluation(project_id, "code", score, result_json)
        print(f"Code Agent: Saved evaluation with score {score} for project {project_id}")
        
    except Exception as e:
        print(f"Code Agent Error: {str(e)}")
        import traceback
        traceback.print_exc()
        save_evaluation(project_id, "code", 0, {"error": str(e), "score": 0})
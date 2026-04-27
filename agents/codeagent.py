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
import tempfile
import shutil
import uuid
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import base64
import re
from dotenv import load_dotenv


def get_github_session():
    """Create a requests session with retry logic for transient errors"""
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
    """Extract owner and repo from URL like https://github.com/owner/repo"""
    match = re.search(r"github\.com/([^/]+)/([^/?#]+?)(?:\.git)?(?:/|$|[?#])", repo_url)
    if match:
        owner = match.group(1)
        repo = match.group(2)
        # Clean up any remaining .git or path components
        repo = repo.replace(".git", "").split("/")[0]
        return owner, repo
    return None, None


def get_default_branch(owner, repo):
    """Get the default branch of a repository"""
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
    """Get all files in repository using Git Trees API"""
    session = get_github_session()

    # Get default branch if not specified
    if branch is None:
        branch = get_default_branch(owner, repo)

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    response = session.get(url, headers=get_github_headers())

    if response.status_code == 404:
        # Try common branch names
        for fallback_branch in ["main", "master", "prod", "develop"]:
            if fallback_branch == branch:
                continue
            url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{fallback_branch}?recursive=1"
            response = session.get(url, headers=get_github_headers())
            if response.status_code == 200:
                branch = fallback_branch
                break

    if response.status_code == 404:
        raise ValueError(
            f"Repository '{owner}/{repo}' not found or has no commits. Check the URL and ensure the repo exists."
        )
    if response.status_code == 403:
        raise ValueError(
            f"Access forbidden to '{owner}/{repo}'. Check GITHUB_TOKEN has proper permissions."
        )
    if response.status_code == 429:
        raise ValueError(
            "GitHub API rate limit exceeded. Set GITHUB_TOKEN in .env to increase limit."
        )
    response.raise_for_status()
    return response.json().get("tree", [])


def get_file_content(owner, repo, path, branch="main"):
    """Get file content from GitHub API"""
    session = get_github_session()
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    response = session.get(url, headers=get_github_headers())
    if response.status_code == 404:
        raise FileNotFoundError(f"File '{path}' not found in {owner}/{repo}")
    if response.status_code == 403:
        raise PermissionError(f"Access forbidden to file '{path}' in {owner}/{repo}")
    response.raise_for_status()
    data = response.json()
    if data.get("encoding") == "base64":
        content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
        return content
    return data.get("content", "")


def fetch_repo_contents(owner, repo, branch="main"):
    """Fetch all code files from repository"""
    tree = get_repo_tree(owner, repo, branch)
    code_extensions = {
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".cpp",
        ".c",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".cs",
        ".swift",
        ".kt",
        ".scala",
        ".sh",
        ".bash",
        ".html",
        ".css",
        ".scss",
        ".less",
        ".vue",
        ".svelte",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".md",
        ".txt",
    }
    skip_dirs = {
        ".git",
        "node_modules",
        "venv",
        "__pycache__",
        ".venv",
        "dist",
        "build",
        "target",
        ".github",
        "assets",
        "static",
        "public",
    }

    files = []
    for item in tree:
        if item.get("type") == "blob":
            path = item.get("path", "")
            if any(skip_dir in path.split("/") for skip_dir in skip_dirs):
                continue
            ext = os.path.splitext(path)[1].lower()
            if (
                ext in code_extensions
                or path.endswith("requirements.txt")
                or path.endswith("package.json")
                or path.endswith("Dockerfile")
            ):
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
        temperature=0,
    )


def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name=os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )


def query_codebase(vectorstore, question, llm):
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

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are a code reviewer analyzing a hackathon project.
Answer based on the code provided. Be concise - one paragraph, max 70 words.
If you can't determine something, say so honestly.""",
            ),
            (
                "human",
                """Code Context:
{context}

Question: {question}

Answer:""",
            ),
        ]
    )

    chain = prompt | llm | StrOutputParser()
    try:
        response = chain.invoke({"context": context[:3000], "question": question})
        return response
    except Exception as e:
        return f"Analysis error: {str(e)}"


def analyze_repository(repo_url: str, questions: list[str]) -> tuple[list[dict], int]:
    """Analyze a GitHub repository using the same logic as POST /code-agent/analyze
    Returns: (analysis_results, number_of_files_analyzed)
    """
    owner, repo_name = parse_repo_url(repo_url)
    if not owner or not repo_name:
        raise ValueError("Invalid repository URL format")

    print(f"Fetching repo: {owner}/{repo_name}")
    files = fetch_repo_contents(owner, repo_name)
    num_files = len(files)
    print(f"Fetched {num_files} files")

    if not files:
        return ([], 0)

    documents = []
    for f in files:
        from langchain_core.documents import Document

        documents.append(
            Document(
                page_content=f"File: {f['path']}\n\n{f['content']}",
                metadata={"source": f["path"]},
            )
        )

    # Split documents
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_documents(documents)
    print(f"Created {len(chunks)} chunks")

    # Create vectorstore
    embeddings = get_embeddings()
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=f"repo_{uuid.uuid4().hex[:8]}",
    )

    # Analyze with questions
    llm = get_llm()
    results = []
    for question in questions:
        print(f"Analyzing: {question}")
        answer = query_codebase(vectorstore, question, llm)
        results.append({"question": question, "answer": answer})
        print(f"Answer: {answer[:100]}...")

    return (results, num_files)


def extract_score_from_text(text: str) -> float:
    """Extract a score between 0 and 1 from text response"""
    patterns = [
        r'(\d+\.?\d*)/10',
        r'(\d+\.?\d*)%',
        r'[Ss]core:?\s*(\d+\.?\d*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = float(match.group(1))
            if value > 1:
                value = value / 100 if value <= 100 else value / 10
            return min(1.0, max(0.0, value))
    
    return 0.5


def save_evaluation(project_id: str, criteria_name: str, score: float, remarks: str, agent_type: str):
    """Save evaluation to database"""
    conn = get_database_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO evaluations (project_id, criteria_name, score, remarks, agent_type)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (project_id, criteria_name, score, remarks, agent_type)
    )
    conn.commit()
    cur.close()
    conn.close()

@router.get("/code-agent")
async def codeAgent_endpoint():
    """Test endpoint - returns available functionality"""
    return {
        "message": "Code Agent is running",
        "capabilities": [
            "Clone and analyze GitHub repositories",
            "Technology stack detection",
            "Code quality assessment",
            "Dependency analysis",
        ],
        "usage": {
            "endpoint": "POST /api/code-agent/analyze",
            "body": {"repo_url": "https://github.com/username/repository"},
        },
    }


@router.post("/code-agent/analyze")
async def code_agent_analyze(request: Request):
    """Analyze a GitHub repository"""
    try:
        data = await request.json()
        repo_url = data.get("repo_url", "")

        if not repo_url:
            raise HTTPException(status_code=400, detail="Repository URL is required")

        print(f"Analyzing repository: {repo_url}")

        questions = [
            "What technologies and programming languages are used?",
            "Explain the project structure and purpose",
            "How is the code quality?",
            "What dependencies and libraries are used?",
        ]

        results, num_files = analyze_repository(repo_url, questions)

        if num_files == 0:
            return {"message": "No code files found in repository", "analysis": []}

        return {
            "message": "Code analysis complete",
            "repo_url": repo_url,
            "files_analyzed": num_files,
            "analysis": results,
        }

    except ValueError as e:
        print(f"Validation error: {str(e)}")
        raise HTTPException(status_code=404, detail=str(e))
    except (FileNotFoundError, PermissionError) as e:
        print(f"File access error: {str(e)}")
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        print(f"Error in code analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


# Background task function
async def invoke_code_agent(repolink: str, project_id: str):
    """Background task for automatic code analysis"""
    try:
        owner, repo_name = parse_repo_url(repolink)
        if not owner or not repo_name:
            print(f"Code Agent Error: Invalid repo URL {repolink}")
            return

        print(f"Fetching {owner}/{repo_name} via GitHub API")
        files = fetch_repo_contents(owner, repo_name)
        print(f"Fetched {len(files)} files")

        if not files:
            print("No files found")
            return

        documents = []
        for f in files:
            from langchain_core.documents import Document

            documents.append(
                Document(
                    page_content=f"File: {f['path']}\n\n{f['content']}",
                    metadata={"source": f["path"]},
                )
            )

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200
        )
        chunks = text_splitter.split_documents(documents)

        embeddings = get_embeddings()
        vectorstore = Chroma.from_documents(
            documents=chunks, embedding=embeddings, collection_name=f"repo_{project_id}"
        )

        conn = get_database_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT technologies FROM hackathons LIMIT 1")
        hackathon = cur.fetchone()
        technologies = hackathon["technologies"] if hackathon else ""
        cur.close()
        conn.close()

        llm = get_llm()
        questions = [
            "What technologies and programming language are used?",
            "Explain the project in brief",
            "How is the code quality?",
            f"Does it use these required technologies: {technologies}?",
        ]

        results = []
        for question in questions:
            answer = query_codebase(vectorstore, question, llm)
            results.append({"question": question, "answer": answer})

        conn = get_database_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE projects SET code_agent_analysis = %s WHERE project_id = %s",
            (json.dumps(results), project_id),
        )
        conn.commit()
        cur.close()
        conn.close()

        print(f"Code Agent: Analysis complete for project {project_id}")

    except Exception as e:
        print(f"Code Agent Error: {str(e)}")

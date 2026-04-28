import asyncio
import base64
import re
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fastapi import APIRouter, Request, HTTPException
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from db import get_database_connection
from psycopg2.extras import RealDictCursor
import json
import os
from ddgs import DDGS
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


def run_search(query: str, max_results: int = 5) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        
        if not results:
            return "No relevant search results found."
        
        formatted = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            formatted.append(f"{title}: {body}")
        
        return "\n".join(formatted)
    
    except Exception as e:
        return f"Search failed: {str(e)}"


def research_question(idea, question, llm, readme_info: str = ""):
    """Research a market question using web search and LLM"""
    
    search_query = f"{idea} startup market research: {question}"
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a market research analyst.

Rules:
- Base your answer PRIMARYLY on the provided README/project description
- Use web search only to supplement with market data (numbers, trends, competitors)
- If README provides clear information about the product, use it to make educated answers
- Do NOT say "insufficient data" if the README clearly describes the product
- Max 70 words
- One paragraph"""),

        ("human", """Project Idea: {idea}
{readme_info}
Web &&&  (for market data only):
{search_results}

Question: {question}

Answer:""")
    ])
    
    chain = prompt | llm | StrOutputParser()

    try:
        response = chain.invoke({
            "idea": idea,
            "readme_info": readme_info,
            "search_results": search_results[:3000],
            "question": question
        })
        return response.strip()
    
    try:
        return chain.invoke({"search_results": search_results[:3000], "query": query}).strip()
    except Exception as e:
        return f"Error: {str(e)}"


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


async def analyze_market(idea: str, theme: str, readme_content: str = ""):
    """Perform full market analysis"""
    llm = get_llm()
    
    marketQuestions = [
        "Who is the target audience of this idea?",
        "What is the market potential and size?",
        "What are the main competitors?",
        "What are the potential pitfalls?",
        "What is the revenue model potential?"
    ]
    
    readme_info = ""
    results = []
    if readme_content and len(readme_content.strip()) > 50:
        readme_info = f"""
Project README/Description:
{readme_content[:5000]}
"""
    else:
        readme_info = """
No sufficient README data available. The project does not have a meaningful README file.
"""
        for question in marketQuestions:
            print(f"Researching: {question}")
            answer = "insufficient data - no README available"
            results.append({
                "question": question,
                "answer": answer
            })
        
        return {
            "analysis": results,
            "matched_theme": "Unknown"
        }
    
    results = []
    for question in marketQuestions:
        print(f"Researching: {question}")
        answer = research_question(idea, question, llm, readme_info)
        results.append({
            "question": question,
            "answer": answer
        })
        print(f"Answer: {answer[:100]}...")
    
    if hackathon:
        return {
            "name": hackathon.get("name") or "General",
            "theme": hackathon.get("theme") or "",
            "criteria": hackathon.get("criteria") or "Market Potential, Innovation, Viability"
        }
    return {"name": "General", "theme": "", "criteria": "Market Potential, Innovation, Viability"}


async def invoke_market_agent(project_id: str, idea: str, hackathon_id: int = None):
    """Background task for automatic market analysis"""
    try:
        hackathon = get_hackathon_info(hackathon_id)
        
        if not idea or not idea.strip():
            print(f"Empty idea for project {project_id}")
            save_evaluation(project_id, "market", 0, {"error": "Empty project description", "score": 0})
            return
        
        print(f"Researching market for: {idea[:100]}...")
        
        llm = get_llm()
        
        research_queries = [
            f"{idea} market size and potential",
            f"{idea} main competitors and market landscape",
            f"{idea} target audience and use cases"
        ]
        
        research_contexts = []
        for query in research_queries:
            print(f"Researching: {query[:80]}...")
            result = run_search(query)
            research_contexts.append(result[:1500])
        
        market_context = "\n\n".join(research_contexts)
        
        evaluation_prompt = f"""Évalue ce projet pour le hackathon "{hackathon['name']}".
Thème: {hackathon['theme']}
Critères: {hackathon['criteria']}

Idée du projet: {idea}

Contexte marché:
{market_context}

Analyse le marché et donne un score 0-100 avec justification. Réponds UNIQUEMENT en JSON valide:
{{"score": <0-100>, "summary": "<résumé 1-2 phrases>", "strengths": ["<point fort>", ...], "weaknesses": ["<point faible>", ...], "criterion_scores": {{"<critère>": <score 0-100>, ...}}}}"""

        chain = ChatPromptTemplate.from_messages([
            ("system", "Tu es un analyste marché expert. Réponds uniquement en JSON valide, sans texte additionnel."),
            ("human", "{evaluation_prompt}")
        ]) | llm | StrOutputParser()
        
        result = chain.invoke({"evaluation_prompt": evaluation_prompt})
        print(f"Raw result: {result[:200]}...")
        
        try:
            result_json = json.loads(result)
            score = float(result_json.get("score", 50))
        except (json.JSONDecodeError, TypeError):
            print(f"Failed to parse result: {result[:200]}")
            score = 50
            result_json = {"summary": result, "score": score, "error": "Parse error"}
        
        save_evaluation(project_id, "market", score, result_json)
        print(f"Market Agent: Saved evaluation with score {score} for project {project_id}")
        
    except Exception as e:
        print(f"Market Agent Error: {str(e)}")
        import traceback
        traceback.print_exc()
        save_evaluation(project_id, "market", 0, {"error": str(e), "score": 0})


@router.get("/market-agent")
async def marketAgent_endpoint():
    return {"message": "Market Agent is running", "capabilities": ["Market analysis with web research", "Score 0-100 evaluation"]}


@router.post("/market-agent/analyze")
async def market_agent_analyze(request: Request):
    """Analyze a market idea"""
    try:
        data = await request.json()
        idea = data.get("idea", "")
        hackathon_id = data.get("hackathon_id")
        
        if not idea:
            raise HTTPException(status_code=400, detail="Idea is required")
        
        hackathon = get_hackathon_info(hackathon_id)
        
        print(f"Analyzing idea: {idea}")
        
        llm = get_llm()
        
        research_queries = [
            f"{idea} market size and potential",
            f"{idea} main competitors",
            f"{idea} target audience"
        ]
        
        research_contexts = []
        for query in research_queries:
            result = run_search(query)
            research_contexts.append(result[:1500])
        
        market_context = "\n\n".join(research_contexts)
        
        evaluation_prompt = f"""Évalue ce projet pour le hackathon "{hackathon['name']}".
Thème: {hackathon['theme']}
Critères: {hackathon['criteria']}

Idée: {idea}

Contexte marché:
{market_context}

Analyse et donne un score 0-100. JSON valide uniquement:
{{"score": <0-100>, "summary": "<résumé>", "strengths": [...], "weaknesses": [...], "criterion_scores": {{"<critère>": <score>, ...}}}}"""

        chain = ChatPromptTemplate.from_messages([
            ("system", "Tu es un analyste marché expert. Réponds uniquement en JSON valide."),
            ("human", "{evaluation_prompt}")
        ]) | llm | StrOutputParser()
        
        result = chain.invoke({"evaluation_prompt": evaluation_prompt})
        
        try:
            result_json = json.loads(result)
            score = result_json.get("score", 50)
        except:
            score = 50
            result_json = {"summary": result, "score": score}
        
        return {
            "message": "Market analysis complete",
            "idea": idea,
            "evaluation": result_json
        }
    
    except Exception as e:
        print(f"Error in market analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def fetch_readme(owner: str, repo: str) -> str:
    """Fetch README content from GitHub repository"""
    session = get_github_session()
    default_branch = get_default_branch(owner, repo)
    
    readme_names = ["README.md", "README.rst", "README.txt", "README", "readme.md", "readme.rst", "readme.txt", "readme"]
    
    for readme_name in readme_names:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{readme_name}?ref={default_branch}"
        response = session.get(url, headers=get_github_headers())
        
        if response.status_code == 200:
            data = response.json()
            content = data.get("content", "")
            encoding = data.get("encoding", "")
            
            if encoding == "base64" and content:
                try:
                    decoded = base64.b64decode(content).decode("utf-8")
                    return decoded.strip()
                except:
                    pass
    
    return ""


def get_github_session():
    """Create a requests session with retry logic"""
    session = Session()
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


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")


def get_github_headers():
    """Get headers for GitHub API requests"""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def get_default_branch(owner: str, repo: str) -> str:
    """Get the default branch of a repository"""
    session = get_github_session()
    url = f"https://api.github.com/repos/{owner}/{repo}"
    response = session.get(url, headers=get_github_headers())
    if response.status_code == 404:
        return "main"
    response.raise_for_status()
    return response.json().get("default_branch", "main")


def parse_repo_url(repo_url: str):
    """Extract owner and repo from URL"""
    owner, repo = None, None
    if repo_url:
        match = re.search(r"github\.com/([^/]+)/([^/?#]+?)(?:\.git)?(?:/|$|[?#])", repo_url)
        if match:
            owner = match.group(1)
            repo = match.group(2).replace(".git", "").split("/")[0]
    return owner, repo


async def invoke_market_agent(project_id: str, idea: str, github_link: str = None, hackathon_id: int = None):
    """Background task for automatic project analysis"""
    try:
        criteria_text = ""
        if hackathon_id:
            conn = get_database_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT criteria FROM hackathons WHERE id = %s", (hackathon_id,))
            hackathon = cur.fetchone()
            if hackathon:
                criteria_text = hackathon["criteria"] or ""
            cur.close()
            conn.close()
        
        readme_content = ""
        if github_link:
            owner, repo = parse_repo_url(github_link)
            if owner and repo:
                readme_content = fetch_readme(owner, repo)
                print(f"Market Agent: Fetched README ({len(readme_content)} chars) from {owner}/{repo}")
        
        result = await analyze_market(idea, "", readme_content)
        
        conn = get_database_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE projects SET market_agent_analysis = %s WHERE project_id = %s",
            (json.dumps(result["analysis"]), project_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"Market Agent: Analysis complete for project {project_id}")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
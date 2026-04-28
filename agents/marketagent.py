import asyncio
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


def research_topic(query: str, llm) -> str:
    """Research a topic using web search"""
    search_results = run_search(query)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Tu es un analyste market research. Réponds en 2-3 phrases basées UNIQUEMENT sur les données fournies. Si insuffisant, dis 'Données insuffisantes pour répondre'."),
        ("human", "Données:\n{search_results}\n\nQuestion: {query}\n\nRéponse:")
    ])
    
    chain = prompt | llm | StrOutputParser()
    
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


def get_hackathon_info(hackathon_id: int) -> dict:
    """Get hackathon details"""
    if not hackathon_id:
        return {"name": "General", "theme": "", "criteria": "Market Potential, Innovation, Viability"}
    
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
        raise HTTPException(status_code=500, detail=str(e))
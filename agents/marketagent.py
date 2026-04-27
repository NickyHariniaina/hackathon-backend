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
        temperature=0.2
    )


def run_search(query: str, max_results: int = 5) -> str:
    """Reliable search using ddgs"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        
        if not results:
            return "No relevant search results found."
        
        # Extract useful text
        formatted = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            formatted.append(f"{title}: {body}")
        
        return "\n".join(formatted)
    
    except Exception as e:
        return f"Search failed: {str(e)}"


def research_question(idea, question, llm):
    """Research a market question using web search and LLM"""
    
    # 🔥 MUCH better query (this matters a LOT)
    search_query = f"{idea} startup market research: {question}"
    
    search_results = run_search(search_query)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a market research analyst.

Rules:
- Use ONLY the provided research data
- If data is weak, say "insufficient data"
- Be specific (numbers, trends, competitors)
- Max 70 words
- One paragraph"""),

        ("human", """Idea: {idea}

Research Data:
{search_results}

Question: {question}

Answer:""")
    ])
    
    chain = prompt | llm | StrOutputParser()

    try:
        response = chain.invoke({
            "idea": idea,
            "search_results": search_results[:3000],
            "question": question
        })
        return response.strip()
    
    except Exception as e:
        return f"LLM error: {str(e)}"


async def analyze_market(idea: str, theme: str):
    """Perform full market analysis"""
    llm = get_llm()
    
    marketQuestions = [
        "Who is the target audience of this idea?",
        "What is the market potential and size?",
        "What are the main competitors?",
        "What are the potential pitfalls?",
        "What is the revenue model potential?"
    ]
    
    results = []
    for question in marketQuestions:
        print(f"Researching: {question}")
        answer = research_question(idea, question, llm)
        results.append({
            "question": question,
            "answer": answer
        })
        print(f"Answer: {answer[:100]}...")
    
    # Theme matching
    theme_prompt = ChatPromptTemplate.from_messages([
        ("system", "Match this idea to one theme. Return ONLY the theme name."),
        ("human", "Themes: {themes}\nIdea: {idea}\nMatched Theme:")
    ])
    
    theme_chain = theme_prompt | llm | StrOutputParser()
    try:
        matched_theme = theme_chain.invoke({
            "themes": theme,
            "idea": idea
        })
        matched_theme = matched_theme.strip().split('\n')[0]  # Take first line only
    except:
        matched_theme = "General"
    
    return {
        "analysis": results,
        "matched_theme": matched_theme
    }

@router.get("/market-agent")
async def marketAgent_endpoint():
    """Test endpoint - returns available functionality"""
    return {
        "message": "Market Agent is running",
        "capabilities": [
            "Market analysis with web research",
            "Target audience identification",
            "Competitor analysis",
            "Theme matching",
            "Revenue potential analysis"
        ],
        "usage": {
            "endpoint": "POST /api/market-agent/analyze",
            "body": {
                "idea": "Your project idea description",
                "theme": "Comma-separated list of themes"
            }
        }
    }

@router.post("/market-agent/analyze")
async def market_agent_analyze(request: Request):
    """Analyze a market idea"""
    try:
        data = await request.json()
        idea = data.get("idea", "")
        theme = data.get("theme", "")
        
        if not idea:
            raise HTTPException(status_code=400, detail="Idea is required")
        
        if not theme:
            # Get theme from database if not provided
            try:
                conn = get_database_connection()
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("SELECT theme FROM hackathons LIMIT 1")
                hackathon = cur.fetchone()
                cur.close()
                conn.close()
                theme = hackathon["theme"] if hackathon else "General"
            except:
                theme = "General"
        
        print(f"Analyzing idea: {idea}")
        print(f"Themes: {theme}")
        
        result = await analyze_market(idea, theme)
        
        return {
            "message": "Market analysis complete",
            "idea": idea,
            "matched_theme": result["matched_theme"],
            "analysis": result["analysis"]
        }
    
    except Exception as e:
        print(f"Error in market analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def invoke_market_agent(project_id: str, idea: str, hackathon_id: int = None):
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
        
        result = await analyze_market(idea, "")
        
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
        print(f"Market Agent Error: {str(e)}")
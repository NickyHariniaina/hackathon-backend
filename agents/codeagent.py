from langchain_openai import ChatOpenAI
from langchain_chroma import Chroma
from langchain_community.document_loaders import GitLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from git import Repo
from fastapi import APIRouter, Request, HTTPException
import asyncio
from db import get_database_connection
from psycopg2.extras import RealDictCursor
import json
import os
import tempfile
import shutil
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

def query_codebase(vectorstore, question, llm):
    """Query the codebase with a specific question - USING invoke() NOT get_relevant_documents()"""
    if vectorstore is None:
        return "No code found to analyze"
    
    # FIXED: Use invoke() instead of deprecated get_relevant_documents()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
    
    try:
        # New way: use invoke()
        relevant_docs = retriever.invoke(question)
    except AttributeError:
        try:
            # Fallback: try get_relevant_documents for older versions
            relevant_docs = retriever.get_relevant_documents(question)
        except:
            return "Unable to retrieve code context"
    
    context = "\n\n".join([doc.page_content for doc in relevant_docs])
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a code reviewer analyzing a hackathon project.
Answer based on the code provided. Be concise - one paragraph, max 70 words.
If you can't determine something, say so honestly."""),
        ("human", """Code Context:
{context}

Question: {question}

Answer:""")
    ])
    
    chain = prompt | llm | StrOutputParser()
    try:
        response = chain.invoke({
            "context": context[:3000],
            "question": question
        })
        return response
    except Exception as e:
        return f"Analysis error: {str(e)}"

@router.get("/code-agent")
async def codeAgent_endpoint():
    """Test endpoint - returns available functionality"""
    return {
        "message": "Code Agent is running",
        "capabilities": [
            "Clone and analyze GitHub repositories",
            "Technology stack detection",
            "Code quality assessment",
            "Dependency analysis"
        ],
        "usage": {
            "endpoint": "POST /api/code-agent/analyze",
            "body": {
                "repo_url": "https://github.com/username/repository"
            }
        }
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
        
        # Create temp directory
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Clone repo
            print("Cloning repository...")
            repo = Repo.clone_from(repo_url, to_path=temp_dir)
            branch = repo.head.reference
            print(f"Cloned branch: {branch}")
            
            # Load documents
            loader = GitLoader(repo_path=temp_dir, branch=branch)
            documents = loader.load()
            print(f"Loaded {len(documents)} documents")
            
            if not documents:
                return {"message": "No code files found in repository", "analysis": []}
            
            # Split documents
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200
            )
            chunks = text_splitter.split_documents(documents)
            print(f"Created {len(chunks)} chunks")
            
            # Create vectorstore
            embeddings = get_embeddings()
            vectorstore = Chroma.from_documents(
                documents=chunks,
                embedding=embeddings,
                collection_name=f"repo_{uuid.uuid4().hex[:8]}"
            )
            
            # Analyze
            llm = get_llm()
            questions = [
                "What technologies and programming languages are used?",
                "Explain the project structure and purpose",
                "How is the code quality?",
                "What dependencies and libraries are used?"
            ]
            
            results = []
            for question in questions:
                print(f"Analyzing: {question}")
                answer = query_codebase(vectorstore, question, llm)
                results.append({
                    "question": question,
                    "answer": answer
                })
                print(f"Answer: {answer[:100]}...")
            
            return {
                "message": "Code analysis complete",
                "repo_url": repo_url,
                "files_analyzed": len(documents),
                "analysis": results
            }
            
        finally:
            # Cleanup
            shutil.rmtree(temp_dir, ignore_errors=True)
            print("Cleaned up temporary files")
    
    except Exception as e:
        print(f"Error in code analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

# Background task function
async def invoke_code_agent(repolink: str, project_id: str):
    """Background task for automatic code analysis"""
    temp_dir = f"./projects_source_code/{project_id}"
    
    try:
        print(f"Cloning {repolink} into {temp_dir}")
        repo = Repo.clone_from(repolink, to_path=temp_dir)
        branch = repo.head.reference
        
        loader = GitLoader(repo_path=temp_dir, branch=branch)
        documents = loader.load()
        
        if not documents:
            print("No documents found")
            return
        
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200
        )
        chunks = text_splitter.split_documents(documents)
        
        embeddings = get_embeddings()
        vectorstore = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=f"repo_{project_id}"
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
            f"Does it use these required technologies: {technologies}?"
        ]
        
        results = []
        for question in questions:
            answer = query_codebase(vectorstore, question, llm)
            results.append({
                "question": question,
                "answer": answer
            })
        
        conn = get_database_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE projects SET code_agent_analysis = %s WHERE project_id = %s",
            (json.dumps(results), project_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"Code Agent: Analysis complete for project {project_id}")
        
    except Exception as e:
        print(f"Code Agent Error: {str(e)}")
from langchain_core.tools import tool
from langchain_tavily import TavilySearch
import os
from dotenv import load_dotenv

load_dotenv()  
TAVILY_API_KEY=os.getenv('TAVILY_API_KEY')

@tool
def web_search_tool(query: str) -> str:
    """Perform a web search using Tavily via LangChain and return the top results (read operation)"""
    tavily = TavilySearch(max_results=3)
    results = tavily.invoke({"query": query})
    if not results or not results.get('results'):
        return "No results found."
    top_results = results['results'][:3]
    output = []
    for item in top_results:
        title = item.get('title', '')
        snippet = item.get('content', '')
        link = item.get('url', '')
        output.append(f"{title}: {snippet} ({link})")
    return "\n".join(output) if output else "No results found." 
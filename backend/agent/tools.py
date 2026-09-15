import httpx
from langchain_core.tools import tool
from sympy import Eq, solve, sympify

from app.config import BRAVE_API_KEY


# ponytail: sympify is eval-adjacent; output is text-only so risk is low. Add a symbols whitelist if tool input ever becomes structured/persistent.
@tool
def solve_equation(expression: str) -> str:
    """Solve a math expression or equation. Use for any math the user asks to compute or solve."""
    if "=" in expression:
        left, right = expression.split("=", 1)
        solutions = solve(Eq(sympify(left), sympify(right)))
        return f"x = {solutions}" if solutions else "No solution found."
    return str(sympify(expression))


@tool
def brave_search(query: str) -> str:
    """Search the web via Brave Search API. Use for current events, facts you don't know, or anything requiring up-to-date info."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 3},
            headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        )
        resp.raise_for_status()
    results = resp.json().get("web", {}).get("results", [])
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{r['title']}: {r.get('description', '')} ({r['url']})" for r in results
    )


ALL_TOOLS = [solve_equation, brave_search]

# Human-friendly names for UI display ("Called Tool: ...").
TOOL_DISPLAY_NAMES = {
    "solve_equation": "Calculator",
    "brave_search": "Web Search",
}

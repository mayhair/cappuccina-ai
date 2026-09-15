from sympy import sympify

from agent.tools import solve_equation
from langchain_core.runnables import RunnableConfig

r = solve_equation.invoke({"expression": "2*x + 3 = 11"})
assert "4" in r, r
r2 = solve_equation.invoke({"expression": "1/4 + 1/8"})
assert str(sympify("3/8")) in r2, r2
print("tools OK:", r, "|", r2)

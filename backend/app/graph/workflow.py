"""
workflow.py — the single LangGraph StateGraph for the assistant.

Deliberately ONE graph, no multi-agent fan-out — per the project's own
directive: "Create only one main graph. Do NOT create multiple agents."
Department/intent understanding happens inline (understand_question) rather
than by routing to separate department agents.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, END

from app.graph.state import ChatState
from app.graph import nodes


def _route_after_understand(state: ChatState) -> str:
    if state.get("is_definition") and state.get("dictionary_answer"):
        return "dictionary_answer"
    return "retrieve_context"


def _route_after_generate_sql(state: ChatState) -> str:
    if state.get("sql") is None:
        # Either no API key, a generation error, or a clarification request —
        # generate_sql already set `answer` for all three cases.
        return END
    return "validate_sql"


def _route_after_validate(state: ChatState) -> str:
    return "execute_sql" if state.get("guard_ok") else "repair_sql"


def _route_after_execute(state: ChatState) -> str:
    if not state.get("exec_ok"):
        return "repair_sql"
    if state.get("row_count", 0) == 0:
        return END  # "no matching rows" answer already set
    return "generate_answer"


def _route_after_repair(state: ChatState) -> str:
    return END if state.get("give_up") else "validate_sql"


def build_graph():
    graph = StateGraph(ChatState)

    graph.add_node("understand", nodes.understand_question)
    graph.add_node("dictionary_answer", nodes.dictionary_answer_node)
    graph.add_node("retrieve_context", nodes.retrieve_business_context)
    graph.add_node("generate_sql", nodes.generate_sql)
    graph.add_node("validate_sql", nodes.validate_sql)
    graph.add_node("execute_sql", nodes.execute_sql)
    graph.add_node("repair_sql", nodes.repair_sql)
    graph.add_node("generate_answer", nodes.generate_answer)

    graph.set_entry_point("understand")

    graph.add_conditional_edges(
        "understand", _route_after_understand,
        {"dictionary_answer": "dictionary_answer", "retrieve_context": "retrieve_context"},
    )
    graph.add_edge("dictionary_answer", END)
    graph.add_edge("retrieve_context", "generate_sql")
    graph.add_conditional_edges(
        "generate_sql", _route_after_generate_sql,
        {END: END, "validate_sql": "validate_sql"},
    )
    graph.add_conditional_edges(
        "validate_sql", _route_after_validate,
        {"execute_sql": "execute_sql", "repair_sql": "repair_sql"},
    )
    graph.add_conditional_edges(
        "execute_sql", _route_after_execute,
        {END: END, "generate_answer": "generate_answer", "repair_sql": "repair_sql"},
    )
    graph.add_conditional_edges(
        "repair_sql", _route_after_repair,
        {END: END, "validate_sql": "validate_sql"},
    )
    graph.add_edge("generate_answer", END)

    return graph.compile()


_compiled = None


def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled

"""M3.1 supervisor-loop workflow: supervisor -> router -> specialist -> supervisor until FINISH."""
from __future__ import annotations

from backend.builder.api import END, GraphBuilder, LLMNodeConfig, RouterNodeConfig
from backend.providers.pricing import DEFAULT_OPENAI_MODEL


def build() -> GraphBuilder:
    b = GraphBuilder(
        name="supervisor_loop",
        cost_budget_usd=0.60,
        latency_budget_ms=300_000,
    )

    b.add_node(
        LLMNodeConfig(
            id="supervisor",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You are a workflow supervisor coordinating two specialists. "
                "Choose exactly one next route token based on the current state.\n"
                "Rules:\n"
                "- If research_notes is empty, return RESEARCHER.\n"
                "- Else if final_answer is empty, return WRITER.\n"
                "- Else if final_answer already addresses the task, return FINISH.\n"
                "Return exactly one token on the first line: RESEARCHER, WRITER, or FINISH."
            ),
            user_prompt_template=(
                "User task:\n{user_input}\n\n"
                "Research notes:\n{research_notes}\n\n"
                "Draft answer:\n{final_answer}\n"
            ),
            output_state_key="supervisor_route",
            temperature=0.0,
        )
    )
    b.add_node(
        RouterNodeConfig(
            id="dispatch",
            route_state_key="supervisor_route",
            routes={
                "RESEARCHER": "researcher",
                "WRITER": "writer",
                "FINISH": END,
            },
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="researcher",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You are the research specialist. Extract the constraints, required phrases, "
                "and useful facts from the task. Return compact bullet points only."
            ),
            user_prompt_template=(
                "Task:\n{user_input}\n\n"
                "Current draft answer:\n{final_answer}\n"
            ),
            output_state_key="research_notes",
            temperature=0.0,
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="writer",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You are the writing specialist. Produce a concise final answer using the research notes. "
                "Honor any exact phrase requirements from the task or notes."
            ),
            user_prompt_template=(
                "Task:\n{user_input}\n\n"
                "Research notes:\n{research_notes}\n\n"
                "Write the final answer in 2-4 sentences."
            ),
            output_state_key="final_answer",
            temperature=0.0,
        )
    )

    b.set_entry("supervisor")
    b.add_edge("supervisor", "dispatch")
    b.add_loop("researcher", "supervisor", max_iterations=4)
    b.add_loop("writer", "supervisor", max_iterations=4)
    return b


def build_compiled():
    return build().compile()

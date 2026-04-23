"""The M1 reference workflow: planner -> coder -> tester -> gate -> (coder | END)."""
from __future__ import annotations

from backend.builder.api import END, GateNodeConfig, GraphBuilder, LLMNodeConfig, TesterNodeConfig
from backend.providers.pricing import DEFAULT_OPENROUTER_MODEL


def build() -> "GraphBuilder":
    b = GraphBuilder(
        name="coder_tester",
        cost_budget_usd=0.50,
        latency_budget_ms=300_000,  # 5 minutes
    )

    b.add_node(
        LLMNodeConfig(
            id="planner",
            provider="openrouter",
            model=DEFAULT_OPENROUTER_MODEL,
            system_prompt=(
                "You are a senior software engineer. Break the user's task into a short, "
                "numbered plan (3-6 steps). Be concrete."
            ),
            user_prompt_template="Task:\n{user_input}",
            output_state_key="plan",
            temperature=0.2,
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="coder",
            provider="openrouter",
            model=DEFAULT_OPENROUTER_MODEL,
            system_prompt=(
                "You are a careful coder. Write Python code that fulfills the plan. "
                "Return ONLY the code, no markdown fences, no explanation. "
                "If there is prior tester feedback, address it specifically."
            ),
            user_prompt_template=(
                "Original task:\n{user_input}\n\n"
                "Plan:\n{plan}\n\n"
                "Previous attempt (if any):\n{coder_output}\n\n"
                "Tester feedback (if any):\n{tester_feedback}\n"
            ),
            output_state_key="coder_output",
            temperature=0.2,
        )
    )
    b.add_node(
        TesterNodeConfig(
            id="tester",
            provider="openrouter",
            model=DEFAULT_OPENROUTER_MODEL,
        )
    )
    b.add_node(
        GateNodeConfig(
            id="gate",
            pass_target=END,
            fail_target="coder",
        )
    )

    b.set_entry("planner")
    b.add_edge("planner", "coder")
    b.add_edge("coder", "tester")
    b.add_edge("tester", "gate")
    b.add_loop("gate", "coder", max_iterations=3)

    return b


def build_compiled():
    return build().compile()

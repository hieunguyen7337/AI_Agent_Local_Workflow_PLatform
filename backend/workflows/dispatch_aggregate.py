"""M3.2 fourth reference workflow: DAG fan-out to two specialists, then aggregate."""
from __future__ import annotations

from backend.builder.api import END, GraphBuilder, LLMNodeConfig
from backend.providers.pricing import DEFAULT_OPENAI_MODEL


def build() -> GraphBuilder:
    b = GraphBuilder(
        name="dispatch_aggregate",
        cost_budget_usd=0.60,
        latency_budget_ms=300_000,
    )

    b.add_node(
        LLMNodeConfig(
            id="dispatcher",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You are a dispatcher for two specialists. Produce a concise shared brief that "
                "extracts the task, any required exact phrases, and what each specialist should cover. "
                "Mention both Specialist A and Specialist B explicitly."
            ),
            user_prompt_template="Task:\n{user_input}",
            output_state_key="dispatch_brief",
            temperature=0.0,
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="specialist_a",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You are Specialist A. Produce short notes focused on the first supporting angle only. "
                "Use the dispatch brief, not free invention."
            ),
            user_prompt_template=(
                "Task:\n{user_input}\n\n"
                "Dispatch brief:\n{dispatch_brief}\n\n"
                "Write Specialist A notes in 2-4 bullets."
            ),
            output_state_key="specialist_a_notes",
            temperature=0.0,
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="specialist_b",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You are Specialist B. Produce short notes focused on the second supporting angle only. "
                "Use the dispatch brief, not free invention."
            ),
            user_prompt_template=(
                "Task:\n{user_input}\n\n"
                "Dispatch brief:\n{dispatch_brief}\n\n"
                "Write Specialist B notes in 2-4 bullets."
            ),
            output_state_key="specialist_b_notes",
            temperature=0.0,
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="aggregator",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You are the aggregator. Produce the final answer using both Specialist A notes and "
                "Specialist B notes. If either note set is missing, say 'insufficient specialist notes'. "
                "Do not ignore either specialist."
            ),
            user_prompt_template=(
                "Task:\n{user_input}\n\n"
                "Dispatch brief:\n{dispatch_brief}\n\n"
                "Specialist A notes:\n{specialist_a_notes}\n\n"
                "Specialist B notes:\n{specialist_b_notes}\n\n"
                "Write the final answer in 2-4 sentences."
            ),
            output_state_key="final_answer",
            temperature=0.0,
        )
    )

    b.set_entry("dispatcher")
    b.add_edge("dispatcher", "specialist_a")
    b.add_edge("dispatcher", "specialist_b")
    b.add_edge("specialist_a", "aggregator")
    b.add_edge("specialist_b", "aggregator")
    b.add_edge("aggregator", END)
    return b


def build_compiled():
    return build().compile()

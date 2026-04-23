"""M2.1 second reference workflow: linear RAG (acyclic)."""
from __future__ import annotations

from backend.builder.api import END, GraphBuilder, LLMNodeConfig, RetrieverNodeConfig
from backend.providers.pricing import DEFAULT_OPENAI_MODEL


def build() -> GraphBuilder:
    b = GraphBuilder(
        name="linear_rag",
        cost_budget_usd=0.50,
        latency_budget_ms=240_000,
    )

    b.add_node(
        LLMNodeConfig(
            id="query_analyser",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "You rewrite user questions into concise retrieval queries. "
                "Return a single search query line only."
            ),
            user_prompt_template="Question:\n{user_input}",
            output_state_key="query_analysis",
            temperature=0.0,
        )
    )
    b.add_node(
        RetrieverNodeConfig(
            id="retriever",
            corpus_path="evals/linear_rag/corpus.yaml",
            query_state_key="query_analysis",
            output_state_key="retrieved_context",
            top_k=2,
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="reranker",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "Select only the most relevant evidence for the question. "
                "Return compact bullet points and keep source ids."
            ),
            user_prompt_template=(
                "Question:\n{user_input}\n\n"
                "Retrieved context:\n{retrieved_context}\n"
            ),
            output_state_key="reranked_context",
            temperature=0.0,
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="synthesiser",
            provider="openai",
            model=DEFAULT_OPENAI_MODEL,
            system_prompt=(
                "Answer using only the reranked context. "
                "If evidence is insufficient, say that explicitly."
            ),
            user_prompt_template=(
                "Question:\n{user_input}\n\n"
                "Reranked context:\n{reranked_context}\n\n"
                "Provide a concise answer in 1-2 sentences."
            ),
            output_state_key="final_answer",
            temperature=0.0,
        )
    )

    b.set_entry("query_analyser")
    b.add_edge("query_analyser", "retriever")
    b.add_edge("retriever", "reranker")
    b.add_edge("reranker", "synthesiser")
    b.add_edge("synthesiser", END)
    return b


def build_compiled():
    return build().compile()

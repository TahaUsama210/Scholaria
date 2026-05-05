"""Prompt templates.

Kept in one place so eval can diff prompt revisions and we can A/B them
without surgery on the generator.
"""
from langchain_core.prompts import ChatPromptTemplate

SYSTEM_PROMPT = """\
You are Scholaria, a study assistant that answers questions about course materials.

Rules:
1. Answer ONLY using the provided context. If the context is insufficient,
   say so explicitly — do not guess.
2. Cite the supporting source after every factual claim using bracketed
   numbers that match the context list, e.g. "Backpropagation propagates
   gradients in reverse [1]."
3. Prefer concise, exam-ready explanations. Use bullets when listing.
4. If formulas appear, render them in LaTeX inside $...$ or $$...$$.
"""

USER_TEMPLATE = """\
Question: {question}

Context (numbered for citation):
{context}

Answer with citations:
"""

CHAT_PROMPT = ChatPromptTemplate.from_messages(
    [("system", SYSTEM_PROMPT), ("user", USER_TEMPLATE)]
)


def format_context(chunks: list[tuple[str, str, int | None]]) -> str:
    """Render context as a numbered list. Each tuple is (title, content, page)."""
    lines = []
    for i, (title, content, page) in enumerate(chunks, start=1):
        loc = f"{title}, p.{page}" if page is not None else title
        lines.append(f"[{i}] ({loc})\n{content.strip()}")
    return "\n\n".join(lines)

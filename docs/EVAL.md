# Evaluation

If you can't measure it, you can't improve it — and an LLM-driven feature regresses silently. The eval harness is what makes the rest of this project a system rather than a demo.

## What we measure

We use [Ragas](https://docs.ragas.io/) because its metrics map directly onto failure modes a reviewer will ask about:

| Metric | What it answers | Threshold |
| --- | --- | --- |
| **Faithfulness** | Are the model's claims actually in the retrieved context, or is it making things up? | ≥ 0.90 |
| **Context Precision** | Of the chunks I pulled, how many are actually relevant — and are the relevant ones at the top? | ≥ 0.80 |
| **Answer Relevancy** | Did the model answer the question that was asked? | ≥ 0.85 |
| **Context Recall** | Did the retriever find every chunk needed to answer? | ≥ 0.75 |

These are the four metrics Ragas itself recommends as the "minimum viable" set, and the thresholds match the production targets the 2026 RAG literature converges on.

## How it runs

```bash
# locally
cd backend
python -m scripts.seed_demo_docs   # populate Postgres with the demo PDFs
python -m eval.run_eval            # ↳ writes eval_results/{predictions,scores}.json
```

The harness:

1. Loads `eval/golden_dataset.json` (Q + ground-truth pairs over the seeded PDFs).
2. Runs each question through the *same* pipeline production uses — `RagPipeline.stream()` — and accumulates the streamed tokens into a final answer plus the `context` event into the retrieved-chunks list.
3. Writes raw predictions to `eval_results/predictions.json` so a regression can be inspected by a human (this is the biggest unlock — eval scores tell you *that* something broke; predictions tell you *what*).
4. Scores predictions with Ragas → `eval_results/scores.json`.
5. Exits non-zero if any metric < threshold. CI sees that as a failed check.

## CI gate

The `eval` job in `.github/workflows/ci.yml`:

- Triggers only on pull requests (CI minutes are precious; main pushes skip it).
- Spins up Postgres + pgvector as a service container.
- Skips itself with a warning if `OPENAI_API_KEY` isn't set as a repo secret — keeps forks green.
- Uploads `eval_results/` as an artefact regardless of pass/fail so a reviewer can see the diff.

## Building a meaningful golden dataset

The included `golden_dataset.json` is small (5 samples) on purpose — enough to demonstrate the harness without burning OpenAI credits in CI. To grow it for real use:

- **Aim for breadth, not size.** 30 well-chosen questions beats 300 near-duplicates. Cover: definitions, multi-hop questions, "out of scope" questions (the model must refuse rather than guess), formula recall, and adversarial paraphrases.
- **Include negative cases.** The `out_of_scope_handling` sample is a deliberate refusal test — it forces faithfulness to do real work.
- **Version the dataset.** Bumping `version` and writing a changelog comment when you add samples lets you compare scores across dataset versions, not just code versions.

## What this catches in practice

| Change in the codebase | Metric that moves first |
| --- | --- |
| Switch to a smaller embedding model | `context_recall` drops |
| Lower `RERANK_TOP_N` | `context_precision` rises, `context_recall` drops |
| Tweak the system prompt to be less strict about citations | `faithfulness` drops |
| Increase `chunk_size_tokens` past ~800 | `answer_relevancy` drops (chunks become diluted) |
| Drop the reranker | `context_precision` collapses; `faithfulness` follows |

That mapping is the whole point: the harness turns prompt-and-retrieval guesswork into a numerical regression test.

# Evaluation

Scholaria's quality claims are backed by two offline harnesses that run against the real code paths:

- **Retrieval eval** (`python -m eval.retrieval_eval`): ranks a 55-question golden set with four configurations (dense only, sparse only, hybrid RRF, hybrid RRF + cross-encoder rerank) and scores them with standard IR metrics.
- **Generation eval** (`python -m eval.generation_eval`): sends every question through the full `RagPipeline.stream()` and checks the streamed answers deterministically (citation validity and coverage, context recall, refusals). An optional LLM-as-judge adds faithfulness and correctness scores when a real LLM is configured.

Both are in-house and dependency-light (the metric functions are about 170 lines in `eval/metrics.py`), need no paid API, and exit non-zero when the production configuration regresses below versioned thresholds. Every number on this page was produced by those commands on 2026-11-03 (machine and settings are listed with each result).

## Headline results

Free "real" profile: `all-MiniLM-L6-v2` embeddings (384-d), `ms-marco-MiniLM-L-6-v2` cross-encoder, dense/sparse top-k 20/20, RRF k=60, 512-token chunks (39 chunks). 47 ranked questions; the 8 out-of-scope questions are excluded from ranking metrics.

| Configuration | hit@1 | recall@5 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| Dense only (pgvector cosine) | 0.766 | 0.979 | 0.849 | 0.867 |
| Sparse only (Postgres FTS) | 0.723 | 0.957 | 0.836 | 0.865 |
| Hybrid (RRF) | 0.766 | 0.979 | 0.869 | 0.897 |
| **Hybrid (RRF) + rerank (production)** | **0.915** | **0.979** | **0.950** | **0.962** |

Paired differences with 95% bootstrap confidence intervals (5,000 resamples of the 47 questions):

| Comparison | ΔMRR@10 | 95% CI | Δhit@1 | 95% CI |
|---|---|---|---|---|
| dense → hybrid | +0.020 | [−0.033, +0.074] | +0.000 | [−0.085, +0.085] |
| dense → hybrid + rerank | +0.101 | [+0.024, +0.184] | +0.149 | [+0.021, +0.277] |
| hybrid → hybrid + rerank | +0.081 | [+0.010, +0.158] | +0.149 | [+0.021, +0.277] |

What this says, plainly:

1. **Hybrid RRF on its own does not measurably beat dense-only retrieval** on this corpus. The +0.020 MRR@10 gain has a confidence interval that straddles zero.
2. **The cross-encoder reranker is where the gain comes from.** Hybrid + rerank beats dense-only by +0.101 MRR@10 (0.849 → 0.950, +11.9% relative) and +0.149 hit@1 (0.766 → 0.915, +19.5% relative), and both intervals exclude zero.
3. **The old claim** that "hybrid retrieval + reranker improved answer relevancy by 30% over naive vector search" **is not supported**. Answer relevancy was never measured, and the retrieval gain measured here is about 12% relative on MRR@10 and 20% on hit@1. Recall@5 sits near its ceiling for every configuration (0.957–0.979), because 5 results cover a large share of a 32-section corpus, so it cannot separate the configurations.

## What is measured, and why

| Layer | Question it answers | Harness |
|---|---|---|
| Retrieval ranking | Is the section that answers the question ranked first? Are all needed sections in the top 5? | `eval.retrieval_eval` |
| Grounding mechanics | Do answers cite real passages, cite every claim, and use context that contains the answer? | `eval.generation_eval` (deterministic) |
| Refusal behaviour | Does the assistant decline questions the notes don't cover, without refusing answerable ones? | `eval.generation_eval` (deterministic) |
| Answer quality | Is the answer faithful to the passages and correct against the reference? | `eval.generation_eval` LLM-as-judge (optional) |

The previous Ragas harness was removed. Ragas needs paid LLM calls for every metric, its 0.2 API change had broken the code (`result.items()`), and its scores are themselves LLM judgements that cannot gate CI on a fork without secrets. The replacements are deterministic wherever possible, and the LLM-dependent part is isolated and optional.

## Corpus

`backend/scripts/corpus/*.md` holds original course notes for four courses, with 8 sections each (32 sections, 321–384 words per section):

| Course | Sections |
|---|---|
| CS480 Machine Learning | backpropagation · overfitting and regularisation · bias-variance · activation functions · optimizers · model evaluation · trees and ensembles · k-means |
| CS348 Database Systems | FDs and 3NF · BCNF · ACID · indexes (B-tree, hash, HNSW) · buffer pool · concurrency control · WAL and recovery · replication and consistency |
| CS350 Operating Systems | processes and `fork()` · signals · CPU scheduling · threads and synchronisation · deadlock · virtual memory and page tables · page replacement · file systems and journaling |
| CS456 Computer Networks | TCP connections · flow control · congestion control · IP and routing · DNS · link layer and ARP · router queues and scheduling · HTTP and QUIC |

The corpus is designed so that retrieval is not trivial:

- **Confusable topics across courses:** buffer-pool LRU vs page-replacement LRU, WAL vs file-system journaling, 2PL deadlock vs the Coffman conditions, CPU round robin vs packet round robin, B+ trees vs multi-level page tables, ACID consistency vs replica consistency, IP TTL vs DNS TTL, flow control vs congestion control.
- **Exact identifiers that favour keyword search:** BCNF, SIGKILL, TCP Reno, ReLU, `fork()`, WAL, LRU, TIME_WAIT, ARIES, CNAME, CoDel, vruntime, O_DIRECT.
- **Paraphrase targets:** concepts are explained in prose, so questions can ask about them without the textbook terms.
- **Original facts preserved:** the six sections of the earlier toy corpus (backpropagation, overfitting, bias-variance, 3NF, ACID, indexes) keep their facts, and unit tests assert the key phrases.

`python -m scripts.seed_demo_docs` renders one PDF per course with reportlab: **one section per page**, a running header, a "Page n of 8" footer, and width-based wrapping. It checks that each section fits on its page. Rendering uses reportlab's invariant mode, so the same Markdown always produces byte-identical PDFs and the same content hashes. The script then ingests the PDFs through the real ingestion service (`register_upload` + `ingest_document`). Re-running it is idempotent: unchanged documents are skipped, older versions of a demo file are replaced, and `--reingest` forces re-chunking after a chunking or model change. `--pdf-only` writes the PDFs without touching a database. The PDFs (`backend/scripts/demo_docs/`) are git-ignored build outputs. The Markdown is the only source of truth.

## Golden dataset v2

`backend/eval/golden_dataset.json` holds 55 questions. Each has relevance labels `[{course_code, page}]` (one page = one section) and a short reference answer.

| Category | n | What it tests | Mean lexical overlap* |
|---|---|---|---|
| definition | 12 | direct "what is X" questions | 0.82 |
| keyword | 13 | exact identifiers (field `keyword`, which must appear in a labelled section) | 0.89 |
| paraphrase | 12 | the concept asked without its vocabulary ("a program duplicates itself…") | 0.36 |
| multi_hop | 10 | needs two sections, often across courses (e.g. LRU in DB vs OS) | 0.69 |
| out_of_scope | 8 | nothing relevant: 5 far off-topic, 3 near-domain (Rust borrow checker, Kubernetes, RSA) | n/a |

\*Share of the question's content words found in its best labelled section, computed by `python -m eval.dataset`. The low paraphrase overlap is what makes that category a test of semantic retrieval.

27 of the 32 sections are the target of at least one question. Validation runs programmatically (`python -m eval.dataset`, and again at the start of every eval run, plus unit tests). It checks the schema, unique ids, that every label points to an existing course and page, that multi_hop questions have two or more labels and out_of_scope questions have none, and that each keyword appears in both the question and a labelled section.

## Metric definitions

**Ranking.** Retrieved chunks are mapped to their section `(course, page)` and de-duplicated, keeping the first occurrence. Relevance is binary. Out-of-scope questions are excluded.

- **hit@1**: 1 if the top-ranked section is relevant.
- **recall@5**: fraction of the relevant sections in the top 5. Multi-hop questions get partial credit.
- **MRR@10**: 1 / rank of the first relevant section within the top 10, else 0.
- **nDCG@10**: DCG with gain 1/log2(rank+1) for relevant sections, normalised by the ideal ordering.

All four configurations rank **the same candidate pool**: the dense and sparse lists returned by `HybridRetriever._dense_and_sparse` with the production settings. Hybrid is `HybridRetriever._rrf` over those lists. Rerank scores the whole fused pool with `make_reranker(settings)`; production then keeps the top `RERANK_TOP_N` = 5. Retrieval is unfiltered across all courses, so cross-course confusions count. Differences between configurations are reported as paired bootstrap 95% CIs over questions.

**Generation.** These are computed on the streamed SSE events of `RagPipeline.stream()`.

- **non_empty_rate**: answers with visible text.
- **citation_validity**: answers whose every `[n]` (including `[1, 2]` groups) refers to a passage in the `context` event.
- **citation_coverage**: share of claim sentences (4 or more words, not a lead-in ending in ":") carrying at least one citation. Averaged over answered in-scope questions.
- **context_recall**: share of labelled sections present in the context the generator received.
- **cited_relevant_rate**: answered in-scope questions that cite at least one labelled section.
- **refusal_accuracy**: out-of-scope questions answered with a refusal. **false_refusal_rate**: in-scope questions refused. Refusals are detected heuristically: phrases such as "couldn't find" or "the materials don't cover" within the first 300 characters.
- **error_rate**: streams that raised or emitted an `error` event.
- **LLM-as-judge** (optional): a 1–5 rubric for faithfulness to the full text of the retrieved chunks and for correctness against the reference answer, normalised to [0, 1]. It is enabled when `LLM_PROVIDER=openai` and `LLM_API_KEY`/`OPENAI_API_KEY` or a keyless `LLM_BASE_URL` (Ollama) is set. `EVAL_JUDGE_MODEL` overrides the model. Otherwise the run prints why the judge was skipped.

## Detailed results

Machine: AMD Ryzen 7 5800X (16 threads), CPU-only PyTorch, Windows 11, Postgres 16 + pgvector in Docker. Latency numbers are specific to this machine.

### Free real profile (MiniLM + cross-encoder)

Per category (hit@1 / MRR@10):

| Category | Dense | Sparse | Hybrid | Hybrid + rerank |
|---|---|---|---|---|
| definition (12) | 0.667 / 0.833 | 0.917 / 0.958 | 0.750 / 0.875 | 0.917 / 0.958 |
| keyword (13) | 0.769 / 0.810 | 0.615 / 0.795 | 0.846 / 0.897 | 0.923 / 0.962 |
| paraphrase (12) | 0.667 / 0.781 | 0.500 / 0.665 | 0.583 / 0.764 | 0.833 / 0.887 |
| multi_hop (10) | 1.000 / 1.000 | 0.900 / 0.950 | 0.900 / 0.950 | 1.000 / 1.000 |

- Keyword questions show the expected hybrid benefit: hybrid 0.897 vs dense 0.810 MRR@10.
- Paraphrase questions show the expected sparse weakness: sparse 0.665, and fusing it in pulls hybrid (0.764) below dense (0.781). The reranker recovers this (0.887).
- The production configuration misses rank 1 on 4 of the 47 questions:
  - `def-3nf`: top-1 is the BCNF section, which also discusses 3NF.
  - `kw-time-wait`: top-1 is the OS deadlock section.
  - `para-congestion`: flow control is ranked above congestion control.
  - `para-bcnf-tradeoff`: the relevant section is ranked 7th.

Stage latency per query: embed 7.2 ms mean, dense + sparse SQL 5.6 ms mean, and RRF + rerank of the about 25-chunk pool 969 ms mean (1143 ms p95). The cross-encoder dominates end-to-end latency.

Generation with `LLM_PROVIDER=fake` (deterministic extractive answers):

| Relevance gate | non_empty | citation_validity | citation_coverage | context_recall | cited_relevant | refusal_accuracy | false_refusal | error_rate |
|---|---|---|---|---|---|---|---|---|
| off | 1.000 | 1.000 | 1.000 | 0.979 | 0.979 | 0.000 (0/8) | 0.000 | 0.000 |
| **`RERANK_MIN_SCORE=-9` (default in `.env.example`)** | 1.000 | 1.000 | 1.000 | 0.979 | 0.979 | **0.750 (6/8)** | **0.000 (0/47)** | 0.000 |

Context recall is 1.000 in every category except paraphrase (0.917). Time to first token p50 1025 ms, p95 1187 ms, dominated by reranking. Without the relevance gate, **refusal accuracy is 0/8**: the pipeline only refused when retrieval returned nothing, and dense retrieval always returns rows. The gate (`RERANK_MIN_SCORE`, from the score-floor experiment below) refuses when the best reranked chunk scores below −9, which lifts refusal accuracy to 6/8 with no false refusals. The two misses are near-domain questions (see the floor table). The floor was tuned on this same dataset, so treat 6/8 as an optimistic, in-sample number. The LLM-as-judge was not run, because no LLM key was available.

### Offline profile (CI default: `EMBEDDING_PROVIDER=hash`, `EMBEDDING_DIM=256`, `RERANKER_PROVIDER=none`)

No model downloads or network. "Dense" here is lexical feature hashing, not a semantic model.

| Configuration | hit@1 | recall@5 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| Dense (hash) | 0.617 | 0.872 | 0.736 | 0.778 |
| Sparse (FTS) | 0.723 | 0.957 | 0.829 | 0.861 |
| Hybrid (RRF) = production (no reranker) | 0.702 | 0.947 | 0.825 | 0.864 |

Hybrid beats hash-dense (ΔMRR@10 +0.088, 95% CI [+0.022, +0.159]) but not sparse-only. Paraphrase is the weak spot: hash-dense MRR@10 is 0.419 and hybrid 0.618. Generation with the fake LLM: non_empty 1.000, citation_validity 1.000, citation_coverage 1.000, context_recall 0.947 (paraphrase 0.833), cited_relevant 0.936, refusal_accuracy 0.000, false_refusal 0.000, errors 0.000. Time to first token p50 7 ms.

### Experiments (`--sweep`, one knob at a time; overall MRR@10 on 47 questions)

**Sparse query: production OR-of-lexemes vs the original `plainto_tsquery` (AND of all terms).** With AND, FTS matched nothing for 26 of the 47 in-scope questions, and sparse-only MRR@10 was 0.426.

| Profile | Config | Production (OR) | Original (AND) |
|---|---|---|---|
| MiniLM | hybrid | 0.867 | 0.901 |
| MiniLM | hybrid + rerank | 0.950 | 0.950 |
| hash | hybrid (production) | 0.824 | 0.790 |

These sweep values were measured before FTS ties were broken by `(document_id, chunk_index)` (they used chunk UUID), so they can differ from the main table by about 0.001. OR helps when there is no reranker (hash profile). It is neutral under rerank, which is the production configuration. AND gives a better un-reranked hybrid with semantic embeddings because a sparse list only appears when the match is precise. No significance test was run on the sweep variants.

**Candidate depth (dense/sparse top-k).** With rerank, top-k 5 (a pool of about 7 chunks) scores the same as 20 or 40 (MRR@10 0.951 vs 0.950). On this 39-chunk corpus, depth does not limit quality, but rerank latency grows with pool size.

**RRF k.** For hybrid without rerank, k ∈ {1, 10, 30, 60, 100} spans 0.865–0.875 MRR@10 (MiniLM) and 0.817–0.828 (hash). k does not matter, and it has no effect under rerank, because the cross-encoder re-scores the whole pool.

**Chunk size.** MiniLM truncates input at 256 word pieces, and the default chunks average about 405 tokens. Re-ingesting at `CHUNK_SIZE_TOKENS=256`, `CHUNK_OVERLAP_TOKENS=32` (83 chunks) gives these MRR@10 values:

| Config | 512 tokens | 256 tokens |
|---|---|---|
| dense | 0.849 | 0.901 |
| hybrid | 0.867 | 0.925 |
| hybrid + rerank | 0.950 | 0.926 |

Smaller chunks help the bi-encoder but hurt the reranked production configuration, so 512 stays the default.

**Refusal by rerank-score floor.** The top cross-encoder score of in-scope questions bottoms out at −8.89. Out-of-scope questions peak at −6.23, from the near-domain RSA question. Refusing below a floor gives:

| Floor | Out-of-scope refused (of 8) | In-scope refused (of 47) |
|---|---|---|
| −10 | 4 | 0 |
| −9 | 6 | 0 |
| −8 | 6 | 2 |
| −7 | 7 | 2 |
| −6 | 8 | 5 |

## How to run

From `backend/`, with `pip install -e ".[dev,eval]"`, against a database you are allowed to reset:

```bash
export DATABASE_URL=postgresql+asyncpg://scholaria:scholaria@localhost:5432/scholaria_eval
export DATABASE_URL_SYNC=postgresql+psycopg://scholaria:scholaria@localhost:5432/scholaria_eval

# Free real profile (models download from Hugging Face on first use, about 180 MB)
export EMBEDDING_PROVIDER=local EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2 \
       EMBEDDING_DIM=384 RERANKER_PROVIDER=local LLM_PROVIDER=fake
# ...or the offline profile:
# export EMBEDDING_PROVIDER=hash EMBEDDING_DIM=256 RERANKER_PROVIDER=none LLM_PROVIDER=fake

alembic upgrade head                 # the vector column takes EMBEDDING_DIM at migrate time:
                                     # changing the dimension needs a fresh schema
python -m scripts.seed_demo_docs     # render + ingest (idempotent; --reingest after chunking changes)
python -m eval.dataset               # validate labels, print dataset stats
python -m eval.retrieval_eval        # add --sweep for the experiments above
python -m eval.generation_eval       # add LLM_PROVIDER=openai + a key to enable the judge
```

Outputs go to `eval_results/` (git-ignored):

- `retrieval.{json,md}`: overall and per-category metrics, CIs, diagnostics, misses and the sweep, plus per-question top-5 sections and ranks in the JSON.
- `generation.{json,md}`: metrics, latency, and per-question answers, citations and judge reasons.

Flags: `--out DIR`, `--profile NAME` (force a threshold profile), `--no-gate`, plus `--no-judge` and `--limit N` for the generation eval. The evals refuse to run (exit 2) if the database does not contain the current corpus. They compare the content hashes of the rendered PDFs, so stale or missing seeds are caught. The JSON also records chunk token statistics as ingested.

## CI gating

`backend/eval/thresholds.json` holds threshold profiles. A run uses the first profile whose `match` block equals the current settings (providers, models, embedding dim), or the profile named by `--profile`. Retrieval gates the production ranking (hybrid + rerank, which equals hybrid when `RERANKER_PROVIDER=none`) on all four metrics. Generation gates non-empty answers, citation validity, coverage, context recall, cited-relevant rate (minimums), false-refusal rate and error rate (maximums), plus refusal accuracy when the relevance gate is on.

Each minimum is the measured value minus 0.04, rounded down to 2 decimals, which is about two questions of slack. Hard invariants are exact: non-empty answers 1.0, valid citations 1.0, zero errors. Refusal accuracy is gated only by the `fake-llm-local-minilm-refusal` profile (`RERANK_MIN_SCORE=-9`, min 0.62, about one question of slack). Without the gate it is 0 by construction under the fake LLM. With no matching profile (e.g. OpenAI embeddings), the report says so and exits 0. Exit codes: 0 = pass or not gated, 1 = below threshold, 2 = invalid dataset or unseeded database.

| Profile | Gated metrics (min) |
|---|---|
| `local-minilm` (retrieval) | hit@1 0.87, recall@5 0.93, MRR@10 0.91, nDCG@10 0.92 |
| `offline-hash` (retrieval) | hit@1 0.66, recall@5 0.90, MRR@10 0.78, nDCG@10 0.82 |
| `fake-llm-local-minilm-refusal` (generation, `RERANK_MIN_SCORE=-9`) | as `fake-llm-local-minilm`, plus refusal_accuracy 0.62 |
| `fake-llm-local-minilm` / `fake-llm-offline-hash` (generation) | non_empty 1.0, citation_validity 1.0, coverage 0.96, context_recall 0.93 / 0.90, cited_relevant 0.93 / 0.89; false_refusal ≤ 0.05, errors 0 |

The offline profile suits PR CI: no secrets and no downloads, and the measured pipeline latency is 8 ms p50 per question. The MiniLM profile needs a Hugging Face model cache and takes about 2.5 minutes with `--sweep` on the machine above. Checks were made that the gate works in practice: offline results run against the `local-minilm` profile exit 1. When a deliberate change moves the numbers, update `measured` and `min` together in the same PR, so the diff shows the trade-off.

Re-seeding between runs reproduced the production config and dense-only exactly. Sparse and hybrid moved by up to 0.011 MRR@10 (hybrid 0.858–0.869 across three ingests), because FTS score ties were broken by chunk UUID, which changes on every ingest. Ties are now broken by `(document_id, chunk_index)`. Document ids still change on a full re-ingest, so cross-document ties can still flip: two later re-ingests moved hybrid MRR@10 between 0.866 and 0.869. The production configuration stayed at 0.950 throughout.

## Limitations

- **Small corpus.** 32 sections and 39 chunks. Recall@5 is near its ceiling for every configuration, and absolute numbers are higher than on a real course library. Differences under about 0.08 MRR@10 are within noise at n=47, as the CIs show.
- **Labels by the corpus author.** The questions and their labels were written by the same author as the notes, which can favour the author's own phrasing. Labels are section-level: a chunk from an unlabelled section that happens to answer the question counts as a miss (e.g. `def-3nf` → BCNF page).
- **The fake LLM measures plumbing, not language quality.** Citation validity and coverage are 1.0 by construction for the extractive fake generator. They become meaningful with a real LLM, together with the judge, which was **not run** for this report (no key available). The judge grades with the generator's model unless `EVAL_JUDGE_MODEL` is set, which is self-grading bias.
- **Heuristic refusal detection.** It is phrase-based and checks only the opening of the answer. An LLM that refuses in unusual wording would be missed.
- **Single-turn only.** History and condensation are not evaluated.
- **Platform-specific latency.** Latency was measured on one CPU-only desktop, with no concurrency.

## Recommendations

Applied: the relevance gate (`RERANK_MIN_SCORE`, a setting because the floor is specific to the cross-encoder model; re-validate with the `score_floor` table after changing models), and deterministic FTS tie-breaking by `(document_id, chunk_index)`. Still open:

1. **Rerank a smaller pool.** Dense/sparse top-k 5–10 gave the same quality here, while rerank cost scales with pool size (about 1 s for 25 chunks on CPU). Re-check on a larger corpus before lowering the defaults.
2. **Keep OR-of-lexemes FTS.** It helps the reranker-less profile and is neutral with rerank.
3. **Keep 512-token chunks** for the reranked configuration. Use about 256 if running dense-only with MiniLM.

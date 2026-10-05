# Aegis Knowledge Engine

Evidence-first engineering knowledge ingestion and question answering for the **Aegis Series-7 Hydraulic Control System** corpus.

> **Core principle:** LLMs may assist with extraction and language, but deterministic code controls entity identity, applicability, scope, conflicts, answerability, provenance, and the final answer contract.

---

## What this project does

Aegis ingests a heterogeneous engineering corpus and converts it into a structured knowledge representation:

```text
Documents
   ↓
Elements
   ↓
Entities + Claims + Evidence + Relations
   ↓
SQLite / FTS5
   ↓
Question analysis
   ↓
Entity + scope resolution
   ↓
Answerability gate
   ↓
Contract-verified answer
```

The system is designed around the failure modes that matter in engineering QA:

- similar component identifiers
- revision-dependent values
- conflicting documents
- low-trust field observations
- diagram/screenshot evidence
- missing attributes
- unsupported assumptions
- citation and provenance requirements

---

## Key Features

### 1. Structured provenance

Answers can be traced from:

```text
answer → claim → evidence → source element → document
```

with source locators and quotes.

### 2. Deterministic entity resolution

The system distinguishes identifiers such as:

```text
PS-04
PS-04A
PS-40
```

while normalizing formatting variants such as:

```text
P.S.04-A
PS_04A
ps-04a
```

### 3. Revision-aware knowledge

Claims carry scope so that values that change between revisions are not silently mixed.

### 4. Conflict handling

Conflicting applicable claims are surfaced rather than averaged or silently overwritten.

### 5. Explicit abstention

The system can return:

- `ANSWERED`
- `ANSWERED_SCOPED`
- `CONFLICTED`
- `PARTIAL`
- `UNANSWERABLE`

Missing information is reported as a search result rather than converted into an unsupported real-world claim.

### 6. Evidence-backed answer contract

The deterministic verifier checks citations, values, scope labels, decision records, and forbidden values before an answer is returned.

### 7. Reproducible raster handling

Scanned and raster material is represented through reviewed transcription sidecars in the evaluated build. This keeps the benchmark path reproducible instead of depending on nondeterministic live vision inference.

---

## Technology

- Python
- FastAPI
- SQLite
- SQLite FTS5
- Pydantic
- PDF/DOCX/XLSX/HTML/JSON/PPTX ingestion adapters
- deterministic rule-based extraction
- optional Anthropic LLM adapter
- static browser frontend

The evaluated core does **not** require a vector database, Neo4j, Kubernetes, or a separate inference server.

---

## Running the application

### 1. Create/activate the virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

For development/testing:

```powershell
pip install -r requirements-dev.txt
```

### 3. Start the API

From the repository root:

```powershell
$env:PYTHONPATH="src"
uvicorn aegis.api.app:create_app --factory --reload
```

The application is available at:

```text
http://127.0.0.1:8000
```

API documentation:

```text
http://127.0.0.1:8000/api/docs
```

Health endpoint:

```text
http://127.0.0.1:8000/healthz
```

---

## Repository Layout

```text
aegis/
├── src/aegis/
│   ├── api/             # FastAPI service
│   ├── ingest/          # document ingestion
│   ├── models/          # domain models
│   ├── query/           # question analysis and answering
│   ├── store/           # SQLite / FTS5 knowledge store
│   ├── llm/             # optional LLM adapter
│   └── config.py
├── data/
│   ├── aegis.db         # structured knowledge store
│   └── transcriptions/  # reviewed raster/scanned transcriptions
├── eval/                # evaluation harness
├── tests/               # tests
├── docs/
│   └── architecture.md
├── requirements.txt
├── requirements-dev.txt
├── README.md
└── .gitignore
```

---

## Knowledge Store

The packaged knowledge store contains the structured representation of the Aegis corpus.

The database contains:

- documents
- source elements
- entities
- aliases
- claims
- evidence
- relations
- search/trace information

The store can be rebuilt from the source corpus using the ingestion pipeline.

---

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the complete design.

The important architectural boundary is:

```text
                    ┌─────────────────────┐
                    │      Retrieval      │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Entity + Scope     │
                    │ Applicability      │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Answerability Gate │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Answer Contract    │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Deterministic      │
                    │ Verifier           │
                    └─────────────────────┘
```

---

## Evaluation Results

The deterministic evaluated build was tested against the provided official and held-out question sets.

### Official set

**23 questions**

| Metric | Result |
|---|---:|
| Status exact | **23 / 23** |
| Critical errors | **0** |
| Major errors | **0** |

### Held-out set

**40 questions**

| Metric | Result |
|---|---:|
| Status exact | **24 / 40** |
| Critical errors | **0** |
| Major errors | **10** |

The held-out results are intentionally reported as-is. The remaining errors are primarily recall/generalization limitations in long-tail question phrasing rather than unsafe entity/value substitutions.

### Important evaluation note

The reported benchmark results are from the **deterministic evaluated path**. The optional LLM adapter is not required for these results.

This avoids presenting a failed or unavailable external-model call as benchmark evidence.

---

## Design Decisions

### Why not make a vector database the core?

The corpus contains many exact identifiers, revision constraints, structured fields, and relationships.

For example:

```text
PS-04
PS-04A
PS-40
```

A semantically similar result is not necessarily the correct engineering entity.

SQLite + FTS5 gives deterministic lookup and makes scope/entity filtering explicit and reproducible.

Semantic retrieval can be introduced as an auxiliary candidate generator without allowing similarity to decide truth.

### Why deterministic answering?

An LLM can generate a convincing answer from the wrong revision or wrong component.

Aegis therefore makes the LLM subordinate to a deterministic evidence contract:

```text
LLM proposal
    ↓
evidence validation
    ↓
scope validation
    ↓
entity validation
    ↓
applicability
    ↓
answerability
    ↓
contract verification
```

Unsupported claims cannot be promoted simply because a model sounds confident.

---

## LLM Integration

The repository contains an optional Anthropic client for long-tail analysis/extraction.

It is intentionally outside the deterministic safety boundary.

The design rule is:

> **An LLM may veto or demote a claim, but it may never approve an unsupported claim.**

The deterministic path remains fully usable without an external model.

If using the optional adapter, configure the API key through an environment variable:

```powershell
$env:ANTHROPIC_API_KEY="..."
```

Do **not** commit API keys or `.env` files.

---

## Evaluation Philosophy

Aegis evaluates more than whether text can be retrieved.

Important dimensions include:

- exact entity resolution
- revision/scope correctness
- answerability
- evidence provenance
- citation validity
- conflict handling
- abstention
- critical-error avoidance

A wrong number, wrong component, wrong revision, or unsupported answer is more serious than imperfect wording.

---

## Safety / Correctness Boundary

The system deliberately separates:

```text
Retrieval
    ↓
Candidate evidence

Applicability
    ↓
Which evidence applies?

Answerability
    ↓
Is there enough evidence?

Composition
    ↓
How should the answer be written?

Verification
    ↓
Does the output obey the evidence contract?
```

This prevents retrieval ranking or language-model fluency from becoming an implicit truth mechanism.

---

## Known Limitations

- The evaluated question analyzer is primarily deterministic and has limited coverage of unusual paraphrases.
- The held-out result demonstrates that long-tail question generalization remains an area for improvement.
- Raster evidence in the evaluated build uses reviewed transcription sidecars rather than claiming live VLM benchmark performance.
- The system reports source conflicts; it does not independently determine which conflicting source is factually true.
- The supplied benchmark is small and should not be interpreted as a production reliability guarantee.

---

## Submission Summary

Aegis is intentionally built as a **small, auditable evidence engine rather than a generic RAG chatbot**.

Its core contribution is the representation and decision layer:

```text
heterogeneous sources
        ↓
structured claims
        ↓
typed entities
        ↓
explicit scope
        ↓
evidence/provenance
        ↓
deterministic applicability
        ↓
answerability
        ↓
contract-verified answer
```

The evaluated deterministic build achieves:

**23/23 exact official statuses with 0 critical and 0 major errors**, while the held-out evaluation achieves **24/40 exact statuses with 0 critical errors**.

That result is reported without hiding the remaining generalization gap.

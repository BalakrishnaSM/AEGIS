# Aegis Knowledge Engine — Architecture

## 1. Overview

Aegis is an evidence-first knowledge engine for answering engineering questions over a heterogeneous technical corpus.

The central design principle is:

> **LLMs may assist with language and extraction, but deterministic code decides identity, applicability, scope, conflicts, answerability, provenance, and what information is allowed into the final answer.**

This is deliberate. In an engineering knowledge system, a fluent answer is not sufficient: the system must be able to explain **where a fact came from, which entity and revision it applies to, whether competing values exist, and when the available evidence is insufficient to answer.**

The evaluated build therefore keeps the critical decision path deterministic.

---

## 2. Problem Characteristics

The Aegis corpus contains approximately 20 heterogeneous source files across formats including:

- PDF manuals and engineering bulletins
- HTML legacy documentation
- XLSX reference/configuration data
- DOCX terminology and field notes
- JSON configuration
- PPTX training material
- PNG screenshots and electrical diagrams
- scanned technical material

The difficult parts are not simply retrieving text. The system must handle:

1. **Entity identity**
   - `PS-04`, `PS-04A`, and `PS-40` are distinct entities.
   - Human-readable aliases such as `P.S.04-A` must normalize deterministically.

2. **Revision and applicability**
   - A value may apply only before or after a software revision.
   - Scope must be represented explicitly rather than inferred from retrieval order.

3. **Conflicting evidence**
   - A field note may disagree with a controlled manual.
   - Conflicts are surfaced instead of silently merged.

4. **Cross-modal evidence**
   - Some relationships exist in diagrams or screenshots rather than prose.

5. **Abstention**
   - Missing attributes and unsupported assumptions must result in a controlled non-answer rather than a fabricated answer.

---

## 3. High-Level Architecture

```text
                         ┌──────────────────────────────┐
                         │        Source Corpus         │
                         │ PDF HTML XLSX DOCX JSON     │
                         │ PPTX PNG + scanned material │
                         └──────────────┬───────────────┘
                                        │
                                        ▼
                         ┌──────────────────────────────┐
                         │      Ingestion Pipeline      │
                         │ format adapters / elements   │
                         │ normalization / extraction   │
                         │ raster transcription         │
                         └──────────────┬───────────────┘
                                        │
                                        ▼
                  ┌─────────────────────────────────────────┐
                  │        Knowledge Representation         │
                  │ Documents • Elements • Entities         │
                  │ Claims • Evidence • Relations • Scope   │
                  └──────────────────┬──────────────────────┘
                                     │
                                     ▼
                         ┌──────────────────────────────┐
                         │        SQLite + FTS5         │
                         │ structured lookup + search   │
                         └──────────────┬───────────────┘
                                        │
                         User Question  │
                                        ▼
                         ┌──────────────────────────────┐
                         │       Query Analyzer         │
                         │ intent + entity + slots     │
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ Applicability / Scope        │
                         │ resolver + entity resolution │
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │      Answerability Gate      │
                         │ answered / scoped / conflict │
                         │ partial / unanswerable       │
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ Deterministic Composer       │
                         │ + Contract Verifier          │
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ Answer + Evidence + Trace    │
                         └──────────────────────────────┘
```

---

## 4. Ingestion Pipeline

The ingestion pipeline is rebuildable and separates source processing from query-time reasoning.

```text
Raw file
  │
  ├── format adapter
  │
  ▼
Document + Elements
  │
  ├── source metadata
  ├── locators
  ├── hashes
  ├── page/cell/slide information
  └── raster regions where applicable
  │
  ▼
Normalization
  │
  ├── entity aliases
  ├── identifiers
  ├── units
  ├── revisions
  └── terminology
  │
  ▼
Claim extraction
  │
  ├── structured/rule extraction
  └── reviewed raster transcriptions
  │
  ▼
Validation
  │
  ├── evidence verification
  ├── value/type checks
  ├── scope checks
  └── condition checks
  │
  ▼
Entity resolution + relations
  │
  ▼
SQLite knowledge store
```

The evaluated build uses deterministic extraction rules for the controlled corpus. The LLM extraction path exists as an optional adapter and is not required for the deterministic evaluation results.

### Raster material

Raster/scanned material is represented through reviewed transcription sidecars. This keeps the evaluated build deterministic and reproducible while preserving source provenance and locators.

A live VLM call is intentionally not required for the evaluated release.

---

## 5. Knowledge Representation

The knowledge store separates source material from claims derived from it.

### Core objects

### Document

Represents an original source and its metadata:

- source path
- document type
- trust/tier
- completeness
- hash
- applicability metadata

### Element

Represents an addressable piece of source material:

- text/content
- page/slide/cell locator
- bounding box where applicable
- source hash

### Entity

Represents a normalized engineering object.

Examples:

```text
PS-04
PS-04A
PS-40
PLC-03
```

Aliases are stored separately so normalization is explicit and auditable.

### Claim

A structured assertion:

```text
(entity, predicate, value, scope, condition, assertion_kind)
```

Examples include:

- operating pressure
- alarm behavior
- supply characteristics
- component identity
- revision applicability

### Evidence

Links a claim back to source material with a locator and source quote.

### Relation

Represents explicit or inferred relationships between entities or claims.

---

## 6. Scope and Applicability

Scope is treated as first-class data.

A claim is not merely:

```text
PS-04A → pressure → 180 bar
```

It can instead be represented as:

```text
PS-04A
  └── operating_pressure
        ├── 180 bar → revision < 3.2
        └── 200 bar → revision >= 3.2
```

The system distinguishes:

- `ANY`
- explicit revision scope
- unknown scope
- document-derived/default scope

`ANY` must never be confused with `UNKNOWN`.

Applicability is resolved at query time using:

1. entity constraints
2. revision constraints
3. document/source scope
4. claim conditions
5. supersession relations

This prevents retrieval order from deciding which value is authoritative for a question.

---

## 7. Evidence and Trust

A source is not treated as equally authoritative merely because it contains matching text.

Evidence carries metadata such as:

- assertion kind
- extraction method
- validation summary
- source tier
- scope basis
- corroboration
- conflicts
- quality

Typical source distinctions include controlled manuals, engineering change notices, reference data, training material, and low-trust field notes.

A low-trust observation is not silently merged into a controlled engineering value.

Instead, the answer can surface it as an advisory or conflict.

---

## 8. Entity Resolution

Entity resolution is deterministic for known engineering identifiers.

Normalization handles variants such as:

```text
P.S.04-A
PS-04A
ps_04a
```

while avoiding fuzzy matching that could incorrectly merge:

```text
PS-04
PS-04A
PS-40
```

This is especially important because an apparently small identifier error can produce a completely wrong engineering answer.

---

## 9. Query Pipeline

A question follows this path:

```text
Question
   │
   ▼
Intent / slot analysis
   │
   ▼
Mention + entity resolution
   │
   ▼
Candidate retrieval
   │
   ▼
Applicability + scope resolution
   │
   ▼
Claim selection
   │
   ▼
Answerability gate
   │
   ├── ANSWERED
   ├── ANSWERED_SCOPED
   ├── CONFLICTED
   ├── PARTIAL
   └── UNANSWERABLE
   │
   ▼
Answer contract
   │
   ▼
Deterministic composition
   │
   ▼
Output verification
   │
   ▼
Answer + citations + trace
```

---

## 10. Answerability

The engine does not equate "retrieved something" with "can answer."

| Status | Meaning |
|---|---|
| `ANSWERED` | Required slots are covered with a single applicable result |
| `ANSWERED_SCOPED` | Answer is covered but depends on explicit scope |
| `CONFLICTED` | Applicable same-scope claims disagree |
| `PARTIAL` | Some required information is available, some is missing |
| `UNANSWERABLE` | Required evidence is not available or entity resolution fails |

For missing information, the system states that the information was **not found in the documents searched** rather than making an unsupported claim about the real world.

---

## 11. Provenance

Every source-derived answer statement can be traced through:

```text
Answer sentence
    ↓
Claim ID
    ↓
Evidence ID
    ↓
Element
    ↓
Document + locator
    ↓
Original source
```

System-derived statements instead point to deterministic decision records such as:

- GAP
- CONFLICT
- INFERRED
- SCOPE_ASSUMPTION
- ADVISORY
- SEARCHED
- COMPLETENESS

This distinction prevents the system from pretending that a derived decision was directly written in a source.

---

## 12. Deterministic Answer Contract

The composer receives structured claims and decisions rather than unrestricted document prose.

The contract controls:

- permitted claims
- permitted values
- citations
- scope labels
- inference labels
- advisory labels
- decision records
- forbidden values

The verifier checks that:

1. cited claims exist in the contract;
2. required claims are cited;
3. numbers and units are permitted;
4. source quotes exist in the store;
5. derived sentences correspond to real decision records;
6. scope and inference labels are present where required;
7. non-answerable responses do not introduce unsupported values.

The final response is therefore **contract-verified**, meaning it passed the deterministic output contract. This is not a claim that the underlying source is necessarily true.

---

## 13. Why SQLite + FTS5

A vector database is deliberately not the core of the evaluated system.

The challenge contains exact identifiers, structured attributes, revision conditions, and engineering relationships. For these tasks, deterministic lookup and FTS5 provide:

- exact identifier handling
- predictable filtering
- explicit scope resolution
- reproducibility
- simple traceability
- no embedding-model dependency

Semantic/vector retrieval can be added as an auxiliary candidate generator without allowing similarity to decide truth or applicability.

---

## 14. LLM Boundary

The architecture allows optional LLM assistance for:

- long-tail question analysis
- prose extraction
- language normalization
- candidate generation

However:

> **The LLM cannot approve a claim.**

LLM output must pass deterministic evidence, type, scope, and contract validation. An LLM validation step may veto or demote a claim, but cannot promote an unsupported claim into the trusted knowledge base.

This makes the deterministic path the safety boundary.

---

## 15. Evaluation Results

### Official evaluation

| Metric | Result |
|---|---:|
| Questions | 23 |
| Status exact | **23 / 23** |
| Critical errors | **0** |
| Major errors | **0** |

### Held-out evaluation

| Metric | Result |
|---|---:|
| Questions | 40 |
| Status exact | **24 / 40** |
| Critical errors | **0** |
| Major errors | **10** |

The most important result is the **zero-critical-error outcome**. The held-out set still exposes recall/generalization gaps, particularly in long-tail paraphrasing and question analysis, and these are treated as known limitations rather than hidden by a confidence score.

The LLM adapter is optional and is not used to claim the deterministic evaluation results above.

---

## 16. Engineering Trade-offs

### Chosen

- SQLite + FTS5
- deterministic entity normalization
- explicit scope algebra
- evidence-backed claims
- deterministic answerability
- deterministic output verification
- traceable provenance
- reviewed raster transcription for reproducibility

### Deliberately not core dependencies

- Neo4j
- vector database
- Kubernetes
- microservices
- fine-tuning
- fuzzy identifier matching
- LLM-approved claims
- LLM-decided abstention

The design prioritizes correctness, reproducibility, explainability, and a small operational footprint over infrastructure complexity.

---

## 17. Repository Structure

```text
aegis/
├── src/aegis/
│   ├── api/
│   ├── ingest/
│   ├── models/
│   ├── query/
│   ├── store/
│   ├── llm/
│   └── config.py
├── data/
│   ├── aegis.db
│   └── transcriptions/
├── eval/
├── tests/
├── docs/
│   └── architecture.md
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

## 18. Conclusion

Aegis treats engineering knowledge as a structured, scoped, evidence-backed representation rather than a collection of retrieved text chunks.

The system's core safety property is the separation of:

```text
retrieval
    ≠
applicability
    ≠
truth
    ≠
answerability
```

Retrieval finds candidates. Deterministic reasoning decides which claims apply. Provenance shows where they came from. The answerability gate decides whether the available evidence is sufficient. The output verifier ensures that the final answer does not escape the evidence contract.

That separation is the foundation of the Aegis design.

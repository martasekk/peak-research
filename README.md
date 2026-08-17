# peak-research — Gemini CLI & Antigravity Plugin

Evidence-first deep research that produces a **citable, audit-gated artifact** — and refuses to publish one that fails its own gates.

Built for **Google Antigravity** and **Gemini CLI** (with backward compatibility for Claude Code), featuring native subagent parallel retrieval, typed claim ledgers, automated contradiction triage, and strict quality gating (G1–G5).

---

## Key Features

* **5-Phase Gated Pipeline**: Plan → Retrieve → Extract → Synthesize → Audit.
* **Public & Free Retrieval Toolkit**: Queries OpenAlex, arXiv, Crossref, Jina, GitHub, and Semantic Scholar with standard library Python (no external dependencies required).
* **Native Subagent Support**: Dispatches parallel `peak-retriever` leaf worker subagents directly in Antigravity.
* **Zero API Key Requirement**: Runs locally using Antigravity's internal model for claim typing and contradiction analysis.
* **Optional LLM Adjudication**: Supports external providers (NVIDIA NIM, OpenRouter, Groq, Anthropic, Ollama, vLLM) via standard OpenAI-compatible endpoints or Anthropic SDK.
* **Strict Quality Gates**:
  * **G1**: Retrieval health check.
  * **G3**: Live citation resolution.
  * **G5**: Comprehensive artifact structural verification.

---

## Directory Structure

```text
peak-research/
├── plugin.json                 # Antigravity plugin manifest
├── gemini-extension.json       # Gemini CLI extension manifest
├── GEMINI.md                   # Complete extension context & subagent contracts
├── README.md                   # Documentation & setup guide
├── commands/                   # TOML-based commands
│   ├── research.toml           # /research command
│   ├── retrieve.toml           # /retrieve command
│   ├── verify.toml             # /verify command (G3/G5 gates)
│   ├── doctor.toml             # /doctor command (system diagnostics)
│   └── cost.toml               # /cost command (adjudication spend & cache)
├── agents/
│   └── peak-retriever.md       # Subagent prompt and contract for leaf workers
├── rules/
│   └── peak-research.md        # Research standards & publish gating rules
├── docs/                       # Comparison docs, benchmarks, and baselines
└── skills/
    └── peak-research/
        ├── SKILL.md            # Skill declaration and runbook
        ├── CONFIG.md           # Configuration & identity specs
        ├── METHOD.md           # 5-phase / 20-step methodology
        ├── run_research.py     # Main Python pipeline orchestrator
        ├── test_gate.py        # Gate verification test suite
        ├── test_adjudicate.py  # LLM adjudication test suite
        ├── references/         # Methodology principles & source catalog
        └── tools/              # Retrieval, adjudication, and paths utilities
            ├── adjudicate.py
            ├── paths.py
            ├── retrieval.py
            └── subagent_retrieve.py
```

---

## Installation

### 1. Global Installation (Antigravity & Gemini CLI)
Copy this plugin directory into:
```text
~/.gemini/config/plugins/peak-research/
```

### 2. Project-Level Installation
Copy this plugin directory into your repository under:
```text
.agents/plugins/peak-research/
```

---

## Usage

### In Antigravity / Gemini CLI
Ask the assistant naturally:
> *"Research the current state of solid-state battery electrolytes using peak-research"*

Or execute the pipeline directly via terminal:
```bash
python skills/peak-research/run_research.py --topic "your question" --archetype literature_review --live-verify
```

### Ad-hoc Literature Retrieval
```bash
python skills/peak-research/tools/retrieval.py openalex "transformer architecture" 2023 5
python skills/peak-research/tools/retrieval.py arxiv 2303.11366
python skills/peak-research/tools/retrieval.py crossref 10.1038/s41586-021-03819-2
```

### Diagnostics & Gate Tests
```bash
python skills/peak-research/test_gate.py
python skills/peak-research/test_adjudicate.py
```

---

## Configuration

* **Polite Pool Access**: Set `OPENALEX_MAILTO` to your email to use the OpenAlex polite pool.
* **Workspace & Output Locations**:
  * Default workspace: `./.peak-research/` (run state, intermediate JSONs, cache)
  * Default output: `./research/<topic-slug>.md`
  * Override via `PEAK_WORKSPACE`, `PEAK_OUTPUT_DIR`, or `--output-dir`.

---

## License & Authors

* **Author**: Martin Hrabal (hrabal@jtjdreams.cz)
* **Version**: 2.1.4

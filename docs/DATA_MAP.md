# Data map — how the first-class objects connect

> **Being replaced.** The agreed target data model is [`TARGET_SCHEMA.md`](TARGET_SCHEMA.md). This document still describes the current code and stays accurate until each part of the target lands; new schema work must match the target.

The model rests on four first-class objects — **Persona**, **Outcome**,
**Bundle/SKU**, **Scenario** — plus two spend-source objects and a priced
catalog. This map shows every edge in the real schema (`backend/app/models.py`)
and the single rule the engine (`backend/tco_engine/engine.py`) uses to read
them. It is the schema, not an idealization; keep it in sync when the model
changes.

## How data flows: entered → derived → calculated → shown

Every number on a screen or in an export travels one path. Each layer is computed
from the one before it, on read; nothing derived is stored, so nothing derived
can go stale (TARGET_SCHEMA §1.1).

```mermaid
flowchart LR
  subgraph RAW["Entered (raw) — people write it"]
    R1["Personas · licence lines · tools<br/>scenarios · gap answers · decisions"]
    R2["Catalog & library<br/>plans · coverage · limits · install settings"]
  end
  subgraph INT["Derived (interpreted) — read-only"]
    I1["tco_engine/interpret.py<br/>prices & sources · cost shares · tool costs<br/>persona × outcome tables · retirements<br/>seat caps · findings"]
  end
  subgraph CALC["Calculated — read-only"]
    C1["tco_engine/engine.py<br/>spend · offsets · dispositions · headline"]
    C2["services/results.views<br/>totals every readout shows"]
  end
  subgraph SHOW["Shown"]
    S1["GUI (useInterpreted, useResult)"]
    S2["HTML / PDF / Excel readouts"]
    S3["Review (findings, worded)"]
    S4["Trace panel 🔎"]
  end
  R1 --> L["services/raw.py<br/>load as entered"] --> I1
  R2 --> L
  I1 --> C1 --> C2
  I1 --> S1
  I1 --> S3
  C1 --> S1
  C2 --> S1
  C2 --> S2
  I1 --> S4
  C1 --> S4
```

- **Entered:** only people write it, through the CRUD routes. A default is
  stored as NULL ("inherit"), never as the resolved value. Engagement settings
  resolve through `Engagement.effective_settings`; a tool's tooling % resolves in
  the interpreter.
- **Derived:** `GET …/interpreted`. Each rule is defined once: `interpret.py`,
  plus `rules.py` (applies-to, covers-tool, headcount shares) and `money.py`
  (managed split, cost per person), which the engine shares.
- **Calculated:** `GET …/result` is the engine's output plus `views` (bridge
  groups, consolidation per persona, quick-win holders, the new-outcome count).
  Exports and snapshots read the same result.
- **Shown:** screens format; they don't calculate. Every write refetches, so
  every screen reflects every edit. `GET …/trace/personas/{id}` walks one
  persona's figures back through the derived values to the entries, with the
  rule at each step.

## The objects

| Object | Role | Identity |
| --- | --- | --- |
| **Persona** | A group of users with a `headcount`. The anchor — nearly every edge starts here. | engagement-scoped uuid |
| **Outcome** | A capability ("Identity & SSO", "Team Chat"). The shared vocabulary both Microsoft and third-party tools are measured against. | uuid + `seed_key` |
| **Bundle / SKU** | A Microsoft licensing bundle (E3, E5, Business Premium). *Delivers* outcomes; *priced by* catalog variants. | `Bundle` |
| **Scenario** | One per persona: the target bundle (+ add-ons) they'd move to. The unit the engine sums into the number. | `PersonaScenario` |
| **Priced catalog** | A bundle's price at a segment / term / billing plan. Where a bundle becomes a dollar figure. | `MicrosoftSku` |
| **Current license** *(spend today)* | What a persona holds now. Resolves to a Bundle (its outcomes) and a paid price. | `CurrentMicrosoftLicense` |
| **Third-party tool** *(spend today)* | Non-Microsoft spend (Okta, Slack). *Delivers* outcomes; *used by* specific personas. | `ThirdPartyProduct` |

## The map

```mermaid
flowchart TD
  P(["Persona"])
  O(["Outcome"])
  S(["Scenario"])
  B(["Bundle / SKU"])
  K(["Priced catalog"])
  CL(["Current license"])
  TP(["Third-party tool"])

  P -->|requires| O
  P -->|has| S
  S -->|targets| B
  B -->|delivers| O
  B -->|priced by| K
  P -->|holds| CL
  CL -->|is a| B
  P -->|uses| TP
  TP -->|delivers| O

  linkStyle 7 stroke:#d97706,stroke-width:3px,stroke-dasharray:6 4;
```

Solid edges are relationships the model stores **and** the engine reads. Edge 7
(`Persona → uses → Third-party tool`, dashed) is stored too, but until the
persona-scoped displacement fix (ENGINE_SPEC §6.3a) the engine never walked it —
which let a tool's retirement credit leak to any persona whose target covered the
outcomes, regardless of whether that persona held the tool.

## The joins (every many-to-many is a first-class association object)

| Edge | Association object | Means |
| --- | --- | --- |
| Persona → Outcome | `PersonaRequirement` | capabilities this persona needs |
| Persona → Current license | `CurrentLicensePersona` | who holds which Microsoft license today |
| Persona → Third-party tool | `ThirdPartyPersona` | who **uses** which tool (scopes displacement, §6.3a) |
| Bundle / Tool → Outcome | `CoverageMapEntry` | what each delivers (only when `ratified`) |
| Scenario → Bundle | `ScenarioAddon` | add-ons layered on the base target (`target_sku_reference`) |
| Bundle → Priced catalog | `MicrosoftSku` | price per segment / term / billing |

No delimited strings, no shadow representations — each edge is a real row with
identity, per the data-architecture law.

## The engine's one question

Everything above exists to answer one question, per persona, per tool:

> A persona's move retires a third-party tool when the tool is **used by that
> persona** (§6.3a) **and** the persona's **target bundle delivers every outcome
> the tool delivers** (§6.6).

The engine walks:

- ✓ `Scenario → targets → Bundle → delivers → Outcome` — does the target cover it?
- ✓ `Third-party tool → delivers → Outcome` — what does the tool provide?
- ✓ `Persona → uses → Third-party tool` — does this persona hold it? *(added in §6.3a; previously skipped)*

All three gates now hold before a tool's cost is credited against a persona's move.

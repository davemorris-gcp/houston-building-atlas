# The Houston Building Atlas - AI Research Companion Setup & Pilot Guide

This guide shows Preservation Staff, historians, board members, and members of the public how to connect their preferred AI tool (**ChatGPT Plus/Web**, **Claude Web/Desktop**, or **Google Gemini**) to **The Houston Building Atlas** in under 2 minutes using their own AI account.

---

## Quick Option 0: Instant 1-Click Property Dossier Copy (Zero Setup Required)

When inspecting any building, parcel, or *Lost Houston* site in [The Houston Building Atlas](https://davemorris-gcp.github.io/houston-building-atlas/):
1. Click **`🤖 Ask AI / Copy Dossier`** inside the Property Inspector (or open **`More -> 🤖 AI Research Companion`**).
2. Click **`📋 Copy Property Dossier + Research Prompt`**.
3. Paste directly into **ChatGPT**, **Claude**, or **Google Gemini**.

This copies the property's complete pre-joined record (verified construction year and 4-tier provenance, plain-English decoded HCAD/COH permits, 22-year certified ownership chain, HPO Historic District survey status, 1866-1926 City Directory appearance, TARO 2.0 / HPL archival box/folder citations, *Lost Houston* context, and verified deep links) straight into your chat window.

---

## Option 1: ChatGPT Plus / Web (Project or Custom GPT)

1. Open [ChatGPT](https://chatgpt.com/) and create a new **Project** (or **Custom GPT**) named **`Houston Building Atlas Research Companion`**.
2. Download and attach these two files from the Atlas **`🤖 AI Research Companion`** modal (or from `https://davemorris-gcp.github.io/houston-building-atlas/`):
   - [`llms-full.txt`](https://davemorris-gcp.github.io/houston-building-atlas/llms-full.txt) (Complete pre-indexed catalog of 623 *Lost Houston* landmarks, 501 standing COH Landmarks, unprotected NRHP/cultural landmark candidates, 95+ Architect/Style portfolios, 23 Historic District surveys, 11 Council District scorecards, and 34 Watchlist dossiers)
   - [`query_atlas.py`](https://davemorris-gcp.github.io/houston-building-atlas/public/ai-companion/query_atlas.py) (Python lookup helper for Code Interpreter / Data Analysis)
3. Paste the **System Instructions** from [`SKILL.md`](https://davemorris-gcp.github.io/houston-building-atlas/public/ai-companion/SKILL.md) (or click **`📋 Copy ChatGPT Instructions`** inside the Atlas `#aiCompanion=1` modal) into the Project Instructions box.

---

## Option 2: Claude Web / Claude Desktop (Project or Skill)

1. Open [Claude](https://claude.ai/) and create a **Project** named **`Houston Building Atlas`** (or install `skills/houston-building-atlas/` in Claude Desktop / Claude Code).
2. Upload [`llms-full.txt`](https://davemorris-gcp.github.io/houston-building-atlas/llms-full.txt), [`SKILL.md`](https://davemorris-gcp.github.io/houston-building-atlas/public/ai-companion/SKILL.md), and [`query_atlas.py`](https://davemorris-gcp.github.io/houston-building-atlas/public/ai-companion/query_atlas.py) to **Project Knowledge**.
3. Paste the **Claude Project Instructions** (available via 1-click copy in the Atlas `#aiCompanion=claude` modal).

---

## Option 3: Google Gemini (Gem or Chat Session)

1. Open [Google Gemini](https://gemini.google.com/) and create a new **Gem** named **`Houston Building Atlas Historian`** (or start a standard chat).
2. Attach [`llms-full.txt`](https://davemorris-gcp.github.io/houston-building-atlas/llms-full.txt) and [`query_atlas.py`](https://davemorris-gcp.github.io/houston-building-atlas/public/ai-companion/query_atlas.py) to the Gem Knowledge files.
3. Paste the **Gemini Gem Instructions** (available via 1-click copy in the Atlas `#aiCompanion=gemini` modal).

---

## 6 Real Houston Preservation Test Prompts to Try Right Away

1. **Demolished Landmark & Architect Portfolio Check (`Kirby Mansion & James Ruskin Bailey`):**
   > *"What happened to the John Henry Kirby Mansion at 2000 Smith St, what permits and asbestos notifications preceded its loss, and what other buildings by its remodel architect James Ruskin Bailey are still standing in Houston? Include clickable links to the Houston Building Atlas."*
2. **Unprotected Cultural & Architectural Landmark Nominations (`Granada Theater, LULAC Council 60 & TBH`):**
   > *"We are compiling high-priority unprotected landmark candidates for HAHC. Give me the HCAD account numbers, construction dates, architectural significance, and 1-click printable THC Historic Resources Survey Form links for Granada Theater (3700 N Main St), LULAC Council 60 (3004 Bagby St), and Talento Bilingue de Houston (333 S Jensen Dr)."*
3. **Statutory Protection Nuance (`Freedmen's Town Heritage District vs. COH Historic Districts`):**
   > *"Does the City of Houston's Freedmen's Town Heritage District protect private historic houses from demolition? Compare it with a City of Houston Historic District like Old Sixth Ward and share the Atlas scorecard links."*
4. **134-Year Stewardship & Deed Chain (`1318 Andrews St`):**
   > *"Trace the historical ownership and resident chain for 1318 Andrews St (HCAD 0090790000009) in Fourth Ward / Freedmen's Town, and give me the Atlas History Tab link."*
5. **Plain-English Permit & Appraiser Shorthand Translation:**
   > *"A Houston property record shows a COH Type 12 permit for 'BLD DEMO/SEWER DISC' under project #26090093 and an HCAD appraiser note reading '4/1/26 NC ... CDU @ GD'. Translate both into plain English for a homeowner or journalist."*
6. **Council District Preservation & Demolition Comparison (`District C vs. Citywide`):**
   > *"Compare pre-1940 historic building stock, protected building counts, and 2020-2026 demolitions in Houston City Council District C against the citywide totals, with links to both Atlas Scorecards."*

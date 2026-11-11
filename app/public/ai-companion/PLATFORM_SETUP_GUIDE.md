# The Houston Building Atlas - AI Research Companion Setup & Pilot Guide

This guide shows Preservation Staff, historians, board members, and members of the public how to connect their preferred AI tool (**ChatGPT Plus/Web**, **Claude Web/Desktop**, or **Google Gemini**) to **The Houston Building Atlas** in under 2 minutes using their own AI account.

---

## Quick Option 0: Zero-Setup 1-Click Launch (`Ask Claude ↗` & `Ask ChatGPT ↗`)

You do not need to install a Skill or create a Project to use the AI Research Companion right away:
1. Open [The Houston Building Atlas AI Research Companion](https://davemorris-gcp.github.io/houston-building-atlas/#aiCompanion=claude) (or inspect any building/parcel on the map and click **`🤖 Ask AI / Copy Dossier`**).
2. Click **`🚀 Ask Claude (1-Click) ↗`** or **`🚀 Ask ChatGPT (1-Click) ↗`** on the Property Dossier or on any of the 6 Sample Questions.
3. Claude (`claude.ai/new?q=...`) or ChatGPT (`chatgpt.com/?q=...`) opens in a new tab with the Atlas context and question already filled into the prompt box (and the full dossier copied to your clipboard). Just press **`Enter`**.

---

## Option 1: Claude Web / Claude Desktop (1-File `.zip` Skill Upload or Project)

### Fast 1-File Skill Upload (`Settings -> Capabilities -> Skills`)
1. Download the single packaged Skill archive: [`houston-building-atlas-skill.zip`](https://davemorris-gcp.github.io/houston-building-atlas/public/ai-companion/houston-building-atlas-skill.zip).
2. In [Claude](https://claude.ai/) or Claude Desktop, open **Settings -> Capabilities -> Skills** (or **Customize -> Skills**), click **`+ Upload a skill`**, and select `houston-building-atlas-skill.zip`.
3. Toggle the skill **On**. Claude now automatically knows the Houston Building Atlas schema, 7 Golden Rules, and lookup script across all your chats.

### Alternative: Claude Project (Offline Full Corpus)
- If you want the entire `442 KB` offline table of 1,622 landmark/historic properties and 623 *Lost Houston* structures pinned inside a dedicated Claude Project, create a Project named **`Houston Building Atlas`** and attach [`llms-full.txt`](https://davemorris-gcp.github.io/houston-building-atlas/llms-full.txt) to Project Knowledge.

---

## Option 2: ChatGPT Plus / Web (1-Click Launch, Project, or Custom GPT)

1. **Zero-Setup 1-Click Launch:** Click **`🚀 Ask ChatGPT (1-Click) ↗`** inside the [Atlas AI Companion modal](https://davemorris-gcp.github.io/houston-building-atlas/#aiCompanion=chatgpt) to open `chatgpt.com` with the Atlas `/llms.txt` index and your question pre-loaded in the chat input.
2. **Reusable Project / Custom GPT:** Create a Project (or Custom GPT) named **`Houston Building Atlas Research Companion`**, attach [`llms-full.txt`](https://davemorris-gcp.github.io/houston-building-atlas/llms-full.txt) and [`query_atlas.py`](https://davemorris-gcp.github.io/houston-building-atlas/public/ai-companion/query_atlas.py), and paste the instructions from the `#aiCompanion=chatgpt` modal.

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

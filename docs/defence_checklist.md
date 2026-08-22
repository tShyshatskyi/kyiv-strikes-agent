# Defence checklist

Target 10–15 minutes. Rehearse once against a timer before recording.

The nine required demonstration points are mapped to segments below; the number
in brackets is the requirement from the assignment.

---

## Before you start recording

- [ ] `.env` filled in and a model actually answering
- [ ] `npx playwright install chromium` done
- [ ] `python -m pytest -q` green — 142 tests
- [ ] `python scripts/probe_playwright.py` run **today**: news sites change, and
      a source that worked last week may be walled today
- [ ] a known-removed article URL ready for the failure scenario
- [ ] `fixtures/articles/` served on `python -m http.server 8899` as a fallback
      if the network fails mid-recording
- [ ] two terminals open, side by side, both showing the repository root

---

## 1. Independent startup and architecture — 2 min

- [ ] **Terminal 1**, on its own, nothing else running: `[1]`

  ```bash
  PYTHONUTF8=1 KSM_OFFLINE=1 NOMINATIM_CONTACT="you@example.com" python -m mcp_server
  ```

  Say: this is a separate OS process. It has no model credentials and does not
  need any.

- [ ] **Terminal 2**: `python -m agent "удар по Києву 17 червня 2026"` `[2]`

  The first two lines print both tool lists: 24 Playwright tools, 4 custom
  ones. That is discovery of both connections, and evidence that at least three
  custom tools are exposed. `[7]`

- [ ] Point at the diagram in `README.md`: two MCP servers, two processes, one
      agent. The custom server never receives article text.

---

## 2. Playwright MCP inside the flow — 2–3 min

- [ ] Show `browser_navigate` opening a search page and then an article. `[3]`

- [ ] Explain its contract from `docs/tool_contracts.md`: one required `url`,
      an empty success result, and — the interesting part — a page behind a bot
      wall is a *successful* navigation returning HTTP 403, so it has to be
      detected by reading the page, not by catching an error. `[5]`

- [ ] Show that its result changes the route: which outlets came back decides
      the independence assessment, which decides the verification level, which
      decides whether the graph searches again. `[4]`

  Have `agent/graph.py` open at `add_conditional_edges` while saying this.

---

## 3. The custom server end to end — 3–4 min

- [ ] Let one full run finish. Walk the printed report top to bottom: rounds
      used, articles read, location and confidence, evidence level, per-field
      values with their status, what is still missing. `[6]`

- [ ] Show the row in the database:

  ```bash
  sqlite3 incidents.sqlite "SELECT id, district, killed, verification_level FROM incidents"
  ```

- [ ] Explain one contract properly — use `corroborate_incident_claims`: `[8]`

  > A casualty count is not a fact stated once, it is completed over days. So a
  > later higher figure supersedes rather than contradicts. A real contradiction
  > is two sources disagreeing at the same time, when neither could know more.
  > And the tool does not just score the evidence — it returns
  > `missing_for_next_level`, which is what the graph's one decision reads.

---

## 4. Failure and offline mode — 2 min

```bash
python scripts/demo_failures.py
```

- [ ] **Scenario C** — two calls, side by side: an Odesa address comes back
      `ok:false` with `OUT_OF_AREA`; a nonexistent street comes back `ok:true`
      with an empty payload. Say why that difference matters: the agent must be
      able to tell "keep looking" from "something is wrong".

- [ ] **Scenario B** — the config points at a command that does not exist; one
      readable sentence naming the server and what to check, no traceback. `[9]`

- [ ] **Scenario A** (the strongest one, do it live if time allows) — point one
      article link at a removed page. The URL appears under `Unreachable`, the
      run finishes on the remaining sources, and the evidence level of the
      written record drops. The failure changes the outcome rather than merely
      being caught.

- [ ] Show `KSM_OFFLINE=1` giving the same answer through the same code path.
      Stress that the fixture replays the *transport*: the recorded Odesa
      response still has to fail point-in-polygon to produce `OUT_OF_AREA`, and
      the error message contains coordinates that were geometrically tested.

---

## 5. Variation, invalid input, and tracing — 3–4 min

For a recorded defence all three are mandatory; live, expect to be asked.

- [ ] **A different valid input**: another date. Or change one configuration
      value — `relevance_window_days` from 14 to 30 admits later summaries.

- [ ] **An invalid input**: `location_text` of one character. Explain that
      validation is two-layered — the published schema declares `minLength: 3`,
      so MCP refuses it before our code runs; semantic problems that pass the
      schema, like `"17 червня"` where a date is expected, come back as our own
      `SCHEMA_VIOLATION` envelope.

- [ ] **Trace one value from source to output**:

  ```bash
  sqlite3 incidents.sqlite "SELECT i.killed, s.url, c.quote FROM incidents i JOIN incident_claims c ON c.incident_id=i.id AND c.field='killed' JOIN sources s ON s.id=c.source_id"
  ```

  Number in the database → the article it came from → the sentence in that
  article that says it. Then open that article and show the sentence.

- [ ] If a record was flagged, show the human end of the loop:

  ```bash
  python -m agent.review
  python -m agent.review ok 1 --by taras
  python -m agent.review export | head -20
  ```

  Before sign-off the incident is missing from the verified export; after, it is
  there. Say why: a confidently misplaced point is a false claim about where a
  strike landed, so doubt is recorded rather than resolved by guessing.

---

## Questions to have an answer ready for

- **Why is the extraction not an MCP tool?** Its contract would be
  `str -> dict`, which says nothing about the domain and would be identical in
  any project. Keeping it in the agent also keeps the server deterministic and
  credential-free.
- **Where does `confidence` come from?** Not from Nominatim's `importance` —
  that is a Wikipedia-derived fame score, and an ordinary address scores 4,500×
  lower than a museum at the same number. Confidence measures how precisely the
  address matched.
- **When would `OUT_OF_AREA` ever fire?** Whenever the geocoder returns a real
  place outside Kyiv. The Kyiv viewbox biases ranking but deliberately does not
  bound the search, precisely so that the district check can actually reject
  something.
- **Which tool has side effects?** Only `upsert_incident`. It writes four
  tables.
- **Why are ДСНС and КМВА in the registry if you never fetch them?** For tiering
  and grouping. An outlet citing them is grouped under them, so their voice
  still counts even though their site cannot be read.

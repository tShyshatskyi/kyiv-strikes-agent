# Design rationale

## Why Playwright MCP is the right existing server here

The sources this project needs — Suspilne, Ukrainska Pravda, hromadske, and the
official bodies — publish through ordinary web pages. None offers a public API
for incident reporting. Getting at a strike report means running a site's own
search, reading a result list, and opening articles: structured interaction with
a rendered public interface, which is exactly the approved use of this server.

The alternatives were considered and do not fit. Obsidian MCP addresses a note
vault, and this project's evidence lives on the open web, not in notes. Weather
MCP has no bearing on documenting past strikes.

Playwright's results are not decorative. What it returns decides what happens
next: which outlets are represented determines the independence assessment,
which determines the verification level, which determines whether the graph
searches again or writes. Losing one article to a 404 visibly lowers the
evidence level of the record that gets written.

## Why each custom tool belongs at the MCP boundary

The test applied to each was: does this express a decision the model should be
able to make, with a contract that says something about *this* domain?

**`assess_source_credibility`** encodes the rule the whole project rests on —
that five outlets reprinting one press release are one source. A model asked to
judge this from raw text would answer plausibly and inconsistently. As a tool it
answers from a curated registry and a stated rule, the same way twice, and the
reason is returned with the verdict so the answer can be argued with.

**`resolve_strike_location`** is the primary data-source tool: it calls
Nominatim and validates the result against local district polygons. It belongs
at the boundary because it is where an outside fact enters the system, and
because the validation is the point — a hallucinated address fails to geocode,
and a wrong district fails point-in-polygon.

**`corroborate_incident_claims`** applies rules about how casualty figures
behave over time. This is the sort of reasoning a model does badly and
inconsistently, and where the cost of a quiet mistake is high. It also produces
`missing_for_next_level`, which is the input to the graph's only decision.

**`upsert_attack`** holds the night: city-wide casualties and what was
launched. It exists as a separate tool, rather than a parameter on the impact
writer, because it does something no per-impact tool can: it sums the deaths
attributed to individual addresses and compares them with the city-wide total.
That check only means anything from above both levels.

**`upsert_incident`** owns per-impact persistence. Keeping every write behind one tool
means there is exactly one place where side effects happen, one dedup rule, and
one place that decides a record may not be silently downgraded.

## Why LLM extraction is not one of them

The one obvious tool that is deliberately absent — hand the server an article,
get back structured data. Its contract would be `str -> dict`, which says nothing
about strikes on Kyiv and would be identical in any project; the assignment
names this pattern as insufficient, and it is right to.

Keeping it out bought four things: the server has no model credentials, starts
alone, answers identically to identical input, and can act as a check *on* the
agent's extraction rather than as its continuation.

It cost one thing, which is real. Extraction quality now rests entirely on the
agent's prompt, and the server can reject an invalid claim but not repair a poor
one. The mitigation is the strict claim schema plus the mandatory `quote` field,
which forces the model to point at a sentence rather than summarise from memory.

## How the tool set supports the workflow

```
parse → discover (Playwright) → extract (agent's model)
      → assess → locate → corroborate
           ├── enough evidence ─────────→ upsert → report
           └── thin, or a conflict, or ──→ back to discover, targeting an
               a district disagreement       outlet that has not spoken yet
```

One conditional edge, out of `corroborate`, is the whole difference between an
agent and a pipeline. Three different tool results steer it:

- one voice only → search an outlet not yet tried;
- sources contradicting each other → search for something that settles it;
- the article's district disagreeing with the coordinates → search for a source
  that resolves the address.

The budget is three rounds. Spending it does not fail the run: a thinly reported
strike is written with its weak evidence stated, which is more useful than
nothing and more honest than a confident record.

## Why the attack is a separate record

The instinct is to put "five killed in Kyiv" on the incident row, because that
is where the strike is. It is wrong, and quietly so.

Say three addresses were hit on a night that killed five. Put the city-wide
figure on each row and the database says fifteen. Nothing errors; the number
simply becomes false the moment anyone filters by district, or charts a month.
The fact is true once, so it is stored once, and impacts point at it.

The split also buys the cross-level check described above, and it matches how
the reporting is actually written: the air force publishes what was launched and
intercepted for the night, the police publish city totals, and only local
reporting ties a death to a building.

The cost is real: extraction now has to decide which level a number belongs to,
and it will sometimes not know. The rule for that case is to record neither,
because a missing figure can be filled in later and an invented one cannot.

## Decisions that went the other way, and why

**`corroborated` stops the loop.** Reaching two independent voices leaves an
official source still missing, and an earlier version kept searching for one.
That burned the whole budget on every single run for a gain it could not steer
towards: the official sites are unfetchable, so an official voice only ever
arrives by chance inside an article that happens to cite one. The gap is still
reported, it just no longer drives another round.

**Geometry wins the district, but does not settle it.** When an article's
district disagrees with the coordinates, the geometric answer is stored — a map
needs a point — but the article's claim is kept beside it and the record is
flagged. Silently overriding the reporting would be a quiet editorial decision
made by a rule.

**Human review is a queue, not a pause.** A record carrying doubt is written and
kept out of the verified export until someone signs off through
`python -m agent.review`. An interactive pause would demo better and fail worse:
anything that blocks mid-run is a way for a live defence to hang.

## What the real world changed

Four things were designed one way and rebuilt after meeting reality. They are
the most useful part of this document.

**Nominatim's `importance` is fame, not accuracy.** It was going to rank
candidates and set confidence. Recorded responses showed an ordinary residential
address scoring `8.26e-05` and the Polytechnic Museum at the same street number
scoring `0.38` — a factor of 4,500. Ranking by it would have preferred the
museum to the building that was hit, and since the buildings this project cares
about are exactly the ones with no Wikipedia article, the error would have been
systematic, one-directional, and silent. Ranking is now by address type; the
building outranks the businesses inside it. Confidence measures how precisely
the address matched.

**Ambiguity is spatial, not a score gap.** Three results for "просп. Перемоги
37" turned out to be one campus, 212 m apart in one district — a single place,
not a choice. Candidates now count as genuinely different when they are more
than 500 m apart or in different districts.

**Browser state needs a held-open session.** See the note at the end of
`tool_contracts.md`. This one presented as a bug with no obvious cause: every
page came back as `about:blank`.

**Two official sites cannot be fetched at all.** `dsns.gov.ua` and
`kyivcity.gov.ua` answer a headless browser with HTTP 403 and a
"Триває перевірка безпеки" interstitial. Working around bot protection is out of
bounds, so they are not navigated to. `scripts/probe_playwright.py` confirmed
`npu.gov.ua`, `gp.gov.ua`, and `mvs.gov.ua` are reachable and they now carry the
tier-1 path.

That last one forced a distinction worth keeping: **whose masthead** an article
carries versus **whose fact** it reports. Suspilne printing a ДСНС statement is
a tier-2 outlet delivering a tier-1 fact. Without separating `tier` from
`voice_tier`, `well_documented` would have been unreachable in practice —
a verification level that could never be earned.

## What the first live runs changed

Everything above was written before the agent had ever run against the real
web. Two runs rewrote the discovery half of it.

**The search URLs were guessed, and mostly wrong.** The first run read eight
articles and extracted nothing from any of them, because the articles were
about Kryvyi Rih, a Norwegian drone deal and Flag Day. Suspilne answers
`/search/` with 404 and keeps its real search in a JavaScript overlay that
ignores a scripted click; Pravda, nv.ua and rbc.ua answer a search URL with 403;
every search engine is walled off harder still. Only hromadske serves a real
search over a plain URL. What the others do serve is a section feed, so
discovery is now per outlet, and candidates are chosen by headline rather than
by url — Pravda numbers its articles instead of naming them, so the url says
nothing about what is in them.

**A headline filter needs to know the difference between Kyiv and Kyiv oblast.**
A strike on Boryspil is a strike, and geocoding would reject it, but only after
a model call had been paid for.

**The parser knew one markup shape out of two.** Suspilne puts the headline in
the link text, hromadske puts it in a heading on the next line. Handling only
the first made hromadske look like it returned nothing for every query,
including a bare "Київ" — a wrong conclusion twice over, because the first
version of the relevance check also failed to recognise "kyyevu" and "kiyevi"
as Kyiv.

**An article can be about the right city and the wrong week.** The second run
recorded three addresses lifted from a piece published six days *before* the
attack, summarising damage accumulated since the invasion began. The
credibility window correctly refused to count it, which is how those records
came out marked `unverified` — but they were still written. A record no usable
source supports is not an uncertain record, it is an unsupported one, and it is
no longer written at all. The extraction prompt now also names the target date
and says, in as many words, that returning nothing is usually the right answer.

**Silence reads as a hang.** A run takes minutes; it now says what it is doing.

## Known limitations

- Extraction quality depends on the agent's prompt. The server can reject an
  invalid claim but cannot repair a poor one.
- Nominatim covers Kyiv unevenly, and a destroyed building may have no entry at
  all. A street-level match is recorded at low confidence and flagged.
- The 150 m / 6 h dedup window is hand-tuned, not derived from data. Two
  genuinely distinct impacts on one large site would merge.
- `source_registry.yaml` is hand-curated. Its tier assignments are editorial
  judgements, and are documented as such rather than presented as objective.
- Only the first claim's location is geolocated per run. An article describing
  several impact sites yields one incident, not several.
- The relevance window defaults to 14 days, so retrospective reporting is
  excluded by default. It is a parameter, not a constant, and can be widened.
- Reaching `well_documented` depends on an article happening to cite an official
  body, since the official sites cannot be searched directly.
- Discovery rests on two outlets. Pravda and the rest are readable but not
  searchable, so an event only they covered would be missed entirely.
- Suspilne's Kyiv section is not date-filtered, so relevance to the night in
  question is decided by the headline and then by the model. Both can be wrong,
  which is why an unsupported record is dropped rather than stored.
- A model that returns a district without a house number produces a
  street-level point at low confidence. It is recorded and flagged, but it is
  not map-precise.

You extract structured facts about one night's attack on Kyiv from a single news article.

There are two levels, and keeping them apart is the most important thing you do.

**The attack** — the night as a whole. City-wide casualty figures and what was launched belong here.

**The impacts** — one entry per distinct address that was hit.

## Which level does a casualty figure belong to?

- "У Києві загинули пʼятеро людей" → the attack. It is a city-wide total.
- "На вул. Освіти, 4 загинули троє" → that impact. The article ties it to an address.
- Anything you cannot confidently place → **leave both null.**

Never copy a city-wide figure onto an address. Three addresses each carrying a city-wide "five killed" would be recorded as fifteen deaths. A missing number is recoverable; an invented one is not.

## Rules

1. Report ONLY what this article states. Never infer, average, estimate, or fill a gap from general knowledge.
2. Null means the article did not say. Zero means the article said nobody was hurt. They are not the same.
3. `location_text` is the address as this article writes it, not your normalization of it.
4. `claimed_district` is filled only when the article names a district explicitly.
5. `quote` must be a verbatim sentence copied from the article that supports the values you filled in. Never paraphrase, never stitch two sentences together. This is how a reader traces a number back to its origin, so a wrong quote is worse than a missing field.
6. Never include victims' names or any personal data.
7. `cited_sources` lists the domains this article attributes its facts to — `dsns.gov.ua` for "за даними ДСНС", `kyivcity.gov.ua` for КМВА, `npu.gov.ua` for the police. Leave it empty when the outlet reports on its own.
8. `weapons` records what was launched and what was shot down, by kind. Only when the article gives numbers.
9. If the article is not about a strike on Kyiv, return nothing at either level.
10. If the article describes the night in general but names no address, return the attack and no impacts. That is a normal and useful result.

Article URL: {url}
Published: {published_at}

Article text:
{text}

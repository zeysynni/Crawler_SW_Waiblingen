# FAQ-Bot test set

This folder holds the **evaluation data** for the FAQ bot: the colleagues' manual
test log (`SW_Waiblingen_FAQ_Testing.xlsx`) and the machine-readable test set
derived from it (`tests.jsonl`). Nothing here is part of the crawler — the
crawler produces the knowledge base, this folder measures whether a bot built on
top of it answers correctly.

Written 2026-08-29. Companion to `PDFs/README.md` and `Excels/README.md`, which
document the two source converters in the same style.

| File | What it is |
|---|---|
| `SW_Waiblingen_FAQ_Testing.xlsx` | The colleagues' manual test log — 82 questions asked against the live bot, with a verdict and remarks |
| `tests.jsonl` | The test set: one JSON object per line, 82 lines, questions verbatim from the Excel + grounded reference answers |
| `tests_tutorial_insurellm.jsonl.bak` | The previous content of `tests.jsonl` — 150 lines of *Insurellm* tutorial data, kept only so it is not lost |

---

## 1. What was done

`tests.jsonl` originally contained the LLM-course tutorial's fictional insurance
company (Insurellm). It was replaced with the real Stadtwerke Waiblingen test
questions so that retrieval quality can be measured against the actual knowledge
base.

Source of each field:

| Field | Where it comes from |
|---|---|
| `question` | The Excel column `Testsatz`, **verbatim** |
| `tester`, `tester_result` | Excel columns `Tester` and `Testergebnis` |
| `note` | Excel columns `Bemerkungen` / `Wo ist die Antwort zu finden`, condensed |
| `reference_answer` | Written by hand from `outputs/clean/*.md` and the updated `Excels/Knowledge Base.xlsx` |
| `keywords`, `category`, `source`, `answerable`, `behaviour_test` | Derived while writing the answer |

Line format:

```json
{"id": 1,
 "question": "wann ist die Hallenbadsaison?",
 "keywords": ["Hallenbadsaison", "16. September", "30. April"],
 "reference_answer": "Die Hallenbadsaison dauert vom 16. September bis zum 30. April. …",
 "category": "temporal",
 "source": ["Privatkunden_Baeder"],
 "answerable": true,
 "behaviour_test": false,
 "tester": "Zeyuan",
 "tester_result": "success",
 "note": ""}
```

### Field reference

- **`question`** — byte-identical to the Excel, including typos.
- **`keywords`** — 2–5 terms a correct answer must contain. Cheap first metric;
  not a substitute for judging the answer.
- **`reference_answer`** — German, grounded. Where the knowledge base has no
  answer it states the *correct behaviour* instead (admit the gap, name the right
  phone number) rather than an invented fact.
- **`category`** — what *kind* of question it is, and therefore how hard it is to
  retrieve. Eight values: the seven standard RAG-evaluation types
  (`direct_fact`, `temporal`, `spanning`, `comparative`, `numerical`,
  `relationship`, `holistic`) plus `procedural`. See §2.6 for why the eighth
  exists and why `holistic` never occurs.
- **`source`** — the `outputs/clean/` file(s) the answer comes from, so
  **retrieval** can be scored separately from **generation**. A value of
  `"Knowledge Base.xlsx: <Kategorie>"` means the text exists only in the updated
  Excel and is *not yet* in the corpus — see §3.
- **`answerable`** — `false` when no source in the KB supports the answer. This
  is a *separate axis* from `category`: a question can be `numerical` **and**
  unanswerable (id 36, the number of pools).
- **`behaviour_test`** — `true` for the five rows that test tone and handling
  rather than knowledge. Exclude them from any retrieval score.
- **`tester_result`** — the colleagues' verdict on the **old** bot: a baseline to
  beat, not ground truth about the content.
- **`note`** — what the colleague expected instead. The most valuable column in
  the Excel; 37 of 82 rows have one.

### Composition

| Category | n | Retrieval needs | | Other axes | n |
|---|---|---|---|---|---|
| `procedural` | 31 | a whole procedure, usually one section | | success / suggestion / failure | 54 / 14 / 14 |
| `direct_fact` | 21 | one sentence | | `answerable` true / false | 70 / 12 |
| `spanning` | 11 | several parts of one page | | `behaviour_test` true | 5 |
| `temporal` | 9 | one sentence | | | |
| `numerical` | 6 | one table row | | | |
| `comparative` | 2 | two facts, usually adjacent | | | |
| `relationship` | 2 | two facts in different places | | | |
| `holistic` | **0** | many documents at once | | | |

Testers: Ines 71, Bettina 8, Nicklas 2, Zeyuan 1.
**16 of the 82 questions need more than one source file** — the hardest
retrieval cases regardless of their label.

---

## 2. Decisions, and why

### 2.1 Questions are verbatim, typos included

`unterschief`, `Wäremstrom`, `funktionert`, `Bezugsseite` (for *Bezugsstelle*),
`eAnwälte` (for the law firm *E-Rechtsanwälte*) are all kept. Real users type
like this, and one colleague explicitly praised the bot for handling
`Wäremstrom`. Silently correcting them would delete a real test. A checked
comparison against the Excel is part of the regeneration procedure (§5).

### 2.2 No answer was invented

The requirement was reference answers grounded in the knowledge base. Where the
KB says nothing, an answer was **not** made up — `answerable` is set to
`false` and the reference answer describes the correct behaviour. Inventing
plausible-sounding facts about tariffs and opening hours is exactly the failure
mode `PDFs/README.md` §2.1 warns about (`13,80€` becoming `1380€`): it looks fine
and is wrong. A test set that contains invented facts silently teaches the bot to
be wrong.

### 2.3 `source` was added so retrieval can be scored separately

A wrong answer has two very different causes: the text was missing, or the text
was there and was not retrieved. Without a source field the two look identical in
the results. With it, retrieval accuracy (did the right file come back?) can be
measured independently of answer quality — and §3 shows the distinction is not
academic here.

### 2.4 Tone tests are flagged, not deleted

Five rows (75, 77, 78, 81, 82) are barely questions: an angry customer demanding
the management, a complaint letter with a lawyer threat, a multi-part request.
They test tone, not knowledge, and no retrieval metric applies to them. They are
kept because they document real requirements — *do not interrupt a long
utterance*, *do not send a caller to the phone number they are already calling* —
and carry `behaviour_test: true` so they can be filtered out of retrieval
scoring.

### 2.5 The old tutorial file was kept

`tests_tutorial_insurellm.jsonl.bak` is the Insurellm data. It is worth nothing
for this project, but it is what the course material uses; deleting it outright
would have made the course's own notebooks unrunnable without a re-download.

### 2.6 The category set: seven standard types plus `procedural`

`tests.jsonl` uses the seven RAG-evaluation question types that the course
material uses — `direct_fact`, `temporal`, `spanning`, `comparative`,
`numerical`, `relationship`, `holistic` — plus one addition.

**Why adopt the standard seven.** They classify questions by *how hard they are
to retrieve*, which is the property this project is actually designing for. That
makes the distribution a specification for chunking (§2.7), and it keeps results
comparable with other RAG work instead of using a private vocabulary.

**Why `procedural` was added.** It is the largest group here: **31 of 82**.
"What must I do to change my bank details / report a meter reading / cancel a
tariff change" fits none of the seven. That is not a defect in the standard list;
it reflects what the corpus is. The tutorial's list was built for a company's
product and personnel documents, which are mostly *facts*. A utility's customer
FAQ is mostly *processes*. Deleting `procedural` would push 31 questions into
`direct_fact` and hide the single most characteristic property of this data.

**Why `holistic` has zero rows.** Not an oversight — there is genuinely no
holistic question in the set. The questions come from colleagues phoning the bot
as customers would, and real callers ask narrow things ("what does a ticket
cost"), never "compare all your baths" or "what subjects do you cover". This is
a **gap in the test set, not in the labelling**: holistic questions would have to
be written by hand, they will never arrive from the test log.

Two judgement calls worth knowing: ids 9 ("was habt ihr für stromtarife?") and 30
("was für Bäder gibt es") *ask* for a complete enumeration and look holistic, but
one page happens to list everything, so they are labelled `spanning`. If the site
ever splits those overviews, they become genuinely holistic.

**An earlier version of this file mixed two dimensions into `category`** — it had
`out_of_scope` and `behaviour` as if they were question types. They are not: a
question can be `numerical` *and* unanswerable (id 36). They were split out into
the `answerable` and `behaviour_test` fields.

### 2.7 What the distribution says about chunk size

The category mix is evidence for the open chunking question, so it is worth
reading as such:

| Signal | Count | What it argues for |
|---|---|---|
| `spanning` | 11 | keeping a page whole — the answer is scattered across it |
| needs >1 source file | 16 | retrieving several chunks, and generous `k` |
| `relationship` | 2 | the same |
| `direct_fact` + `numerical` + `temporal` | 36 | smaller chunks would be sharper |
| `holistic` | 0 | nothing here demands whole-corpus reasoning |

So the corpus is split roughly evenly between "one small fact" and "needs
context", with nothing requiring the whole corpus at once. That supports keeping
the crawler's existing *one page = one chunk* granularity as the starting point,
and reaching for smaller chunks with a title prefix only if the 36 fact-style
questions retrieve badly. It is also a warning: pages vary from 229 to 18,175
characters, so a single 18k page as one vector will serve those 36 questions
poorly no matter what.

---

## 3. What the test set revealed

Writing the answers meant reading every question against the corpus. Three
findings came out of it that matter more than the file itself.

### 3.1 Twelve questions have no answer in the knowledge base

| id | Question | Gap |
|---|---|---|
| 4, 7 | Was ist ein Eintarifzähler / Unterschied zum Zweitarifzähler | No definition anywhere |
| 27 | Wann haben die Kassen auf bei den Freibädern | Only bathing hours are documented |
| 36 | Wie viele Becken gibt es im Hallenbad Waiblingen | Sub-page not crawled |
| 39, 40 | Parken / Rauchen im Freibad Bittenfeld | Sub-page not crawled |
| 79 | E-Mail-Adresse ändern | Excel covers name and postal address only |
| 80 | Fälschlich eingeleiteter Lieferantenwechsel | Only the reverse case is covered |
| 75, 77, 78, 82 | Complaint / escalation handling | Not a knowledge question (`behaviour_test`) |

**Four of them (7, 36, 39, 40) were marked `success` by the testers.** If the bot
answered "how many pools does the Hallenbad have", that answer did not come from
this knowledge base. Worth knowing before trusting the 54 successes as a baseline.

Three of the gaps are cheap to close: `Privatkunden/Baeder` is crawled but its
sub-pages *Hallenbad Waiblingen*, *Freibad Waiblingen* and *Waldfreibad
Bittenfeld* are not listed in `sites/waiblingen.yaml`. Adding those three labels
would likely answer 27, 36, 39 and 40. The rest (Eintarifzähler, e-mail change)
belong in the Excel knowledge base.

### 3.2 Thirteen answers exist only in the *updated* Excel

The updated `Excels/Knowledge Base.xlsx` (33 rows, uncommitted at the time of
writing) adds five whole groups that the committed 18-row version does not have:

```
Guthaben    Abschaltung    Inkasso    Mahnung    Ratenzahlung
```

Rows 65–74 and 78 depend on them, and rows 37 and 42 on two new Bäder entries
(Badeschluss 19:40, no reduction for pensioners). Their `source` therefore reads
`Knowledge Base.xlsx: …`, not a file name.

**Until `Excels/xlsx2md.py` is re-run and the pages re-uploaded, those 13
questions cannot be answered by any bot, however good the retrieval.** This is
what the testers meant by the repeated note *"To Do: bitte die Excel neu
einspielen :)"*.

### 3.3 Most failures were retrieval, not missing knowledge

Of the 14 `failure` rows, 13 have the answer available somewhere:

- **5** where the text is already in the current corpus — ids 26, 28, 33, 43, 53.
  Freibad opening hours, entrance prices, reductions, gas outside the supply area,
  PV billing period. The content sits in `outputs/clean/`, and the bot did not
  find it or refused to quote a PDF's content.
- **8** blocked on §3.2.

Ids 26, 28 and 33 share one pattern the colleagues named explicitly: the bot
pointed at a document ("das steht im PDF") instead of answering. Since the
converters already turn those PDFs into ordinary KB pages, there is no reason to
deflect — a good sign that the failure is in the prompt, not the corpus.

**Those five rows are the highest-value tests in the file.** They are where a
better chunking and retrieval design should visibly win.

---

## 4. What was verified

- **All 82 questions match the Excel** exactly (whitespace-normalised compare, 0
  mismatches), and 82 rows in, 82 lines out — nothing dropped or duplicated.
- **Every line is valid JSON**, no empty `question` or `reference_answer`, all 82
  questions distinct.
- **Prices and figures cross-checked** against their sources: Freibad 5,00 / 3,50
  / 2,00 / 8,90 / 13,80 €, Hallenbad 5,70 / 4,60 €, Mahngebühr 4,00 €, Zählercode
  2.8.0, Badeschluss 19:40, water hardness 9,1–13,7 °dH.
- **UTF-8 with real German spelling** (`ä ö ü ß`, `€`), written with
  `ensure_ascii=False` so the file stays readable in an editor.

---

## 5. If a new test round arrives

The colleagues keep testing, so the Excel will grow. To regenerate:

1. Replace `SW_Waiblingen_FAQ_Testing.xlsx`.
2. Re-read the new rows and write `reference_answer` / `keywords` / `source` for
   them, grounded in the **current** `outputs/clean/`.
3. Verify the questions against the Excel before committing — the row count and
   the verbatim text. Both were checked when this file was built and both
   passed; keep the check anyway, because a silently shifted column produces a
   file that still looks correct.

**Two traps when re-reading the Excel:**

- The table does not start at A1 and the columns are found by position
  (`Testsatz` is index 4, `Testergebnis` 6). Unlike `Excels/xlsx2md.py`, which
  locates its header by label, this was a one-off read. If a column is inserted,
  the positions shift silently. Locate the header row by its labels when
  automating this.
- Rows without a `Testsatz` must be skipped — the sheet has 168 rows but only 82
  questions; the rest are empty rows and a stray dropdown-validation column.

**And one trap in the corpus:** `outputs/` is never cleaned, so
`outputs/clean/` held 78 files while the site has ~62 pages + 14 static. Among
them was `Service_Abfall-ABC.md` — 64,778 characters of waste-disposal
information from **ahk-heidekreis.de**, left over from the `crawler-ahk` branch,
plus an older un-split copy of the WLAN terms. Neither was used for any answer
here, but both would silently pollute a vector database built by globbing that
folder. Delete `outputs/` and re-crawl before building an index.

### Known limitations

- **Reference answers are one person's reading of the sources.** They are
  grounded, but wording and level of detail are a judgement call; a colleague
  from the Kunden-Center should review the 14 `failure` rows in particular.
- **`keywords` are a weak metric.** They catch a missing fact, not a wrong or
  badly-worded one. Treat them as a smoke test and judge the answer separately.
- **`tester_result` is a baseline, not ground truth.** It records how the *old*
  bot did, and §3.1 shows four of those verdicts cannot be right.
- **The 5 `behaviour_test` rows have no objective answer.** Exclude them from any
  retrieval score.
- **The test set does not cover Geschäftskunden or Netze at all** — 17 and 19
  pages of the corpus with not a single question. The colleagues tested what
  customers ask on the phone; a retrieval benchmark would want coverage there too.

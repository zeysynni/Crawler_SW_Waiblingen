# How this crawler works — explained without technical jargon

**What it does:** this small program visits one page of the AHK Heidekreis
website — the *Abfall-ABC* (the A-to-Ö list that tells you where every kind
of waste belongs) — and turns it into a single, tidy document: one table per
letter, 798 entries in total, from *Abbeizmittel* to *Ölbindemittel*.

---

## The problem: the webpage only shows one letter at a time

Open the page in your browser and you'll see buttons for A, B, C … Ö. Click
"K" and you see the K entries; click "S" and the K entries disappear. At any
moment, only **one letter** of the list is visible on screen.

If you tried to copy the list by hand, you'd have to click through all 26
letters and copy 798 rows one by one — and repeat all of that whenever the
AHK updates the list.

A naive automated tool has the same problem as your eyes: if it just "looks
at" the page, it sees only the letter that happens to be displayed (letter A,
the default) and misses everything else.

## The trick: the whole list is in the page's luggage

Here's the key insight. When your browser asks the AHK website for that page,
the website doesn't send just the visible part — it sends the **complete
list of all 798 entries along with the page**, packed away like luggage in
the trunk of a car. The letter buttons don't fetch anything new from the
website; they only decide which part of that luggage gets **displayed**.

So instead of pressing 26 buttons and reading the screen 26 times, our
crawler simply opens the trunk: it takes the complete packed list directly
from the delivered page and unpacks all of it in one go.

That's why the result is **complete** (every letter, every entry), **fast**
(a few seconds), and **reliable** (the same input always produces the same
document — there's no guessing anywhere, and no artificial intelligence that
might make things up).

## What happens during a run, step by step

1. **Read the shopping list.** A small configuration file
   (`sites/ahk-heidekreis.yaml`) says which page(s) to visit. Only pages on
   this list are ever visited — nothing is explored "on its own".
2. **Fetch the page.** The program opens the page the same way a browser
   does. If the website doesn't answer, it tries once more; if it still
   fails, that's reported clearly.
3. **Unpack the list.** From the delivered page, the program takes the
   packed A–Ö data and turns it into tables: one section per letter, with
   three columns — *Abfallart* (what), *Wohin?* (where it goes), and
   *Hinweise* (tips, e.g. "ätzend" or "nicht in die Restmülltonne").
4. **Write two files.**
   - `outputs/raw/…` — the page as it was received (kept for checking),
   - `outputs/clean/Service_Abfall-ABC.md` — **the result**: the complete,
     tidy A–Ö document. This is the file you use.
5. **Report.** At the end, the program prints a short report: a ✓ for every
   page that worked, a ✗ with the reason for anything that failed, plus how
   long it took. (Optionally, this report can also be sent to a phone.)

## What if the AHK website changes some day?

Websites get redesigned. If the Abfall-ABC page ever changes so much that the
packed list isn't where the program expects it, the program does **not**
quietly produce a wrong or empty document — it stops and reports a clear
failure for that page. You'll know something needs attention; you'll never
unknowingly work with broken data.

## Running it yourself

A colleague with the project set up runs exactly one command:

```
uv run python main.py
```

A few seconds later, the finished document is at
`outputs/clean/Service_Abfall-ABC.md`. Running it again simply refreshes the
document with whatever is currently on the AHK website.

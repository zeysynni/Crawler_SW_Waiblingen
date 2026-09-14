# Git notes — is my local branch the same as its upstream?

Personal cheat sheet. Not tracked by git (listed in `.gitignore`).

## Words

- **Remote** — the copy of the project on GitHub. Here it is named `origin`
  (`https://github.com/zeysynni/Crawler_SW_Waiblingen.git`).
- **Upstream** — the branch on GitHub that a local branch follows. For local
  `crawler-crawl4ai` the upstream is `origin/crawler-crawl4ai`.
- **`origin/crawler-crawl4ai` is NOT GitHub itself.** It is a *snapshot* of
  GitHub stored on my computer, and it only updates when I run `git fetch`.
  Comparing without fetching first compares against old information.

## The short recipe

```bash
git fetch origin              # 1. refresh the snapshot (safe, changes nothing)
git status -sb                # 2. same or different? which side is ahead?
git diff --stat @{u}          # 3. which files differ
git diff @{u} -- path/to/file # 4. what exactly changed inside one file
```

## Step 1 — which branch am I on, what is its upstream?

```bash
git branch -vv
```

- The line with `*` is the current branch.
- The name in `[square brackets]` is the upstream.
- No square brackets = no upstream set yet → set it on first push:
  `git push -u origin <branch-name>`

## Step 2 — refresh

```bash
git fetch origin
```

Downloads **information only**. Does not touch my files, does not merge.
Safe to run any time.

`git pull` = `git fetch` **plus** merge. Do not use it just to check.

## Step 3 — read the answer

```bash
git status -sb
```

Read the first line, the one starting with `##`:

| Output | Meaning | Action |
| --- | --- | --- |
| `## br...origin/br` (nothing after) | identical | nothing |
| `## br...origin/br [ahead 2]` | I have 2 commits GitHub lacks | `git push` |
| `## br...origin/br [behind 3]` | GitHub has 3 commits I lack | `git pull` |
| `## br...origin/br [ahead 1, behind 2]` | **diverged** — both sides moved | stop, think; merge or rebase carefully |
| `## br` (no `...`) | no upstream set | `git push -u origin br` |

Lines *below* the `##` line are **uncommitted** changes (edited but not
committed). They are not part of the ahead/behind count — that counts
*commits*. Nothing below the `##` line = clean working folder.

## Step 4 — exact numbers

```bash
git rev-list --left-right --count HEAD...@{u}
```

Output is two numbers separated by a tab, e.g. `0	3`.
Left = commits only I have (ahead). Right = commits only GitHub has (behind).
`0	0` = identical.

## Step 5 — what is different

```bash
git diff --stat @{u}          # file names + how many lines changed
git diff --name-status @{u}   # file names + M modified / A added / D deleted
git diff @{u}                 # the actual changed lines
git log --oneline HEAD...@{u} # which commits differ
```

**Direction:** in `git diff @{u}` the upstream is the "before" and my local
branch is the "after". So `+` lines = what I have extra, `-` lines = what
GitHub has and I don't. To reverse it: `git diff HEAD @{u}`.

**`git diff` cannot tell me which side is newer.** The output looks the same
whether I am ahead or behind. Only step 3 states the direction — read it
before choosing between `push` and `pull`.

## Does a set upstream mean I can skip naming it?

Setting the upstream gives two things:

1. Commands that are *about* the remote use it automatically —
   `git status`, `git push`, `git pull`.
2. The shorthand `@{u}` now means `origin/<branch>`. Before an upstream is
   set, `@{u}` errors with *no upstream configured*.

It does **not** change what bare `git diff` means. Bare `git diff` has one
fixed job: working folder vs. last commit. Same for bare `git log` — my own
history, not a comparison.

**Rule of thumb: comparison commands need a target; status/push/pull don't.**

```bash
git status            # upstream used automatically
git push              # upstream used automatically
git diff @{u}         # must name the target
git log @{u}..HEAD    # must name the target
```

## Safety

- `git fetch`, `git status`, `git branch -vv`, `git diff`, `git log` never
  change my work — safe to repeat freely.
- If `git status -sb` says `[ahead N, behind M]` (diverged), stop and ask
  before pushing. That is the one case where a wrong command can lose commits.

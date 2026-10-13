# Kid-friendly UX plan: less reading, more doing

> Goal: a kid (6–15) opens beejoy, knows in one glance what to do, does it, and feels good.
> Today the kid pages read like the parent manual: almost every card carries an explanation sentence.
> Measured on a fresh Grade-3 account, the phone "Today" page was 3078px tall with ~880 characters
> and 12 cards before the kid had done anything.

## Principles

1. **One screen, one job.** Each kid page has one hero action. Everything else is secondary or folded away.
2. **Show, don't explain.** Replace sentences with icons, numbers, progress bars, colors and animation.
   If a rule needs explaining ("80% accuracy counts as a record"), the *parent* needs it, not the kid.
3. **Text budget per element.** Button ≤ 6 characters, label ≤ 10, hint ≤ 1 short line, and at most 1 hint per screen.
4. **Explain once, at the moment it matters.** First-time tips appear once (then stored as "seen"), never as permanent copy.
5. **Parents read, kids play.** The parent view of the same page (`readonly`) keeps full explanations;
   the kid view drops them. Same template, two densities.
6. **Celebrate with feedback, not words.** Stars flying into the counter, the progress bar filling,
   the mascot reacting — rather than toasts that describe what happened.
7. **Hear it instead of read it.** Younger kids (G1–G3) get 🔊 on question stems and the mascot's tips (TTS already exists).

## Done in this change (Today page, kid view only)

- Hero shows only the next task, its minutes, and one big button.
- Task list: titles + "⏱ N 分钟" only; the per-task "why" sentences are kept for the parent view.
- "这周学校学了啥？" prompt reduced to a question + one button + skip.
- "看看我的进步" (goal, week chart, map, records, 前方, findings) is folded into one collapsible row;
  it opens automatically on Mondays (weekly progress card) and is always open for parents.
- Removed explanatory footnotes (dashed-line legend, map explanation, accuracy rule) from the kid view.
- Result on a phone: ~3078px → ~1770px tall, ~880 → ~340 characters.

## How it works (for every kid page)

- `simple` (template variable) / `body[data-simple]`: true only when a school-age kid is looking at their own page
  (not a parent viewing as the kid, not an adult learner). Set in `app/web.py::render`.
- Put explanations for grown-ups in an element with class `for-grownups`: it stays in the HTML (parents,
  adult learners and tests still see it) but is hidden for kids. For a shorter kid line use
  `{% if simple %}short{% else %}long{% endif %}`.
- In JS use `isSimple()` (`app/static/app.js`) for answer feedback, toasts and pop-ups.
- Secondary sections fold into `<details class="quiet-box"><summary class="quiet-h">…</summary>`.
- `tests/test_today_kidtext.py` measures the text a kid actually sees on each page and fails when it goes over budget.
  If a page goes over, cut words before raising the budget.

## Done: all kid pages

Measured on a new Grade-3 account (characters a kid sees in the page body, phone width):

| Page | Before | After |
|---|---|---|
| Today | 877 | 330 |
| Learn a topic | 427 | 177 |
| Knowledge map | 729 | 278 |
| Subjects | 188 | 103 |
| Progress (school chapter) | 596 | 296 |
| Settings | 463 | 142 |
| Browser plug-in | 549 | 43 |
| Playground | 333 | 209 |
| Reading / books / records / papers / mocks / warm-up / review / placement | 1,868 | 1,157 |
| **Total (21 pages)** | **~6,030** | **~2,735** |

What changed:
- Intro paragraphs that explain rules are hidden or replaced by a one-line kid version with an icon.
- English subtitles, textbook edition names and knowledge-point counts are hidden on kid pages.
- Learn page: "why it's useful" and the prerequisites/next topics are folded; the result shows ⭐⭐☆ instead of a sentence.
- Answer feedback and pop-ups use one short line ("📒 记下了，过几天再练").
- Settings: device list, plug-in and AI status are folded under "⚙️ 更多"; the plug-in page tells kids to ask a parent.
- Records: the long tables are folded under "📚 更多记录".

## Next steps (proposed, in priority order)

| # | Page | Change |
|---|------|--------|
| 1 | Practice | Big answer buttons, auto-advance after a correct answer; hide the focus timer for G1–G2. |
| 3 | Subjects | Each subject = one big colorful tile with a ring progress. |
| 4 | Feedback | Replace "+1 ⭐ 完成一项！" toasts with a star animation into the header counter; mascot cheers / encourages. |
| 5 | Audio | 🔊 on question stems and mascot hints for G1–G3 (reuse `app/tts`). |
| 6 | First-run tips | One-time coach marks ("点这里开始") instead of permanent helper text. |

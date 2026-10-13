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

## Next steps (proposed, in priority order)

| # | Page | Change |
|---|------|--------|
| 1 | Practice / warmup / review | Drop the intro paragraph under the title; hide the focus timer + "ghost" race behind a small icon for G1–G4; big answer buttons, auto-advance after correct. |
| 2 | Global | A "kid density" switch in `base.html` (`data-tone="kid"` already exists) so templates can use one `kid_hint()` macro that renders nothing for kids. Audit all kid templates against the text budget. |
| 3 | Subjects | Each subject = one big colorful tile with a ring progress; move "导入试卷 / 模拟考 / 学校学到哪了" to the parent side. |
| 4 | Feedback | Replace "+1 ⭐ 完成一项！" toasts with a star animation into the header counter; mascot cheers / encourages. |
| 5 | Audio | 🔊 on question stems and mascot hints for G1–G3 (reuse `app/tts`). |
| 6 | First-run tips | One-time coach marks ("点这里开始") instead of permanent helper text. |
| 7 | Measure | Track per-page character count in a smoke test so the text does not grow back. |

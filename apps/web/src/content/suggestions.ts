// The cover's «Try» questions: three that Delfos answers well today, each a different kind of answer (a past
// episode, a what-if, a future scenario). Each wording was sent to the planner on 2026-11-15 and gave the plan
// it should (`event` of daily rain in October, `whatif`, `scenario` 1995-2054 after picking Alzira). Ask a new
// wording again before changing one: «en la DANA de octubre…» makes Delfos read «Dana» as a place, and without
// the month the episode becomes the whole year. Short enough for one line on a phone (about 340 px).
import type { Language } from "../i18n/messages";

export const SUGGESTIONS: Record<Language, string[]> = {
  en: [
    "Paiporta, October 2024 DANA: how much rain fell?",
    "How much water would a data centre in Sagunt drink?",
    "How much will Alzira warm by 2050?",
  ],
  es: [
    "Paiporta, DANA de octubre de 2024: ¿cuánto llovió?",
    "¿Cuánta agua bebería un centro de datos en Sagunt?",
    "¿Cuánto se calentará Alzira hacia 2050?",
  ],
};

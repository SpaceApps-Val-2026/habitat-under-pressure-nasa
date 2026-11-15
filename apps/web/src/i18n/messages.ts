// Two languages, no runtime dependency: the JSON files are the single source of copy.
import en from "../../../../locales/en.json";
import es from "../../../../locales/es.json";

export const messages = { en, es } as const;

export type Language = keyof typeof messages;

type Leaves<T, Prefix extends string = ""> = {
  [Key in keyof T & string]: T[Key] extends string ? `${Prefix}${Key}` : Leaves<T[Key], `${Prefix}${Key}.`>;
}[keyof T & string];

export type MessageKey = Leaves<typeof en>;

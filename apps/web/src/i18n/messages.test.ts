import { expect, test } from "vitest";

import { messages } from "./messages";

function entries(value: unknown, prefix = ""): [string, unknown][] {
  if (typeof value !== "object" || value === null) return [[prefix, value]];
  return Object.entries(value).flatMap(([key, child]) => entries(child, prefix ? `${prefix}.${key}` : key));
}

test("en and es have the same keys", () => {
  const en = entries(messages.en).map(([key]) => key).sort();
  const es = entries(messages.es).map(([key]) => key).sort();
  expect(es).toEqual(en);
});

test("every message is a non-empty string", () => {
  for (const language of Object.values(messages)) {
    for (const [key, value] of entries(language)) {
      expect(typeof value, key).toBe("string");
      expect((value as string).trim(), key).not.toBe("");
    }
  }
});

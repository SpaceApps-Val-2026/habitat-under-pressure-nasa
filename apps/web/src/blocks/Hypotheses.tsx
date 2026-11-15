import { localized, referenced, Section, type BlockProps } from "./shared";

export function Hypotheses({ block, results, language }: BlockProps) {
  const text = localized(block.text, language);
  if (!text || referenced(block, results).length === 0) return null;
  return (
    <Section title={language === "es" ? "Hipótesis" : "Hypotheses"}>
      {text.split("\n").map((paragraph) => <p key={paragraph} className="body">{paragraph}</p>)}
    </Section>
  );
}

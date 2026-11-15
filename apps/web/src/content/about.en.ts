// «What is this?» in English. Facts from AGENTS.md (Delfos), docs/product-requirements.md,
// docs/decisiones.md and docs/licencias-y-atribuciones.md. The source list, the evidence levels and the data
// classes are not written here: the page builds them from the catalogue and the interface's own words.
import type { AboutContent } from "./types";

export const aboutEn: AboutContent = {
  kicker: "What is this",
  title: "A question about a place, answered with Earth observation data",
  lead:
    "Habitat Under Pressure answers questions about what is changing in a place — the climate, the land people transform, the water we depend on — with data from satellites, reanalyses and official statistics. Every answer says where it comes from and how far it reaches.",
  sections: [
    {
      heading: "How to use it",
      paragraphs: [
        "Type a question about any place on Earth on the cover and press Enter. You do not choose variables, periods or methods: Delfos decides them. It only asks which place you mean when the question does not say.",
        "While it works you see which sources it consults and which steps it takes. When it finishes, the answer opens.",
      ],
    },
    {
      heading: "Delfos: a network of informants",
      paragraphs: [
        "The oracle of Delphi was not magic: it was a network of informants. The engine of this app is named after it for that reason. Its informants are the sources in the catalogue: satellites, reanalyses, official layers and documents.",
        "The language model is only the voice that writes the answer. It knows nothing by itself, computes nothing and adds no figures: every number comes from a source and a saved calculation.",
        "When its informants cannot reach, it says so: it gives the absence with its reason, or looks for another answer it can support, and explains what changed.",
      ],
    },
    {
      heading: "What you get",
      paragraphs: [
        "An article with the answer, the main figure, the evidence behind it and what it does not say. It reads in three formats — journalistic, institutional and scientific — and the data open in a panel with series, maps and sources.",
        "Every analysis is saved in the Library with its question, plan, data and method, so anyone can open it again and check it.",
      ],
    },
  ],
  evidenceHeading: "How much each figure is worth",
  evidenceIntro: "Every finding carries an evidence level. It says what could be checked, not how sure the sentence sounds:",
  classesHeading: "What kind of figure it is",
  classesIntro: "And every figure says what kind it is:",
  sourcesHeading: "Where the data come from",
  sourcesIntro:
    "These are the sources in the catalogue Delfos can consult today. Each keeps its own licence and citation, which the answer shows next to the figure.",
  closing: {
    heading: "What it does not do",
    paragraphs: [
      "It does not attribute causes: a change that matches a project in time and place does not prove the project caused it, and the answer says so.",
      "It does not see finer than its sources: when the cell of a dataset is larger than the area asked about, it warns. An episode of a few hours over a small area can be diluted in a monthly total.",
      "It is not a warning system or an official forecast. Future scenarios are models, with their range and their horizon.",
      "It is made by a team in València for the NASA Space Apps Challenge 2026. The code and original materials are open, under the Apache 2.0 licence; every data source keeps its own terms.",
    ],
  },
};

// DRAFT for Iban and Lara to rewrite. Written by Opus-flujo (Claude) on 15 November 2026 from the principles
// in AGENTS.md («El corazón del sistema»), docs/product-requirements.md and docs/decisiones.md. It is not an
// approved decision: change the words here; the page only lays them out.
import type { ManifestoContent } from "./types";

export const manifestoEn: ManifestoContent = {
  kicker: "Manifesto · draft",
  title: "What we stand for",
  lead:
    "Decisions about a place are often argued with partisan figures and no common baseline. This tool takes no side: it says what is happening, with which data and how certain it is.",
  principles: [
    {
      title: "One method for any place",
      text: "The same question is answered the same way anywhere on Earth. There are no special cases and no pages made for one place.",
    },
    {
      title: "The data speak; the model only gives them a voice",
      text: "The knowledge is in the sources. The language model writes, but it does not know, does not compute and adds no figures.",
    },
    {
      title: "Every figure with its evidence and its limits",
      text: "Every figure says which source it comes from, what kind of figure it is, what evidence level it has and what it does not allow us to claim.",
    },
    {
      title: "Absence is part of the answer",
      text: "When there are no data, it says why. A gap is never filled in, and the answer never sounds surer than it is.",
    },
    {
      title: "Coinciding is not causing",
      text: "A change that matches a project in time and place is described as a coincidence, not as a cause.",
    },
    {
      title: "Open data, with attribution",
      text: "We use public data and credit those who produce them. Our own code and materials are open.",
    },
    {
      title: "Anyone can check it",
      text: "Every analysis keeps its question, plan, data and method, so someone else can review it or run it again.",
    },
  ],
  closing: "If a decision makes the system look smarter without more data behind it, it is the wrong decision.",
};

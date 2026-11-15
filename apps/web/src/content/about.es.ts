// «¿Qué es esto?» en castellano. Los hechos salen de AGENTS.md (Delfos), docs/product-requirements.md,
// docs/decisiones.md y docs/licencias-y-atribuciones.md. La lista de fuentes, los niveles de evidencia y las
// clases de dato no se escriben aquí: la página los genera del catálogo y de los textos de la interfaz.
import type { AboutContent } from "./types";

export const aboutEs: AboutContent = {
  kicker: "Qué es esto",
  title: "Una pregunta sobre un lugar, respondida con datos de observación de la Tierra",
  lead:
    "Hábitat bajo presión responde preguntas sobre lo que cambia en un lugar —el clima, la tierra que transformamos, el agua de la que dependemos— con datos de satélites, reanálisis y estadísticas oficiales. Cada respuesta dice de dónde sale y hasta dónde llega.",
  sections: [
    {
      heading: "Cómo se usa",
      paragraphs: [
        "Escribe una pregunta en la portada sobre cualquier lugar de la Tierra y pulsa Intro. No hace falta elegir variables, periodos ni métodos: eso lo decide Delfos. Solo te preguntará de qué lugar hablas si no se deduce de la pregunta.",
        "Mientras trabaja ves qué fuentes consulta y qué pasos da. Al terminar se abre la respuesta.",
      ],
    },
    {
      heading: "Delfos: una red de informantes",
      paragraphs: [
        "El oráculo de Delfos no era magia: era una red de informantes. El motor de esta aplicación se llama así por eso. Sus informantes son las fuentes del catálogo: satélites, reanálisis, capas oficiales y documentos.",
        "El modelo de lenguaje es solo la voz que redacta la respuesta. No sabe nada por sí mismo, no calcula y no aporta cifras: cada número sale de una fuente y de un cálculo guardado.",
        "Cuando sus informantes no llegan, lo dice: da la ausencia con su motivo o busca otra respuesta que sí pueda sostener, y explica qué ha cambiado.",
      ],
    },
    {
      heading: "Qué recibes",
      paragraphs: [
        "Un artículo con la respuesta, la cifra principal, la evidencia que la sostiene y lo que no dice. Se puede leer en tres formatos —periodístico, institucional y científico— y abrir los datos en un panel con series, mapas y fuentes.",
        "Cada análisis queda guardado en la Biblioteca con su pregunta, su plan, sus datos y su método, para que cualquiera lo vuelva a abrir y lo revise.",
      ],
    },
  ],
  evidenceHeading: "Cuánto vale cada cifra",
  evidenceIntro:
    "Cada hallazgo lleva un nivel de evidencia. Dice qué se ha podido comprobar, no lo seguro que suena la frase:",
  classesHeading: "Qué clase de dato es",
  classesIntro: "Y cada cifra dice de qué tipo es:",
  sourcesHeading: "De dónde salen los datos",
  sourcesIntro:
    "Estas son las fuentes del catálogo que Delfos puede consultar hoy. Cada una conserva su licencia y su cita, que la respuesta enseña junto a la cifra.",
  closing: {
    heading: "Lo que no hace",
    paragraphs: [
      "No atribuye causas: que un cambio coincida en tiempo y lugar con una obra no prueba que la obra lo haya causado, y la respuesta lo dice así.",
      "No ve más fino que sus fuentes: si la celda de un dato es más grande que la zona preguntada, lo avisa. Un episodio de horas y muy local puede quedar diluido en un total mensual.",
      "No es un sistema de alerta ni una predicción oficial. Los escenarios futuros son modelos, con su rango y su horizonte.",
      "Lo hace un equipo de València para el NASA Space Apps Challenge 2026. El código y los materiales propios son abiertos, con licencia Apache 2.0; cada fuente de datos conserva sus condiciones.",
    ],
  },
};

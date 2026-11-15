// BORRADOR para que Iban y Lara lo reescriban. Redactado por Opus-flujo (Claude) el 15 de noviembre de 2026
// a partir de los principios de AGENTS.md («El corazón del sistema»), docs/product-requirements.md y
// docs/decisiones.md. No es una decisión aprobada: cambiad el texto aquí; la página solo lo maqueta.
import type { ManifestoContent } from "./types";

export const manifestoEs: ManifestoContent = {
  kicker: "Manifiesto · borrador",
  title: "Lo que sostenemos",
  lead:
    "Las decisiones sobre un lugar se discuten a menudo con cifras de parte y sin una base común. Esta herramienta no toma partido: dice qué pasa, con qué datos y con cuánta certeza.",
  principles: [
    {
      title: "Un método para cualquier lugar",
      text: "La misma pregunta se responde igual en cualquier sitio de la Tierra. No hay casos especiales ni páginas hechas a medida para un lugar.",
    },
    {
      title: "Hablan los datos; el modelo solo pone la voz",
      text: "El conocimiento está en las fuentes. El modelo de lenguaje redacta, pero no sabe, no calcula y no aporta cifras.",
    },
    {
      title: "Cada cifra con su evidencia y sus límites",
      text: "Toda cifra dice de qué fuente sale, qué clase de dato es, qué nivel de evidencia tiene y lo que no permite afirmar.",
    },
    {
      title: "La ausencia es parte de la respuesta",
      text: "Cuando no hay datos, se dice por qué. Nunca se rellena un hueco ni se suena más seguro de lo que se está.",
    },
    {
      title: "Coincidir no es causar",
      text: "Un cambio que coincide con una obra en el tiempo y en el lugar se describe como coincidencia, no como causa.",
    },
    {
      title: "Datos abiertos, con su atribución",
      text: "Usamos datos públicos y citamos a quien los produce. El código y los materiales propios son abiertos.",
    },
    {
      title: "Cualquiera puede comprobarlo",
      text: "Cada análisis guarda su pregunta, su plan, sus datos y su método, para que otra persona lo revise o lo repita.",
    },
  ],
  closing: "Si una decisión hace que el sistema parezca más listo sin tener más datos detrás, es la decisión equivocada.",
};

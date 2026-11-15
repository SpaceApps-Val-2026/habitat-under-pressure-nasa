// Generates TypeScript types from the contract schemas. The schemas are the source of truth;
// the files in src/contracts/ are never edited. Run: npm run gen:types
import { mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { compileFromFile } from "json-schema-to-typescript";

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const contracts = resolve(web, "../../docs/contratos");
const target = resolve(web, "src/contracts");
const banner = "/* Generated from docs/contratos by `npm run gen:types`. Do not edit. */";

mkdirSync(target, { recursive: true });
const names = [];
for (const file of readdirSync(contracts).filter((name) => name.endsWith(".schema.json")).sort()) {
  const name = file.replace(".schema.json", "");
  if (name === "common") continue; // only shared definitions; each contract inlines what it uses
  const code = await compileFromFile(resolve(contracts, file), {
    cwd: contracts,
    bannerComment: banner,
    additionalProperties: false,
    style: { printWidth: 120 },
  });
  writeFileSync(resolve(target, `${name}.d.ts`), code.replace(/\r\n/g, "\n"), "utf8");
  names.push(name);
  console.log(`generated ${name}.d.ts`);
}
writeFileSync(
  resolve(target, "index.ts"),
  `${banner}\n${names.map((name) => `export type * as ${name} from "./${name}";`).join("\n")}\n`,
  "utf8",
);

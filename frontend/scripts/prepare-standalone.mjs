import { cpSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

// Next's standalone server does not copy these runtime assets itself. Without
// them it serves the HTML but every client chunk is a 404, leaving client pages
// permanently unhydrated (a blank screen in the browser).
const buildDir = ".next";
const standaloneDir = join(buildDir, "standalone");

for (const [source, destination] of [
  [join(buildDir, "static"), join(standaloneDir, ".next", "static")],
  ["public", join(standaloneDir, "public")],
]) {
  if (!existsSync(source)) continue;
  mkdirSync(destination, { recursive: true });
  cpSync(source, destination, { recursive: true, force: true });
}

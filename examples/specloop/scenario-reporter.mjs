// Trusted node:test reporter for specloop coverage: prints `SCENARIO <id>` for
// every passing test whose name contains an OpenSpec scenario ID
// (capability/requirement/scenario), and one line per failure.
const ID = /\b[a-z0-9]+(?:-[a-z0-9]+)*\/[a-z0-9]+(?:-[a-z0-9]+)*\/[a-z0-9]+(?:-[a-z0-9]+)*\b/g;

export default async function* reporter(source) {
  for await (const event of source) {
    if (event.type === "test:pass" && !event.data.skip && !event.data.todo) {
      for (const id of event.data.name.match(ID) ?? []) yield `SCENARIO ${id}\n`;
    } else if (event.type === "test:fail") {
      const message = String(event.data.details?.error?.message ?? "").split("\n")[0].slice(0, 300);
      yield `FAIL ${event.data.name}: ${message}\n`;
    }
  }
}

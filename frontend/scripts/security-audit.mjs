import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const ADVISORY = "https://github.com/advisories/GHSA-fc5q-39xc-vjpf";

export function evaluateAudit(report, lock, exception, now = Date.now()) {
  if (report?.auditReportVersion !== 2 || report.error || !report.vulnerabilities
      || typeof report.vulnerabilities !== "object" || Array.isArray(report.vulnerabilities)
      || Object.values(report.vulnerabilities).some(v => !v || typeof v !== "object"
        || !["info", "low", "moderate", "high", "critical"].includes(v.severity))) {
    throw new Error("Invalid audit report; refusing to accept an audit-tool failure.");
  }
  const high = Object.entries(report.vulnerabilities).filter(([, v]) => ["high", "critical"].includes(v.severity));
  const counts = report.metadata?.vulnerabilities;
  if (counts && (!Number.isInteger(counts.high) || !Number.isInteger(counts.critical)
      || counts.high + counts.critical !== high.length)) {
    throw new Error("Invalid audit finding counts.");
  }
  if (!high.length) return { accepted: [], findings: 0 };
  const created = Date.parse(exception.created_utc), expires = Date.parse(exception.expires_utc);
  if (exception.advisory_url !== ADVISORY || !Number.isFinite(created) || !Number.isFinite(expires)
      || !Number.isFinite(now) || now < created || now >= expires || expires <= created || expires - created > 7 * 86400000) {
    throw new Error("Security exception is invalid, not yet valid, or expired; review upstream remediation.");
  }
  const expected = ["fdir", "picomatch", "tinyglobby", "vite"];
  if (JSON.stringify(Object.keys(exception.versions || {}).sort()) !== JSON.stringify(expected)) {
    throw new Error("Unexpected exception dependency scope.");
  }
  function check(name, path = new Set()) {
    if (path.has(name)) throw new Error("Cyclic audit dependency graph.");
    const v = report.vulnerabilities[name];
    const node = "node_modules/" + name;
    const installed = lock.packages?.[node];
    if (!expected.includes(name) || !v || v.name !== name || v.severity !== "high"
        || JSON.stringify(v.nodes) !== JSON.stringify([node]) || !installed || installed.dev !== true
        || installed.version !== exception.versions[name] || !Array.isArray(v.via) || !v.via.length) {
      throw new Error("Unaccepted advisory, version, dependency path or production exposure: " + name);
    }
    const next = new Set(path); next.add(name);
    for (const cause of v.via) {
      if (typeof cause === "string") check(cause, next);
      else if (!cause || name !== "picomatch" || cause.name !== "picomatch"
          || cause.url !== ADVISORY || cause.severity !== "high") {
        throw new Error("New or changed direct security advisory: " + name);
      }
    }
  }
  for (const [name] of high) check(name);
  return { accepted: high.map(([name]) => name), findings: high.length };
}

function main() {
  if (!process.env.npm_execpath) throw new Error("Run through npm run audit:security.");
  const result = spawnSync(process.execPath, [process.env.npm_execpath, "audit", "--json"], {
    encoding: "utf8", timeout: 60000, maxBuffer: 4 * 1024 * 1024,
  });
  // Keep npm's complete, unmodified report visible as a local/CI artifact.
  if (result.stdout) writeFileSync(new URL("../audit-report.json", import.meta.url), result.stdout);
  if (result.error || ![0, 1].includes(result.status)) throw new Error("npm audit failed to run successfully.");
  const report = JSON.parse(result.stdout);
  const lock = JSON.parse(readFileSync(new URL("../package-lock.json", import.meta.url)));
  const exception = JSON.parse(readFileSync(new URL("../security-exception.json", import.meta.url)));
  const outcome = evaluateAudit(report, lock, exception);
  if (outcome.accepted.length) {
    console.log(`TEMPORARY BUILD-ONLY EXCEPTION: ${ADVISORY}`);
    console.log(`Accepted findings: ${outcome.accepted.join(", ")}. Expires ${exception.expires_utc}.`);
    console.log("Raw npm findings remain in audit-report.json. This is risk acceptance, not a vulnerability fix.");
  } else console.log("No high/critical findings require an exception.");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
